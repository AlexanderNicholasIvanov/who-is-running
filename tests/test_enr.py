from __future__ import annotations

import gzip
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from conftest import FIXTURES, FakeWeb, fixture_bytes

from who_is_running.build_site import build_results
from who_is_running.districts import COUNTY_NAMES_2010
from who_is_running.pipeline import run
from who_is_running.results import CountyResult, results_path, write_csv
from who_is_running.sources import enr
from who_is_running.sources.enr import (
    EnrSource,
    Feed,
    county_key,
    find_election,
    office_of,
    parse_detail,
    updated_at,
)
from who_is_running.text import decode

CENSUS = fixture_bytes("enr_census_counties.txt")
FIPS = {
    ("IA", "ADAIR"): "19001",
    ("IA", "OBRIEN"): "19141",
    ("IA", "POLK"): "19153",
    ("CO", "DENVER"): "08031",
    ("CO", "ELPASO"): "08041",
    ("SC", "ABBEVILLE"): "45001",
    ("SC", "CHARLESTON"): "45019",
}
# Real elections.json entries (Iowa's site, October 2026).
IA_ELECTIONS = [
    {"State": "IA", "EID": "127077", "ElectionName": "2026 General Election",
     "Date": "11/3/2026 12:00:00 AM"},
    {"State": "IA", "EID": "126082", "ElectionName": "2026 Primary Election",
     "Date": "6/2/2026 12:00:00 AM"},
    {"State": "IA", "EID": "122322", "ElectionName": "2024 General Election",
     "Date": "11/5/2024 12:00:00 AM"},
]  # fmt: skip


def _parse(name: str, state: str, year: int) -> tuple[list[CountyResult], dict[str, object]]:
    return parse_detail(decode(fixture_bytes(name)), state, year, FIPS)


def _votes(rows: list[CountyResult], office: str) -> dict[tuple[str, str], int]:
    return {(r.county_fips, r.party): r.votes for r in rows if r.office == office}


