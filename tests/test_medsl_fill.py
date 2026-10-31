from __future__ import annotations

import logging

import pytest
from conftest import FakeWeb, fixture_bytes

from who_is_running.models import STATEWIDE, Candidacy, Level
from who_is_running.results import CountyResult
from who_is_running.sources import medsl_fill
from who_is_running.sources.medsl_fill import FILLS, Fill, apply, fill_results
from who_is_running.xlsx import _column, sheets

ID_CONTESTS = (17248, 17251, 17252, 17253, 17254, 17255, 17256, 17257)
CENSUS = {"national_county.txt": fixture_bytes("census_fill_counties.txt")}


def _fill(state: str, year: int) -> Fill:
    return next(f for f in FILLS if (f.state, f.year) == (state, year))


def _candidacy(
    state: str, office: str, name: str, party: str, district: str = "", counties: str = "ADAMS"
) -> Candidacy:
    return Candidacy(
        state=state,
        election_year=2022,
        election_date="2022-11-08",
        level=Level.FEDERAL if office.startswith("US ") else Level.STATE,
        office=office,
        district=district,
        jurisdiction="",
        counties=counties,
        candidate_name=name,
        party=party,
        election_type="general",
        incumbent_status="",
        candidate_status="",
        source="medsl",
        source_candidate_id="",
    )


def _result(state: str, office: str, fips: str, name: str, votes: int) -> CountyResult:
    return CountyResult(state, 2022, office, fips, "", name, "", votes, "medsl")


def _votes(rows: list[CountyResult]) -> dict[tuple[str, str, str], int]:
    return {(r.office, r.county_fips, r.candidate_name): r.votes for r in rows}


def _counties() -> dict[str, tuple[str, str]]:
    return medsl_fill.census_counties(FakeWeb(CENSUS))


def test_xlsx_reads_shared_strings_by_cell_reference() -> None:
    (rows,) = sheets(fixture_bytes("ca_sov_candidates.xlsx")).values()
    assert rows[0][3] == "County Name"
    assert rows[3][3:8] == ["Alameda", "100000000000", "US Senate - 1", "1581", "Alex Padilla"]
    assert rows[3][-1] == "383300"
    assert (_column("A"), _column("Z"), _column("AA"), _column("AX")) == (0, 25, 26, 49)


def test_california_senate_races_come_from_the_statement_of_vote() -> None:
    web = FakeWeb({"csv-candidates.xlsx": fixture_bytes("ca_sov_candidates.xlsx")})

    rows = fill_results(_fill("CA", 2022), web, [], _counties())

    assert _votes(rows) == {
        ("US Senate", "06001", "ALEX PADILLA"): 383300,
        ("US Senate", "06001", "MARK P. MEUSER"): 94283,
        ("US Senate", "06003", "ALEX PADILLA"): 372,
        ("US Senate", "06003", "MARK P. MEUSER"): 240,
        ("US Senate (special)", "06001", "ALEX PADILLA"): 379778,
        ("US Senate (special)", "06001", "MARK P. MEUSER"): 94326,
        ("US Senate (special)", "06003", "ALEX PADILLA"): 373,
        ("US Senate (special)", "06003", "MARK P. MEUSER"): 240,
    }  # and not the governor's race, which MEDSL has
    assert {(r.candidate_name, r.party) for r in rows} == {
        ("ALEX PADILLA", "DEM"),
        ("MARK P. MEUSER", "REP"),
    }
    assert {(r.county_fips, r.county, r.source) for r in rows} == {
        ("06001", "ALAMEDA", "ca-sos"),
        ("06003", "ALPINE", "ca-sos"),
    }


