"""Tests for the Google Civic Information API source.

Fixtures:
- civic_elections.json: a real 2026 ``elections`` response (captured 2026-10-15 in
  Call-for-Code-for-Racial-Justice/Five-Fifths-Voter, Apache-2.0).
- civic_voterinfo_venice.json: Google's documented sample ``voterinfo`` response
  (developers.google.com/civic-information/docs/using_api), with its trailing comma removed.
- civic_voterinfo_no_contests.json: trimmed from a real 2026 response for Illinois
  (same capture), which had locations but no contests.
- civic_voterinfo_autauga.json / _prattville.json: built from the documented response
  format (discovery doc revision 20261028) with made-up names, to cover each district scope.
- civic_govt_units.zip: real rows of the Census 2022 Government Units List, repacked as a
  minimal .xlsx.
- census_sldl_al.txt: real rows of the Census 2024 state house district-county file.
"""

from __future__ import annotations

import logging
import urllib.error
from datetime import date
from pathlib import Path

import pytest
from conftest import FakeWeb, fixture_bytes

from who_is_running.districts import SLDL_2024
from who_is_running.http import NotFound
from who_is_running.models import Candidacy, Level
from who_is_running.pipeline import output_path, run, write_csv
from who_is_running.sources.civic import (
    GOVT_UNITS,
    KEY_ENV,
    CivicError,
    CivicSource,
    Election,
    Seed,
    district_of,
    level_of,
    parse_govt_units,
    party_code,
    plan_calls,
)
from who_is_running.text import decode

KEY = "AIzaTESTKEY123"
GENERAL = Election("12000", "2026 General Midterm Election", "2026-11-03", "")


class Web(FakeWeb):
    """FakeWeb that can also answer with an HTTP error for some URLs."""

    def __init__(self, routes: dict[str, bytes], errors: dict[str, int] | None = None) -> None:
        super().__init__(routes)
        self.errors = errors or {}

    def __call__(self, url: str) -> bytes:
        for fragment, code in self.errors.items():
            if fragment in url:
                self.requested.append(url)
                err = urllib.error.HTTPError(url, code, "error", None, None)  # type: ignore[arg-type]
                if code in (403, 404):
                    raise NotFound(url) from err  # what http_get does
                raise err
        return super().__call__(url)


def _web(errors: dict[str, int] | None = None, extra: dict[str, bytes] | None = None) -> Web:
    return Web(
        {
            "/civicinfo/v2/elections?": fixture_bytes("civic_elections.json"),
            GOVT_UNITS: fixture_bytes("civic_govt_units.zip"),
            SLDL_2024: fixture_bytes("census_sldl_al.txt"),
            "address=135+N+COURT+ST": fixture_bytes("civic_voterinfo_autauga.json"),
            "address=101+W+MAIN+ST": fixture_bytes("civic_voterinfo_prattville.json"),
            "address=500+W+TEMPLE+ST": fixture_bytes("civic_voterinfo_venice.json"),
            # Geocoded into another state: ignored.
            "address=333+W+OCEAN+BLVD": fixture_bytes("civic_voterinfo_no_contests.json"),
            **(extra or {}),
        },
        # Unparseable address. The Autauga school district's address isn't routed: 404.
        errors={"address=200+N+SPRING+ST": 400, **(errors or {})},
    )


def _source(web: Web, today: date = date(2026, 10, 31)) -> CivicSource:
    return CivicSource(web, key=KEY, today=lambda: today, rate=0, workers=2)


def _by_name(rows: list[Candidacy]) -> dict[str, Candidacy]:
    return {r.candidate_name: r for r in rows}