def _zip(xml: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("detail.xml", xml)
    return buf.getvalue()


def test_county_totals_match_certified_results() -> None:
    # Iowa 2024, final: the same county totals MEDSL has from the certified canvass.
    rows, status = _parse("enr_ia_2024.xml", "IA", 2024)
    votes = _votes(rows, "US President")
    assert votes["19141", "REP"] == 5998  # O'Brien
    assert votes["19141", "DEM"] == 1428
    assert votes["19153", "DEM"] == 140075  # Polk
    assert votes["19153", "REP"] == 112240
    assert {r.county for r in rows} == {"ADAIR", "O'BRIEN", "POLK"}
    assert {r.candidate_name for r in rows} >= {"Donald J. Trump and JD Vance"}
    assert status["precincts_reported"] == status["precincts_total"] == 5 + 9 + 176
    assert status["counties"] == {"19001": [5, 5], "19141": [9, 9], "19153": [176, 176]}
    assert status["updated"] == "2024-12-16T08:19:40-06:00"


def test_only_statewide_candidate_races_are_kept() -> None:
    rows, _ = _parse("enr_ia_2024.xml", "IA", 2024)
    # Dropped: a House district (not every county), a retention vote that isn't
    # marked as a question, a judge retention marked as one, and write-ins.
    assert {r.office for r in rows} == {"US President"}
    assert "Write-in" not in {r.candidate_name for r in rows}

    rows, _ = _parse("enr_co_2022.xml", "CO", 2022)
    # Colorado's propositions aren't marked as questions either.
    assert {r.office for r in rows} == {"US Senate", "GOVERNOR"}

    rows, _ = _parse("enr_sc_2024.xml", "SC", 2024)
    assert {r.office for r in rows} == {"US President"}  # not straight-party or amendments


def test_vote_types_add_up_or_use_the_total() -> None:
    # South Carolina splits votes into five types and prefixes names with the party.
    rows, _ = _parse("enr_sc_2024.xml", "SC", 2024)
    votes = _votes(rows, "US President")
    assert votes["45019", "REP"] == 99265  # Charleston
    assert votes["45019", "DEM"] == 111427
    assert votes["45001", "REP"] == 8509  # Abbeville
    assert "Donald J Trump/JD Vance" in {r.candidate_name for r in rows}

    # Colorado gives only a "Total Votes" type.
    rows, _ = _parse("enr_co_2022.xml", "CO", 2022)
    assert _votes(rows, "US Senate")["08031", "DEM"] == 228419  # Denver
    assert _votes(rows, "US Senate")["08031", "REP"] == 51582
    assert _votes(rows, "GOVERNOR")["08041", "REP"] == 144384  # El Paso
    assert _votes(rows, "GOVERNOR")["08041", "DEM"] == 133447


def test_office_names_match_the_national_map() -> None:
    assert office_of("United States Senator") == "US Senate"
    assert office_of("U.S. Senate - Unexpired Term") == "US Senate (special)"
    assert office_of("Presidential Electors") == "US President"
    assert office_of("President and Vice President") == "US President"
    assert office_of("Governor and Lt. Governor") == "GOVERNOR"
    assert office_of("Governor/Lieutenant Governor") == "GOVERNOR"
    assert office_of("Secretary of State") == "SECRETARY OF STATE"
    assert office_of("Lieutenant Governor") == "LIEUTENANT GOVERNOR"


def test_helpers() -> None:
    assert county_key("O'Brien") == county_key("O'Brien County") == "OBRIEN"
    assert county_key("St. Mary's") == county_key("Saint Marys") == "STMARYS"
    assert updated_at("11/3/2026 9:04:57 PM CST") == "2026-11-03T21:04:57-06:00"
    assert updated_at("garbage") == ""
    assert find_election(IA_ELECTIONS, date(2026, 11, 3)) == "127077"
    assert find_election(IA_ELECTIONS, date(2022, 11, 8)) == ""


def test_dtd_is_refused() -> None:
    with pytest.raises(ValueError, match="DTD"):
        parse_detail('<!DOCTYPE x [<!ENTITY a "b">]><ElectionResult/>', "IA", 2026, FIPS)


def _web(xml: bytes, elections: list[dict[str, str]] = IA_ELECTIONS) -> FakeWeb:
    return FakeWeb(
        {
            COUNTY_NAMES_2010: CENSUS,
            # Clarity gzips elections.json even when not asked to.
            "/IA/elections.json": gzip.compress(json.dumps(elections).encode()),
            "/IA/127077/current_ver.txt": b"382865",
            "/IA/127077/382865/reports/detailxml.zip": _zip(xml),
        }
    )


def test_source_finds_the_election_and_writes_results_with_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The 2024 counts served as if they were 2026's, so there are votes to write.
    monkeypatch.setattr(enr, "FEEDS", {2026: {"IA": Feed(f"{enr.CLARITY}/IA")}})
    web = _web(fixture_bytes("enr_ia_2024.xml"))

    assert run(EnrSource(web), [2026], tmp_path) == {}  # results only, no candidates

    assert not (tmp_path / "enr").exists()
    csv_text = (tmp_path / "results" / "enr" / "2026.csv").read_text(encoding="utf-8")
    assert (
        "IA,2026,US President,19153,POLK,Kamala D. Harris and Tim Walz,DEM,140075,enr" in csv_text
    )
    status = json.loads((tmp_path / "results" / "enr" / "2026.json").read_text(encoding="utf-8"))
    assert status["IA"]["url"] == "https://results.enr.clarityelections.com/IA/127077/"
    assert status["IA"]["precincts_total"] == 190


def test_no_votes_yet_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Iowa's real 2026 file a few days before the election: every count is zero.
    monkeypatch.setattr(enr, "FEEDS", {2026: {"IA": Feed(f"{enr.CLARITY}/IA", "127077")}})
    source = EnrSource(_web(fixture_bytes("enr_ia_2026.xml")))

    assert source.results(2026) == []
    rows, status = _parse("enr_ia_2026.xml", "IA", 2026)
    assert {r.office for r in rows} == {"US Senate", "GOVERNOR"}
    assert status["precincts_reported"] == 0
    assert status["updated"] == "2026-10-30T15:04:57-05:00"


def test_unlisted_election_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enr, "FEEDS", {2026: {"IA": Feed(f"{enr.CLARITY}/IA")}})
    web = _web(fixture_bytes("enr_ia_2024.xml"), elections=IA_ELECTIONS[1:])

    assert EnrSource(web).results(2026) == []
    assert not any("current_ver" in url for url in web.requested)