def test_tennessee_governor_adds_up_precincts_and_skips_write_ins() -> None:
    web = FakeWeb({"AllbyPrecinct.xlsx": fixture_bytes("tn_by_precinct.xlsx")})

    rows = fill_results(_fill("TN", 2022), web, [], _counties())

    votes = _votes(rows)
    assert votes["GOVERNOR", "47003", "BILL LEE"] == 818
    assert votes["GOVERNOR", "47001", "BILL LEE"] == 978 + 178
    assert votes["GOVERNOR", "47001", "JASON BRANTLEY MARTIN"] == 161 + 27
    assert {r.office for r in rows} == {"GOVERNOR"}  # no amendment "Yes"/"No"
    assert not any("WRITE" in r.candidate_name for r in rows)
    assert len({r.candidate_name for r in rows}) == 10
    lee = next(r for r in rows if r.candidate_name == "BILL LEE")
    assert lee.party == "REP"


def test_idaho_canvass_uses_medsl_spelling_and_skips_totals() -> None:
    web = FakeWeb({f"download/{i}/": fixture_bytes(f"id_canvass_{i}.csv") for i in ID_CONTESTS})
    medsl = [
        _candidacy("ID", "US Senate", "MIKE CRAPO", "REP"),
        _candidacy("ID", "US Senate", "SCOTT OH CLEVELAND", "IND"),
        _candidacy("ID", "LIEUTENANT GOVERNOR", "SCOTT BEDKE", "REP"),
        # MEDSL splits his votes over two spellings; the fill counts them as one.
        _candidacy("ID", "ATTORNEY GENERAL", "RAUL LABRADOR", "REP"),
        _candidacy("ID", "ATTORNEY GENERAL", "RAUL R LABRADOR", "REP"),
        # Same name, other race: not a match for the senate race.
        _candidacy("ID", "GOVERNOR", "DAVID ROTH", "WRONG"),
    ]

    rows = fill_results(_fill("ID", 2022), web, medsl, _counties())

    votes = _votes(rows)
    assert votes["US Senate", "16003", "MIKE CRAPO"] == 1425
    assert votes["US Senate", "16077", "DAVID ROTH"] == 420
    assert votes["LIEUTENANT GOVERNOR", "16077", "SCOTT BEDKE"] == 1491
    assert votes["SUPERINTENDENT OF PUBLIC INSTRUCTION", "16003", "DEBBIE CRITCHFIELD"] == 1606
    assert votes["GOVERNOR", "16077", "BRAD LITTLE"] == 1411  # MEDSL has 0 for Power
    assert votes["ATTORNEY GENERAL", "16077", "RAUL LABRADOR"] == 1337
    assert not any(r.candidate_name == "RAUL R LABRADOR" for r in rows)
    assert {r.county_fips for r in rows} == {"16003", "16077"}  # not the "Totals" row
    assert not any(r.candidate_name == "TOTAL VOTES CAST" for r in rows)
    parties = {(r.office, r.candidate_name): r.party for r in rows}
    assert parties["US Senate", "SCOTT OH CLEVELAND"] == "IND"
    assert parties["US Senate", "DAVID ROTH"] == "DEM"
    assert parties["US Senate", "RAY J. WRITZ"] == "CONSTITUTIONAL"


def test_indiana_enr_matches_medsl_names_by_shared_words() -> None:
    web = FakeWeb({"OffCatC_1006_A.json": fixture_bytes("in_enr_ussenate.json")})
    fill = Fill(
        "IN",
        2022,
        "in-sos",
        ("OffCatC_1006_A.json",),
        lambda b: medsl_fill.parse_in_enr(b, ["US Senate"]),
    )
    medsl = [
        _candidacy("IN", "US Senate", "TODD YOUNG", "REP"),
        _candidacy("IN", "US Senate", "THOMAS M MCDERMOTT JR", "DEM"),
        _candidacy("IN", "US Senate", "JAMES M SCENIAK", "LIB"),
    ]

    rows = fill_results(fill, web, medsl, _counties())

    assert _votes(rows) == {
        ("US Senate", "18001", "TODD YOUNG"): 6914,
        ("US Senate", "18001", "THOMAS M MCDERMOTT JR"): 1841,
        ("US Senate", "18001", "JAMES M SCENIAK"): 195,
        ("US Senate", "18033", "TODD YOUNG"): 8584,
        ("US Senate", "18033", "THOMAS M MCDERMOTT JR"): 2773,
        ("US Senate", "18033", "JAMES M SCENIAK"): 371,
    }  # write-ins ("Danny Niederberger (W/I)", 4 votes in DeKalb) are left out