def test_contests_become_candidacies() -> None:
    web = _web()
    rows = _source(web).candidacies(2026)
    by_name = _by_name(rows)

    governor = by_name["Pat Example"]
    assert governor == Candidacy(
        state="AL",
        election_year=2026,
        election_date="2026-11-03",
        level=Level.STATE,
        office="Governor",
        district="",
        jurisdiction="",
        counties="*",
        candidate_name="Pat Example",
        party="REP",
        election_type="general",
        incumbent_status="",
        candidate_status="",
        source="civic",
        source_candidate_id="",
    )
    # Seen at two addresses, listed once.
    assert [r.candidate_name for r in rows].count("Sam Sample") == 1
    assert by_name["Sam Sample"].party == "DEM"

    # State house: counties from the Census lines, not just the sampled county.
    assert by_name["Jordan Delegate"].district == "42"
    assert by_name["Jordan Delegate"].counties == "AUTAUGA;CHILTON"
    assert by_name["Riley Prattville"].counties == "AUTAUGA;ELMORE"
    # State senate lines aren't available here: fall back to where it was seen.
    senate = by_name["Casey Senate"]
    assert (senate.district, senate.counties, senate.party) == ("30", "AUTAUGA", "")
    judge = by_name["Morgan Bench"]
    assert (judge.level, judge.jurisdiction, judge.counties) == (Level.STATE, "", "AUTAUGA")
    assert judge.election_type == "general"  # "Retention" contest in the general election

    commissioner = by_name["Avery County"]
    assert commissioner.level is Level.LOCAL
    assert (commissioner.district, commissioner.jurisdiction) == ("1", "AUTAUGA")
    assert commissioner.counties == "AUTAUGA"
    assert by_name["Drew Local"].party == "Independent"
    assert by_name["Quinn School"].party == "LIB"

    # Google's own sample (Los Angeles): the county race, not the House race or the measure.
    da = by_name["GEORGE GASCÓN"]
    assert (da.state, da.level, da.office) == ("CA", Level.LOCAL, "DISTRICT ATTORNEY")
    assert (da.jurisdiction, da.counties, da.party) == ("LOS ANGELES", "LOS ANGELES", "")
    assert sum(r.office == "DISTRICT ATTORNEY" for r in rows) == 12

    # Federal races (the fec source has them), write-ins and referendums are skipped.
    for name in ("Lee Federal", "Robin House", "TED W. LIEU", "Write-in"):
        assert name not in by_name
    assert {r.state for r in rows} == {"AL", "CA"}  # the Illinois answer is ignored
    assert all(r.counties for r in rows)

    # One lookup per sampled address; the key goes only to Google.
    lookups = [u for u in web.requested if "/voterinfo?" in u]
    assert len(lookups) == 6
    assert all("electionId=12000" in u and f"key={KEY}" in u for u in lookups)
    assert all(KEY not in u for u in web.requested if "googleapis.com" not in u)


def test_no_contact_details_are_kept() -> None:
    rows = _source(_web()).candidacies(2026)
    text = repr(rows)
    for secret in ("example.org", "555", "patexample"):
        assert secret not in text


def test_missing_key_skips_without_touching_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    web = _web()
    source = CivicSource(web, today=lambda: date(2026, 10, 31))
    existing = output_path(tmp_path, source, 2026)
    write_csv(existing, [])
    before = existing.read_bytes()

    with pytest.raises(NotFound, match=KEY_ENV):
        source.candidacies(2026)
    assert run(source, [2026], tmp_path) == {}
    assert existing.read_bytes() == before
    assert web.requested == []


def test_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY)
    web = _web()
    CivicSource(web, today=lambda: date(2026, 10, 31), rate=0).candidacies(2026)
    assert any(f"key={KEY}" in u for u in web.requested)


def test_file_is_deleted_once_the_election_is_no_longer_served(tmp_path: Path) -> None:
    source = _source(_web(), today=date(2026, 11, 4))
    existing = output_path(tmp_path, source, 2026)
    write_csv(existing, [])

    assert source.candidacies(2026) == []
    run(source, [2026], tmp_path)
    assert not existing.exists()


def test_test_elections_are_ignored() -> None:
    web = _web()
    _source(web).candidacies(2026)
    assert not any("electionId=2000" in u or "electionId=1001" in u for u in web.requested)


@pytest.mark.parametrize("code", [400, 403])
def test_rejected_key_fails_without_leaking_it(code: int) -> None:
    web = _web(errors={"/civicinfo/v2/elections?": code})
    with pytest.raises(CivicError) as caught:
        _source(web).candidacies(2026)
    assert KEY not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("code", [429, 403])