def test_missing_report_mid_update_keeps_the_previous_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(enr, "FEEDS", {2026: {"IA": Feed(f"{enr.CLARITY}/IA", "127077")}})
    web = _web(b"")
    del web.routes["/IA/127077/382865/reports/detailxml.zip"]

    with pytest.raises(Exception, match="detailxml"):
        EnrSource(web).results(2026)


def test_refresh_years_cover_the_canvass() -> None:
    source = EnrSource(FakeWeb({}))
    assert source.refresh_years(date(2026, 10, 31)) == []
    assert source.refresh_years(date(2026, 11, 4)) == []  # the live workflow's days
    assert source.refresh_years(date(2026, 11, 7)) == [2026]
    assert source.refresh_years(date(2027, 1, 5)) == []


def _live(state: str, year: int, county: str, name: str, votes: int) -> CountyResult:
    return CountyResult(state, year, "US President", county, "X", name, "DEM", votes, "enr")


def test_certified_results_take_precedence_over_live_ones(tmp_path: Path) -> None:
    data = tmp_path / "results"
    (data / "medsl").mkdir(parents=True)
    medsl = FIXTURES / "site_data" / "results" / "medsl" / "2024.csv"
    (data / "medsl" / "2024.csv").write_bytes(medsl.read_bytes())
    status = {
        st: {"updated": "2024-11-06T01:00:00-05:00", "precincts_reported": 1,
             "precincts_total": 2, "url": "u", "counties": {"x": [1, 2]}}
        for st in ("DE", "IA")
    }  # fmt: skip
    for year in (2024, 2026):
        path = results_path(tmp_path, "enr", year)
        write_csv(
            path,
            [_live("DE", year, "10005", "LIVE DE", 5), _live("IA", year, "19153", "LIVE IA", 7)],
        )
        path.with_suffix(".json").write_text(json.dumps(status), encoding="utf-8")

    built = build_results(data, tmp_path / "out")

    by_year = {b["year"]: b for b in built}
    assert sorted(by_year) == [2024, 2026]
    # 2024: Delaware stays certified; live results only add Iowa, which MEDSL's fixture lacks.
    assert by_year[2024]["source"] == "medsl"
    assert by_year[2024]["states"] == ["AK", "DE", "IA", "SD"]
    assert list(by_year[2024]["live"]) == ["IA"]
    assert "counties" not in by_year[2024]["live"]["IA"]  # kept out of the manifest
    out = tmp_path / "out"
    de = json.loads((out / "2024" / "DE.json").read_text(encoding="utf-8"))
    assert "live" not in de
    assert "LIVE DE" not in json.dumps(de)
    ia = json.loads((out / "2024" / "IA.json").read_text(encoding="utf-8"))
    assert ia["live"]["counties"] == {"x": [1, 2]}
    national = json.loads((out / "2024" / "national.json").read_text(encoding="utf-8"))
    assert set(national["races"]["US President"]) == {"AK", "DE", "IA", "SD"}
    assert (out / "2024.csv").exists() and (out / "2024-enr.csv").exists()
    # 2026: nothing certified yet, so every live state shows.
    assert by_year[2026]["source"] == "enr"
    assert list(by_year[2026]["live"]) == ["DE", "IA"]
    assert (out / "2026.csv").exists()