def test_north_dakota_governor_from_openelections() -> None:
    web = FakeWeb({"20241105__nd__general__county.csv": fixture_bytes("oe_nd_2024_county.csv")})
    medsl = [
        _candidacy("ND", "GOVERNOR AND LIEUTENANT GOVERNOR", "KELLY ARMSTRONG", "REP"),
        _candidacy("ND", "GOVERNOR AND LIEUTENANT GOVERNOR", "MERRILL PIEPKORN", "DEM"),
    ]

    rows = fill_results(_fill("ND", 2024), web, medsl, _counties())

    assert {(r.county_fips, r.candidate_name, r.party): r.votes for r in rows} == {
        ("38015", "KELLY ARMSTRONG", "REP"): 36496,
        ("38015", "MERRILL PIEPKORN", "DEM"): 11343,
        ("38015", "MICHAEL (COACH) COACHMAN AND LYDIA GESSELE", ""): 3492,
        ("38023", "KELLY ARMSTRONG", "REP"): 894,
        ("38023", "MERRILL PIEPKORN", "DEM"): 226,
        ("38023", "MICHAEL (COACH) COACHMAN AND LYDIA GESSELE", ""): 67,
    }  # not the Senate rows, nor the write-in line
    assert {r.election_year for r in rows} == {2024}


def test_unknown_county_is_an_error_not_a_guess() -> None:
    fill = Fill(
        "ID",
        2022,
        "id-sos",
        ("x",),
        lambda b: [medsl_fill.Vote("US Senate", "Atlantis", "MIKE CRAPO", "R", 5)],
    )
    with pytest.raises(ValueError, match="Atlantis"):
        fill_results(fill, FakeWeb({"x": b""}), [], _counties())


def test_apply_replaces_medsl_rows_for_the_race_and_marks_it_statewide() -> None:
    canvass = {f"download/{i}/": fixture_bytes(f"id_canvass_{i}.csv") for i in ID_CONTESTS}
    web = FakeWeb({**CENSUS, **canvass})
    crapo = _candidacy("ID", "US Senate", "MIKE CRAPO", "REP")
    house = _candidacy("ID", "US House", "MIKE SIMPSON", "REP", district="02")
    medsl = [_result("ID", "US Senate", "16003", "MIKE CRAPO", 2850)]  # doubled
    governor = _result("NV", "GOVERNOR", "32009", "JOE LOMBARDO", 338)  # no fill for it

    candidacies, results = apply(2022, web, [crapo, house], [*medsl, governor])

    assert [c.counties for c in candidacies] == [STATEWIDE, "ADAMS"]  # the House race is untouched
    votes = _votes(results)
    assert votes["US Senate", "16003", "MIKE CRAPO"] == 1425  # replaced, not added to
    assert votes["GOVERNOR", "32009", "JOE LOMBARDO"] == 338  # kept
    assert sum(1 for r in results if r.source == "medsl") == 1


def test_apply_skips_a_fill_that_fails_and_states_medsl_has_no_file_for(
    caplog: pytest.LogCaptureFixture,
) -> None:
    web = FakeWeb(CENSUS)  # no canvass files
    crapo = _candidacy("ID", "US Senate", "MIKE CRAPO", "REP")
    medsl = [_result("ID", "US Senate", "16003", "MIKE CRAPO", 1425)]

    with caplog.at_level(logging.WARNING):
        assert apply(2022, web, [crapo], medsl) == ([crapo], medsl)

    assert "couldn't fill ID 2022 from id-sos" in caplog.text
    # CA, TN and IN have no MEDSL rows here, so nothing is fetched for them.
    assert not any(host in u for u in web.requested for host in ("sos.ca", "tnsos", "indiana"))
    assert apply(2020, web, [crapo], medsl) == ([crapo], medsl)