def test_quota_errors_stop_the_run(code: int, tmp_path: Path) -> None:
    web = _web(errors={"address=135+N+COURT+ST": code})
    source = _source(web)
    with pytest.raises(CivicError) as caught:
        source.candidacies(2026)
    assert KEY not in str(caught.value)
    assert f"HTTP {code}" in str(caught.value)


def test_network_errors_are_redacted(caplog: pytest.LogCaptureFixture) -> None:
    def broken(url: str) -> bytes:
        if "/voterinfo?" in url:
            raise urllib.error.URLError(f"timed out fetching {url}")
        return _web()(url)

    caplog.set_level(logging.WARNING)
    source = CivicSource(broken, key=KEY, today=lambda: date(2026, 10, 31), rate=0)
    assert source.candidacies(2026) == []  # a few failures are tolerated
    assert "voterinfo lookup failed" in caplog.text
    assert KEY not in caplog.text
    assert "key=REDACTED" in caplog.text


def test_seeds_are_street_addresses_from_the_census_list() -> None:
    seeds = parse_govt_units(fixture_bytes("civic_govt_units.zip"))
    assert seeds[0] == Seed("CA", "LOS ANGELES", "500 W TEMPLE ST, LOS ANGELES, CA 90012", 9829544)
    autauga = [s.address for s in seeds if s.county == "AUTAUGA"]
    # County government first, then the city, then the school district.
    assert autauga == [
        "135 N COURT ST, PRATTVILLE, AL 36067",
        "101 W MAIN ST, PRATTVILLE, AL 36067",
        "153 W 4TH ST, PRATTVILLE, AL 36067",
    ]
    # Counties with only PO boxes (Chilton, Bullock, Wake) and inactive units drop out.
    assert {s.county for s in seeds} == {"LOS ANGELES", "AUTAUGA"}


def test_plan_gives_every_county_one_lookup_then_favors_big_ones() -> None:
    big = [Seed("CA", "BIG", f"{n} A ST", 1_000_000) for n in range(1, 30)]
    small = [Seed("AL", "SMALL", f"{n} B ST", 120_000) for n in range(1, 5)]
    tiny = [Seed("AL", "TINY", "1 C ST", 1_000)]
    # Extra lookups go where people per lookup is highest: 1M/16 > 120k/2 > 1M/17.
    calls = plan_calls([GENERAL], big + small + tiny, budget=19)
    counties = [s.county for _, s in calls]
    assert counties[:3] == ["BIG", "SMALL", "TINY"]
    assert counties.count("BIG") == 16 and counties.count("SMALL") == 2

    state_only = Election("9", "Louisiana runoff", "2026-12-12", "AL")
    assert {s.state for _, s in plan_calls([state_only], big + small, budget=100)} == {"AL"}


def test_helpers() -> None:
    assert party_code("Party Preference: Democratic") == "DEM"
    assert party_code("No Party Preference") == ""
    assert party_code("Constitution") == "Constitution"
    assert district_of({"scope": "congressional", "name": "36TH US CONGRESSIONAL"}) == "36"
    assert district_of(
        {"scope": "stateLower", "id": "ocd-division/country:us/state:vt/sldl:7"}
    ) == ("7")
    assert district_of({"scope": "statewide", "id": "ocd-division/country:us/state:al"}) == ""
    assert district_of({"scope": "countywide", "name": "LOS ANGELES COUNTY"}) == ""
    assert level_of({"office": "U.S. Senator", "district": {"scope": "statewide"}}) is None
    assert level_of({"office": "State Senator", "district": {"scope": "stateUpper"}}) is Level.STATE
    assert level_of({"office": "Mayor", "level": ["locality"]}) is Level.LOCAL


def test_redrawn_districts_fall_back_to_where_they_were_seen() -> None:
    # Pretend the Census lines put house district 42 in other counties only.
    lines = decode(fixture_bytes("census_sldl_al.txt")).replace("Autauga County", "Shelby County")
    rows = _source(_web(extra={SLDL_2024: lines.encode()})).candidacies(2026)
    assert _by_name(rows)["Jordan Delegate"].counties == "AUTAUGA"


def test_missing_address_list_fails_loudly() -> None:
    web = _web()
    del web.routes[GOVT_UNITS]
    with pytest.raises(CivicError, match="NotFound"):
        _source(web).candidacies(2026)
