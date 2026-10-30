from __future__ import annotations

from datetime import date

from conftest import FakeWeb, fixture_bytes

from who_is_running.districts import CD119, SLDL_2024
from who_is_running.models import Candidacy, Level
from who_is_running.sources.md import ELECTIONS, MdSource, district_of

ARCHIVE_2026_GENERAL = "elections/archive/2026/general_candidates/"
CURRENT_2026_GENERAL = "elections/2026/general_candidates/"


def _general_2026(web: FakeWeb) -> dict[str, Candidacy]:
    return {r.candidate_name: r for r in MdSource(web).candidacies(2026)}


def _web(**routes: bytes) -> FakeWeb:
    return FakeWeb(
        {
            f"{CURRENT_2026_GENERAL}2026_GG_statewide": fixture_bytes("md_statewide.csv"),
            f"{CURRENT_2026_GENERAL}2026_GG_all_counties": fixture_bytes("md_counties.csv"),
            CD119: fixture_bytes("census_cd119.txt"),
            SLDL_2024: fixture_bytes("census_sldl.txt"),
            **routes,
        }
    )


def test_statewide_list_is_federal_or_state() -> None:
    rows = _general_2026(_web())

    moore = rows["Wes Moore"]
    assert moore.level is Level.STATE
    assert moore.office == "Governor / Lt. Governor"
    assert moore.party == "DEM"
    assert moore.jurisdiction == ""
    assert moore.counties == "*"  # run by "State Of Maryland"
    assert moore.election_date == "2026-11-03"
    assert moore.election_type == "general"
    assert moore.candidate_status == "active"

    rep = rows["Pat Q. Example"]
    assert rep.level is Level.FEDERAL
    assert rep.district == "3"
    assert rep.candidate_status == "withdrawn"
    # The list doesn't say which counties a district covers; the Census lines do.
    assert rep.counties == "ANNE ARUNDEL;CARROLL;HOWARD"

    assert rows["Dana Delegate"].district == "11B"
    assert rows["Dana Delegate"].party == "UNA"
    assert rows["Dana Delegate"].counties == "BALTIMORE"

    judge = rows["Robin Bench"]
    assert judge.party == ""  # "Judicial" isn't a party
    # Listed by circuit, but elected in the county the judge sits in.
    assert (judge.district, judge.jurisdiction, judge.counties) == (
        "1",
        "Wicomico County",
        "Wicomico County",
    )


def test_county_list_is_local_with_county() -> None:
    rows = _general_2026(_web())

    boor = rows["Chelsea Boor"]
    assert boor.level is Level.LOCAL
    assert boor.jurisdiction == "Allegany County"
    assert boor.counties == "Allegany County"
    assert boor.district == ""

    assert rows["Tammy Fraley"].district == "4"
    assert rows["Tammy Fraley"].party == ""
    assert rows["Thomas M. Pelagatti"].candidate_status == "declined"


def test_html_error_pages_are_treated_as_missing() -> None:
    html = b"<!DOCTYPE html><html><title>404: Page Not Found</title></html>"
    web = FakeWeb(
        {
            f"{CURRENT_2026_GENERAL}2026_GG_statewide": html,
            f"{ARCHIVE_2026_GENERAL}2026_GG_statewide": fixture_bytes("md_statewide.csv"),
        }
    )

    rows = _general_2026(web)

    # Fell back to the archive URL for the statewide list; the county list is missing.
    assert "Wes Moore" in rows
    assert "Chelsea Boor" not in rows


def test_years_without_files_yield_nothing() -> None:
    assert MdSource(FakeWeb({})).candidacies(2026) == []
    assert MdSource(FakeWeb({})).candidacies(2020) == []


def test_years() -> None:
    source = MdSource(FakeWeb({}))
    assert source.available_years() == sorted(ELECTIONS)
    assert source.refresh_years(date(2027, 1, 5)) == [2026]


def test_district_of() -> None:
    assert district_of("Congressional District 8") == "8"
    assert district_of("Judicial Circuit 1") == "1"
    assert district_of("State Of Maryland") == ""
    assert district_of("Baltimore City") == ""
