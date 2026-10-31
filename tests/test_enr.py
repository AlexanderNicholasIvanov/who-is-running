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
from who_is_running.districts import COUNTY_NAMES_2010, parse_county_names
from who_is_running.models import STATEWIDE, Candidacy, Level
from who_is_running.pipeline import run
from who_is_running.results import CountyResult, results_path, write_csv
from who_is_running.sources import enr
from who_is_running.sources.enr import (
    EnrSource,
    Feed,
    county_key,
    detail_contests,
    find_election,
    florida_lines,
    line_contests,
    minnesota_lines,
    office_and_district,
    office_of,
    parse_detail,
    parse_florida,
    parse_minnesota,
    to_candidacies,
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
    assert office_of("U.S.  Senate") == "US Senate"  # South Carolina 2026
    assert office_of("U.S. SENATOR") == "US Senate"  # West Virginia 2026
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

    assert run(EnrSource(web), [2026], tmp_path) == {}  # federal races only: no candidates

    assert not (tmp_path / "enr").exists()
    csv_text = (tmp_path / "results" / "enr" / "2026.csv").read_text(encoding="utf-8")
    assert (
        "IA,2026,2026-11-03,general,US President,19153,POLK,"
        "Kamala D. Harris and Tim Walz,DEM,140075,enr" in csv_text
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


def test_refresh_years_cover_the_candidates_and_the_canvass() -> None:
    source = EnrSource(FakeWeb({}))
    assert source.refresh_years(date(2026, 10, 31)) == [2026]  # candidates, no votes yet
    assert source.refresh_years(date(2026, 11, 3)) == []  # the live workflow's days
    assert source.refresh_years(date(2026, 11, 4)) == []
    assert source.refresh_years(date(2026, 11, 7)) == [2026]
    assert source.refresh_years(date(2027, 1, 5)) == []


def _live(state: str, year: int, county: str, name: str, votes: int) -> CountyResult:
    return CountyResult(
        state,
        year,
        f"{year}-11-05",
        "general",
        "US President",
        county,
        "X",
        name,
        "DEM",
        votes,
        "enr",
    )


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


def _census() -> dict[str, tuple[str, str]]:
    return parse_county_names(decode(CENSUS))


def test_florida_file() -> None:
    fips = {(st, county_key(n)): f for f, (st, n) in _census().items()}
    rows, status = parse_florida(decode(fixture_bytes("enr_fl_2024.txt")), "FL", 2024, fips)
    # Dropped: a House district, an amendment and a retention vote (Yes/No).
    assert {r.office for r in rows} == {"US President", "US Senate"}
    president = _votes(rows, "US President")
    # Florida's file as finally posted, against MEDSL's certified county totals.
    assert president["12001", "DEM"] == 81578  # Alachua
    assert president["12001", "REP"] == 52939
    assert president["12086", "REP"] == 605590  # Miami-Dade
    assert president["12027", "DEM"] == 3525  # "Desoto" in Florida's file
    assert {r.county for r in rows} == {"ALACHUA", "DESOTO", "MIAMI-DADE"}
    assert "Trump / Vance" in {r.candidate_name for r in rows}
    assert "Rick Scott" in {r.candidate_name for r in rows}
    assert status["updated"] == ""  # the file doesn't say
    assert status["counties"]["12001"] == [63, 63]


def test_minnesota_file() -> None:
    rows, status = parse_minnesota(decode(fixture_bytes("enr_mn_2024.txt")), "MN", 2024, _census())
    senate = _votes(rows, "US Senate")
    # County 69 is St. Louis, Census 27137; DFL is Minnesota's Democratic party.
    assert senate["27137", "DFL"] == 72294
    assert senate["27137", "REP"] == 41273
    assert senate["27123", "DFL"] == 198504  # Ramsey
    assert senate["27001", "REP"] == 5857  # Aitkin
    assert {r.county for r in rows} == {"AITKIN", "RAMSEY", "ST. LOUIS"}
    judges = {r.party for r in rows if r.office == "CHIEF JUSTICE - SUPREME COURT"}
    assert judges == {""}  # "NP": nonpartisan
    assert "WRITE-IN" not in {r.candidate_name for r in rows}
    assert status["counties"]["27001"] == [51, 51]

    # 2026: every county elects a sheriff under the same office ID; not statewide.
    rows, _ = parse_minnesota(decode(fixture_bytes("enr_mn_2026.txt")), "MN", 2026, _census())
    assert {r.office for r in rows} == {"GOVERNOR"}
    assert all(r.votes == 0 for r in rows)


def test_source_reads_a_state_file(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://electionresultsfiles.sos.mn.gov/20261103/allracesbycounty.txt"
    monkeypatch.setattr(enr, "FEEDS", {2026: {"MN": Feed(url, kind="minnesota")}})
    source = EnrSource(FakeWeb({COUNTY_NAMES_2010: CENSUS, url: fixture_bytes("enr_mn_2024.txt")}))

    rows = source.results(2026)

    assert {r.state for r in rows} == {"MN"}
    assert source.results_status(2026)["MN"]["url"] == url
    # Not posted yet: skipped, not an error.
    assert EnrSource(FakeWeb({COUNTY_NAMES_2010: CENSUS})).results(2026) == []


# Candidate lists: the 2026 files a few days before the election, trimmed to a
# few contests each.


def _candidacies(state: str) -> list[Candidacy]:
    if state in ("CO", "SC"):
        contests, every = detail_contests(decode(fixture_bytes(f"enr_{state.lower()}_2026.xml")))
    elif state == "FL":
        fips = {(st, county_key(n)): f for f, (st, n) in _census().items()}
        text = decode(fixture_bytes("enr_fl_2026.txt"))
        contests, every = line_contests(florida_lines(text, "FL", fips))
    else:
        text = decode(fixture_bytes("enr_mn_2026.txt"))
        contests, every = line_contests(minnesota_lines(text, "MN", _census()))
    return to_candidacies(contests, every, state, 2026)


def _by_name(rows: list[Candidacy]) -> dict[str, Candidacy]:
    return {r.candidate_name: r for r in rows}


def test_clarity_contests_become_candidacies() -> None:
    rows = _candidacies("CO")
    # Dropped: the US Senate race (the fec source has it), a retention vote and an
    # amendment (Yes/No choices).
    assert {(r.office, r.district) for r in rows} == {
        ("Governor / Lieutenant Governor", ""),
        ("State Senator", "8"),
        ("State Representative", "4"),
        ("Regional Transportation District Director", "O"),
        ("Town of Green Mountain Falls Mayor", ""),
    }
    by_name = _by_name(rows)
    assert by_name["Phil Weiser / Lesley Dahlkemper"] == Candidacy(
        state="CO",
        election_year=2026,
        election_date="2026-11-03",
        level=Level.STATE,
        office="Governor / Lieutenant Governor",
        district="",
        jurisdiction="",
        counties=STATEWIDE,
        candidate_name="Phil Weiser / Lesley Dahlkemper",
        party="DEM",
        election_type="general",
        incumbent_status="",
        candidate_status="",
        source="enr",
        source_candidate_id="",
    )
    senator = by_name["Dylan Roberts"]
    assert senator.level == Level.STATE
    assert senator.counties.startswith("CLEAR CREEK;EAGLE;GARFIELD;")
    assert by_name["Bill Hare"].counties == "DENVER" and by_name["Bill Hare"].party == "FWD"
    # A local race in one county names it; its "party" is a ballot-order number.
    rtd = by_name["Jack Rosenthal"]
    assert (rtd.level, rtd.jurisdiction, rtd.party) == (Level.LOCAL, "BOULDER", "")
    mayor = by_name["Todd Dixon"]
    assert (mayor.level, mayor.jurisdiction, mayor.counties) == (Level.LOCAL, "", "EL PASO;TELLER")


def test_south_carolina_names_and_parties() -> None:
    rows = _candidacies("SC")
    by_name = _by_name(rows)
    # "REP Mark Hammond": the party prefix comes off.
    assert by_name["Mark Hammond"].party == "REP"
    assert by_name["Mark Hammond"].counties == STATEWIDE
    house = by_name["Bill Whitmire"]
    assert (house.office, house.district, house.counties) == (
        "State House of Representatives",
        "1",
        "OCONEE;PICKENS",
    )
    assert by_name["Joy Hansen"].party == ""  # "NON": nonpartisan
    assert by_name["Joy Hansen"].level == Level.LOCAL
    # Not the US House, the straight-party line, a referendum or write-ins.
    assert {r.office for r in rows} == {
        "Secretary of State",
        "State House of Representatives",
        "Town Council - Town of Ware Shoals",
    }
    assert "Write-In" not in by_name


def test_florida_candidacies() -> None:
    rows = _candidacies("FL")
    by_name = _by_name(rows)
    # Florida's file gives tickets by surname only.
    governor = by_name["Donalds / Avila"]
    assert (governor.party, governor.counties) == ("REP", STATEWIDE)
    # "WRI": a qualified write-in candidate, not a party.
    write_in = by_name["Anderson / Nokovich"]
    assert (write_in.party, write_in.candidate_status) == ("", "write-in")
    rep = by_name["Michelle Salzman"]
    assert (rep.office, rep.district, rep.counties) == ("State Representative", "1", "ESCAMBIA")
    district = by_name["Derek Felder"]
    assert (district.level, district.jurisdiction) == (Level.LOCAL, "LEE")
    # Not the US Senate race or a retention vote.
    assert {r.office for r in rows} == {
        "Governor and Lieutenant Governor",
        "State Representative",
        "Lehigh Acres Municipal Services Improvement District, Seat 4",
    }


def test_minnesota_county_offices_are_one_race_per_county() -> None:
    rows = _candidacies("MN")
    sheriffs = {r.jurisdiction: r.candidate_name for r in rows if r.office == "County Sheriff"}
    assert sheriffs == {
        "AITKIN": "Daniel Guida",
        "RAMSEY": "Bob Fletcher",
        "ST. LOUIS": "Gordon Scott Ramsay",
    }
    assert {r.level for r in rows if r.office == "County Sheriff"} == {Level.LOCAL}
    governor = _by_name(rows)["Lisa Demuth and Ryan Wilson"]
    assert (governor.party, governor.counties) == ("REP", STATEWIDE)
    assert "WRITE-IN" not in _by_name(rows)


def test_offices_and_districts() -> None:
    # Real contest names from the 2026 files.
    assert office_and_district("State Senator - District 8") == ("State Senator", "8")
    assert office_and_district("State Representative District 10A") == (
        "State Representative",
        "10A",
    )
    assert office_and_district("State  House of Representatives, District  1") == (
        "State House of Representatives",
        "1",
    )
    assert office_and_district("State Senate District 15 (Unexpired Term)") == (
        "State Senate (Unexpired Term)",
        "15",
    )
    assert office_and_district("STATE SENATOR, 3rd Senatorial District - UNEXPIRED TERM") == (
        "STATE SENATOR - UNEXPIRED TERM",
        "3",
    )
    assert office_and_district("HOUSE OF DELEGATES, 1st District") == ("HOUSE OF DELEGATES", "1")
    assert office_and_district("Solicitor, Circuit  3") == ("Solicitor", "3")
    assert office_and_district("Anderson County School Trustee District 2 At Large") == (
        "Anderson County School Trustee At Large",
        "2",
    )
    assert office_and_district("State Board of Education Member - Congressional District 7") == (
        "State Board of Education Member",
        "7",
    )
    assert office_and_district("Regional Transportation District Director - District J") == (
        "Regional Transportation District Director",
        "J",
    )
    assert office_and_district("County Commissioner District 01") == ("County Commissioner", "1")
    # No district to take out: left whole.
    assert office_and_district("Judge - 10th District Court 1") == (
        "Judge - 10th District Court 1",
        "",
    )
    assert office_and_district("Circuit Judge, 11th Judicial Circuit, Group 5") == (
        "Circuit Judge, 11th Judicial Circuit, Group 5",
        "",
    )


def test_source_writes_candidates_from_the_same_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enr, "FEEDS", {2026: {"IA": Feed(f"{enr.CLARITY}/IA", "127077")}})
    web = _web(fixture_bytes("enr_ia_2026.xml"))

    assert run(EnrSource(web), [2026], tmp_path) == {2026: 2}

    csv_text = (tmp_path / "enr" / "2026.csv").read_text(encoding="utf-8")
    assert "IA,2026,2026-11-03,state,Governor and Lt. Governor,,,*,Rob Sand and Dave" in csv_text
    assert "US Senate" not in csv_text and "Write-in" not in csv_text
    assert not (tmp_path / "results").exists()  # no votes yet
    # candidacies() and results() share one download.
    assert sum("detailxml.zip" in url for url in web.requested) == 1
