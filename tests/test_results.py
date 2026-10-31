from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from conftest import FIXTURES, FakeWeb, fixture_bytes

from who_is_running import geo
from who_is_running.build_site import MAP_COUNTIES, NATIONAL_RACES, build, build_results
from who_is_running.districts import STATE_FIPS
from who_is_running.results import CountyResult, results_path, write_csv
from who_is_running.sources.medsl import MedslSource, parse_with_results

HEADER = fixture_bytes("medsl_precincts.csv").decode().splitlines()[0]
WEB = Path(__file__).parent.parent / "web"


def _row(
    precinct: str,
    office: str,
    candidate: str,
    party: str,
    mode: str,
    votes: int,
    county: str,
    fips: str,
    district: str = "STATEWIDE",
    dataverse: str = "PRESIDENT",
) -> str:
    """One MEDSL precinct row, in the column order of the 2024 Alaska and Delaware files."""
    return (
        f"{precinct},{office},{party},{party},{mode},{votes},{county},{fips},{county},{fips},"
        f"{candidate},{district},{dataverse},2024,GEN,ALASKA,FALSE,FALSE,AK,2,94,81,2024-11-05,1"
    )


def _results(*rows: str) -> list[CountyResult]:
    return sorted(parse_with_results([HEADER, *rows])[1])


def _votes(results: list[CountyResult]) -> dict[tuple[str, str], int]:
    return {(r.county_fips, r.candidate_name): r.votes for r in results}


def test_statewide_votes_add_up_by_county() -> None:
    results = _results(
        # A precinct with a TOTAL row: the per-mode rows beside it are already in it.
        _row(
            "01-440", "US PRESIDENT", "DONALD J TRUMP", "REPUBLICAN", "TOTAL", 300, "KENAI", "2122"
        ),
        _row(
            "01-440",
            "US PRESIDENT",
            "DONALD J TRUMP",
            "REPUBLICAN",
            "ELECTION DAY",
            250,
            "KENAI",
            "2122",
        ),
        _row(
            "01-440", "US PRESIDENT", "DONALD J TRUMP", "REPUBLICAN", "EARLY", 50, "KENAI", "2122"
        ),
        # Without one, the modes are summed.
        _row(
            "01-445",
            "US PRESIDENT",
            "DONALD J TRUMP",
            "REPUBLICAN",
            "ELECTION DAY",
            70,
            "KENAI",
            "2122",
        ),
        _row(
            "01-445", "US PRESIDENT", "DONALD J TRUMP", "REPUBLICAN", "EARLY", 30, "KENAI", "2122"
        ),
        _row("36-750", "US PRESIDENT", "KAMALA D HARRIS", "DEMOCRAT", "TOTAL", 90, "NOME", "2180"),
        # Alaska counts early and absentee ballots by district, not county.
        _row("DISTRICT 1", "US PRESIDENT", "KAMALA D HARRIS", "DEMOCRAT", "ABSENTEE", 40, "", ""),
    )

    assert _votes(results) == {
        ("02122", "DONALD J TRUMP"): 400,
        ("02180", "KAMALA D HARRIS"): 90,
        ("", "KAMALA D HARRIS"): 40,
    }
    assert {r.county for r in results} == {"KENAI", "NOME", ""}
    assert {r.office for r in results} == {"US President"}


def test_only_statewide_races_keep_county_votes() -> None:
    results = _results(
        _row("1", "US PRESIDENT", "DONALD J TRUMP", "REPUBLICAN", "TOTAL", 5, "KENAI", "2122"),
        _row("2", "US PRESIDENT", "DONALD J TRUMP", "REPUBLICAN", "TOTAL", 5, "NOME", "2180"),
        _row(
            "1",
            "STATE HOUSE",
            "BEN CARPENTER",
            "REPUBLICAN",
            "TOTAL",
            9,
            "KENAI",
            "2122",
            "008",
            "STATE",
        ),
        # Ohio's files list every district's candidates in every county with zero
        # votes. Those rows don't make a district race statewide.
        _row(
            "2",
            "STATE HOUSE",
            "BEN CARPENTER",
            "REPUBLICAN",
            "TOTAL",
            0,
            "NOME",
            "2180",
            "",
            "STATE",
        ),
    )

    assert {r.office for r in results} == {"US President"}


MI_18_1 = (
    "STATE PROPOSAL - 18-1: LEGISLATIVE INITIATIVE: COALITION TO REGULATE MARIJUANA LIKE ALCOHOL"
)


def test_statewide_measures_keep_yes_and_no_votes_by_county() -> None:
    # Michigan 2018's Proposal 1: Eaton County reports TOTAL rows, Wayne by mode.
    rows = [
        _row('"0-22L, WARD 3"', MI_18_1, "YES", "NONPARTISAN", "TOTAL", 366, "EATON", "26045"),
        _row('"0-22L, WARD 3"', MI_18_1, "NO", "NONPARTISAN", "TOTAL", 119, "EATON", "26045"),
        _row('"0-23, WARD 3"', MI_18_1, "YES", "NONPARTISAN", "TOTAL", 790, "EATON", "26045"),
        _row('"0-23, WARD 3"', MI_18_1, "NO", "NONPARTISAN", "TOTAL", 263, "EATON", "26045"),
        _row("1", MI_18_1, "YES", "NONPARTISAN", "ELECTION DAY", 137, "WAYNE", "26163"),
        _row("1", MI_18_1, "NO", "NONPARTISAN", "ELECTION DAY", 36, "WAYNE", "26163"),
    ]
    rows = [r.replace(",STATEWIDE,PRESIDENT,", ",STATEWIDE,STATE,") for r in rows]  # a STATE row
    candidacies, results = parse_with_results([HEADER, *rows])

    assert _votes(sorted(results)) == {
        ("26045", "Yes"): 1156,
        ("26045", "No"): 382,
        ("26163", "Yes"): 137,
        ("26163", "No"): 36,
    }
    assert {(r.office, r.party) for r in results} == {(MI_18_1, "")}
    # Yes and No aren't people running for anything.
    assert candidacies == []


def test_measure_answers_are_read_as_yes_and_no() -> None:
    # Florida's and Oklahoma's 2024 wording; Florida's overvote lines are dropped.
    fl = "AMENDMENT NO 3: ADULT PERSONAL USE OF MARIJUANA"
    ok = "STATE QUESTION NO. 834 LEGISLATIVE REFERENDUM NO. 377"
    results = _results(
        _row("1", fl, "YES FOR APPROVAL", "", "TOTAL", 607, "KENAI", "2122", "", "STATE"),
        _row("1", fl, "NO FOR REJECTION", "", "TOTAL", 484, "KENAI", "2122", "", "STATE"),
        _row("1", fl, "OVERVOTES", "", "TOTAL", 1, "KENAI", "2122", "", "STATE"),
        _row("10001", ok, "FOR THE PROPOSAL - YES", "", "ABSENTEE", 8, "KENAI", "2122", "", ""),
        _row("10001", ok, "AGAINST THE PROPOSAL - NO", "", "ABSENTEE", 0, "KENAI", "2122", "", ""),
    )

    assert {(r.office, r.candidate_name, r.votes) for r in results} == {
        (fl, "Yes", 607),
        (fl, "No", 484),
        (ok, "Yes", 8),
        (ok, "No", 0),
    }


def test_measures_not_on_every_ballot_are_dropped() -> None:
    results = _results(
        _row("1", "US PRESIDENT", "DONALD J TRUMP", "REPUBLICAN", "TOTAL", 5, "CASEY", "21045"),
        _row("2", "US PRESIDENT", "DONALD J TRUMP", "REPUBLICAN", "TOTAL", 5, "ADAIR", "21001"),
        # Kentucky 2022: a county's own question, with no dataverse.
        _row("A104", "QUESTION", "YES", "", "TOTAL", 119, "CASEY", "21045", "", ""),
        _row("A104", "QUESTION", "NO", "", "TOTAL", 85, "CASEY", "21045", "", ""),
        # Local measures are never kept, even where they cover every county here.
        *(
            _row(p, "PROPOSITION 1", answer, "", "TOTAL", 3, county, fips, "", "LOCAL")
            for p, county, fips in (("1", "CASEY", "21045"), ("2", "ADAIR", "21001"))
            for answer in ("YES", "NO")
        ),
    )

    assert {r.office for r in results} == {"US President"}


def test_yes_no_lines_that_are_not_measures() -> None:
    results = _results(
        # Florida 2024: judges up for retention.
        _row(
            "1",
            "SUPREME COURT - RETENTION - RENATHA FRANCIS",
            "YES",
            "NONPARTISAN",
            "TOTAL",
            620,
            "ALACHUA",
            "12001",
            "JUSTICE OF THE SUPREME COURT",
            "STATE",
        ),
        _row(
            "1",
            "SUPREME COURT - RETENTION - RENATHA FRANCIS",
            "NO",
            "NONPARTISAN",
            "TOTAL",
            380,
            "ALACHUA",
            "12001",
            "JUSTICE OF THE SUPREME COURT",
            "STATE",
        ),
        # Washington 2022's advisory votes answer Repealed or Maintained.
        _row(
            "RALSTON",
            "WASHINGTON STATE - ADVISORY VOTE NO. 39",
            "REPEALED",
            "",
            "TOTAL",
            29,
            "ALACHUA",
            "12001",
            "",
            "STATE",
        ),
        _row(
            "RALSTON",
            "WASHINGTON STATE - ADVISORY VOTE NO. 39",
            "MAINTAINED",
            "",
            "TOTAL",
            3,
            "ALACHUA",
            "12001",
            "",
            "STATE",
        ),
        # Michigan 2022 counts rejected write-ins under each race, as "REJECTED".
        _row(
            "Akron Township Precinct 1",
            "GOVERNOR",
            "GRETCHEN WHITMER",
            "DEMOCRAT",
            "TOTAL",
            187,
            "ALACHUA",
            "12001",
            "",
            "STATE",
        ),
        _row(
            "Akron Township Precinct 1",
            "GOVERNOR",
            "REJECTED",
            "",
            "TOTAL",
            1,
            "ALACHUA",
            "12001",
            "",
            "STATE",
        ),
        _row(
            "Akron Township Precinct 1",
            "GOVERNOR",
            "YES",
            "",
            "TOTAL",
            1,
            "ALACHUA",
            "12001",
            "",
            "STATE",
        ),
    )

    assert {(r.office, r.candidate_name) for r in results} == {("GOVERNOR", "GRETCHEN WHITMER")}


def test_measures_do_not_change_which_races_are_statewide() -> None:
    # Virginia 2018 spells one county "KING & QUEEN" in its amendment rows only.
    q1 = "PROPOSED CONSTITUTIONAL AMENDMENT QUESTION 1"
    candidacies, results = parse_with_results(
        [
            HEADER,
            _row(
                "1",
                "US SENATE",
                "TIMOTHY KAINE",
                "DEMOCRAT",
                "TOTAL",
                9,
                "KING AND QUEEN",
                "51097",
                "",
                "SENATE",
            ),
            *(
                _row(
                    "# AB - CENTRAL ABSENTEE PRECINCT (01)",
                    q1,
                    answer,
                    "",
                    "TOTAL",
                    votes,
                    "KING & QUEEN",
                    "51097",
                    "",
                    "STATE",
                )
                for answer, votes in (("YES", 30), ("NO", 47))
            ),
        ]
    )

    assert [c.counties for c in candidacies] == ["*"]
    # ...and the amendment, never in "KING AND QUEEN", isn't on every ballot.
    assert {r.office for r in results} == {"US Senate"}
    assert {r.county for r in results} == {"KING AND QUEEN"}


def test_measure_lines_do_not_join_a_race_of_the_same_name() -> None:
    # Michigan 2022 has a STATE SENATE race with no district, and "YES" lines
    # under STATE SENATE elsewhere must not put it on that county's ballot.
    candidacies = parse_with_results(
        [
            HEADER,
            *(
                _row("1", "GOVERNOR", "GRETCHEN WHITMER", "DEMOCRAT", "TOTAL", 9, c, f, "", "STATE")
                for c, f in (("KEWEENAW", "26083"), ("OTTAWA", "26139"))
            ),
            _row(
                "1",
                "STATE SENATE",
                "EDWARD MCBROOM",
                "REPUBLICAN",
                "TOTAL",
                9,
                "KEWEENAW",
                "26083",
                "",
                "STATE",
            ),
            _row("1", "STATE SENATE", "YES", "", "TOTAL", 1, "OTTAWA", "26139", "", "STATE"),
            _row("1", "STATE SENATE", "NO", "", "TOTAL", 1, "OTTAWA", "26139", "", "STATE"),
        ]
    )[0]

    assert {(c.office, c.counties) for c in candidacies} == {
        ("GOVERNOR", "*"),
        ("STATE SENATE", "KEWEENAW"),
    }


def test_measure_in_a_file_without_counties() -> None:
    # Alaska 2022 reports by legislative district only. A STATE row is a state
    # measure; without a dataverse there's no telling.
    bm1 = "BM#1 CONSTITUTIONAL CONVENTION"
    q = "QUESTION"
    results = _results(
        _row("01-600 Ketchikan No. 1", bm1, "YES", "", "ELECTION DAY", 103, "", "", "", "STATE"),
        _row("01-600 Ketchikan No. 1", bm1, "NO", "", "ELECTION DAY", 368, "", "", "", "STATE"),
        _row("01-600 Ketchikan No. 1", q, "YES", "", "ELECTION DAY", 1, "", "", "", ""),
        _row("01-600 Ketchikan No. 1", q, "NO", "", "ELECTION DAY", 1, "", "", "", ""),
    )

    assert _votes(results) == {("", "Yes"): 103, ("", "No"): 368}


def test_tally_lines_are_not_candidates() -> None:
    # New York 2018 files these under each race's candidates.
    results = _results(
        _row("1", "US SENATE", "KIRSTEN E GILLIBRAND", "DEMOCRAT", "TOTAL", 5, "ALBANY", "36001"),
        *(
            _row("1", "US SENATE", line, "", "TOTAL", 5, "ALBANY", "36001")
            for line in ("VOIDS", "ELIGIBLE", "MANUALLY COUNTED EMERGENCY", "BLANK/VOID")
        ),
    )

    assert {r.candidate_name for r in results} == {"KIRSTEN E GILLIBRAND"}


def test_file_without_counties_still_gives_statewide_totals() -> None:
    # Wisconsin 2018 and Alaska 2022 have no county columns filled in.
    results = _results(
        _row("WARD 1", "US SENATE", "TAMMY BALDWIN", "DEMOCRAT", "TOTAL", 600, "", ""),
        _row("WARD 2", "US SENATE", "TAMMY BALDWIN", "DEMOCRAT", "TOTAL", 400, "", ""),
        _row(
            "WARD 1",
            "STATE ASSEMBLY",
            "ROBIN VOS",
            "REPUBLICAN",
            "TOTAL",
            9,
            "",
            "",
            "063",
            "STATE",
        ),
    )

    assert _votes(results) == {("", "TAMMY BALDWIN"): 1000}


def test_older_column_names() -> None:
    # Wisconsin 2018: "party" and "county" instead of party_detailed and county_name.
    header = (
        "precinct,office,party,mode,votes,county,candidate,district,dataverse,state_po,year,stage"
    )
    rows = [
        f"Ward {n},US Senate,{party},total,{votes},{county},{name},statewide,senate,WI,2018,gen"
        for n, party, votes, county, name in (
            (1, "democrat", 300, "Wood", "Tammy Baldwin"),
            (2, "republican", 200, "Adams", "Leah Vukmir"),
        )
    ]

    candidacies, results = parse_with_results([header, *rows])

    assert {(c.candidate_name, c.party, c.counties) for c in candidacies} == {
        ("Tammy Baldwin", "DEM", "*"),
        ("Leah Vukmir", "REP", "*"),
    }
    # No county codes, so the votes count statewide only.
    assert _votes(sorted(results)) == {("", "Tammy Baldwin"): 300, ("", "Leah Vukmir"): 200}


def test_na_is_not_a_county() -> None:
    # Rhode Island 2022 has US House rows in a county named "NA"; the
    # governor's race is still in every county.
    rows = [
        _row(
            "1",
            "GOVERNOR",
            "DANIEL J MCKEE",
            "DEMOCRAT",
            "TOTAL",
            5,
            "BRISTOL",
            "44001",
            "",
            "STATE",
        ),
        _row(
            "2", "GOVERNOR", "DANIEL J MCKEE", "DEMOCRAT", "TOTAL", 5, "KENT", "44003", "", "STATE"
        ),
        _row("3", "US HOUSE", "SETH MAGAZINER", "DEMOCRAT", "TOTAL", 5, "NA", "NA", "002", "HOUSE"),
    ]

    candidacies, results = parse_with_results([HEADER, *rows])

    assert {c.office: c.counties for c in candidacies}["GOVERNOR"] == "*"
    assert {r.county_fips for r in results} == {"44001", "44003"}


def _by_stage(results: list[CountyResult]) -> dict[tuple[str, str, str], int]:
    return {(r.stage, r.election_date, r.candidate_name): r.votes for r in results}


def test_runoff_votes_are_a_separate_contest() -> None:
    # From MEDSL's 2022 Georgia file: one precinct's absentee votes in the
    # November 8 general and the December 6 runoff for the same Senate seat.
    lines = fixture_bytes("medsl_runoff.csv").decode().splitlines()

    candidacies, results = parse_with_results(lines)

    # Before, the runoff's votes were added to the general election's.
    assert _by_stage(results) == {
        ("general", "2022-11-08", "RAPHAEL WARNOCK"): 295,
        ("general", "2022-11-08", "HERSCHEL JUNIOR WALKER"): 17,
        ("general", "2022-11-08", "CHASE OLIVER"): 1,
        ("runoff", "2022-12-06", "RAPHAEL WARNOCK"): 217,
        ("runoff", "2022-12-06", "HERSCHEL JUNIOR WALKER"): 11,
    }
    assert {r.office for r in results} == {"US Senate"}
    assert {(c.election_date, c.election_type) for c in candidacies} == {
        ("2022-11-08", "general"),
        ("2022-12-06", "runoff"),
    }


def test_recounts_are_not_added_to_the_count() -> None:
    # Arizona 2022: Cochise County's attorney general recount repeats its precincts.
    lines = [
        "precinct,district,office,writein,party_detailed,party_simplified,candidate,dataverse,mode,"
        "votes,state,year,stage,state_po,state_fips,state_cen,state_ic,date,jurisdiction_name,"
        "jurisdiction_fips,county_name,county_fips,readme_check,magnitude,special",
        *(
            f"01 BE BENSON,,ATTORNEY GENERAL,FALSE,DEMOCRAT,DEMOCRAT,KRIS MAYES,STATE,EARLY VOTING,"
            f"324,ARIZONA,2022,{stage},AZ,4,86,61,2022-11-08,COCHISE,04003,COCHISE,04003,TRUE,1,FALSE"
            for stage in ("GEN", "GEN RECOUNT")
        ),
        "1,,ATTORNEY GENERAL,FALSE,DEMOCRAT,DEMOCRAT,KRIS MAYES,STATE,TOTAL,555,ARIZONA,2022,GEN,"
        "AZ,4,86,61,2022-11-08,PIMA,04019,PIMA,04019,TRUE,1,FALSE",
    ]

    results = parse_with_results(lines)[1]

    assert _votes(sorted(results)) == {("04003", "KRIS MAYES"): 324, ("04019", "KRIS MAYES"): 555}


def test_primary_file() -> None:
    lines = fixture_bytes("medsl_primary.csv").decode().splitlines()

    results = parse_with_results(lines, primary_states=frozenset({"OK", "NV"}))[1]

    got = {(r.state, r.stage, r.party, r.candidate_name): r.votes for r in results}
    assert got == {
        # Each party's primary is its own race, and its runoff a primary runoff.
        ("OK", "primary", "DEM", "DREW EDMONDSON"): 119,
        ("OK", "primary", "DEM", "CONNIE JOHNSON"): 96,
        ("OK", "primary", "REP", "MICK CORNETT"): 25,
        ("OK", "primary", "REP", "KEVIN STITT"): 17,
        ("OK", "primary runoff", "REP", "KEVIN STITT"): 23,
        ("OK", "primary runoff", "REP", "MICK CORNETT"): 23,
        # A small party's race stays in though it got no votes here.
        ("OK", "primary runoff", "LIB", "CHRIS POWELL"): 0,
        ("OK", "primary runoff", "LIB", "REX L LAWHORN"): 0,
        # Nevada masks small counts as -1.
        ("NV", "primary", "DEM", "ASHEESH DEWAN"): 0,
    }
    assert {r.election_date for r in results if r.stage == "primary runoff"} == {"2018-08-28"}


def test_source_adds_primary_votes_but_not_primary_candidates() -> None:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("2018_precinct_primary.csv", fixture_bytes("medsl_primary.csv"))
    source = MedslSource(FakeWeb({"2018_precinct_primary.csv.zip": archive.getvalue()}))

    assert source.candidacies(2018) == []  # no general-election files here
    results = source.results(2018)

    assert {r.stage for r in results} == {"primary", "primary runoff"}
    assert "OK" in {r.state for r in results}
    assert "AZ" not in {r.state for r in results}  # not checked against certified totals


def test_write_csv_round_trip(tmp_path: Path) -> None:
    row = CountyResult(
        "DE",
        2024,
        "2024-11-05",
        "general",
        "GOVERNOR",
        "10001",
        "KENT",
        "MATT MEYER",
        "DEM",
        43179,
        "medsl",
    )
    path = results_path(tmp_path, "medsl", 2024)

    write_csv(path, [row])

    assert path == tmp_path / "results" / "medsl" / "2024.csv"
    assert path.read_text(encoding="utf-8").splitlines() == [
        ",".join(CountyResult.columns()),
        "DE,2024,2024-11-05,general,GOVERNOR,10001,KENT,MATT MEYER,DEM,43179,medsl",
    ]


# --- build_results -------------------------------------------------------------


def test_build_results(tmp_path: Path) -> None:
    out = tmp_path / "results"

    (built,) = build_results(FIXTURES / "site_data" / "results", out)

    assert built == {
        "source": "medsl",
        "year": 2024,
        "states": ["AK", "DE", "SD"],
        "national": ["US President", "Governor"],
    }
    assert (out / "2024.csv").exists()

    de = json.loads((out / "2024" / "DE.json").read_text(encoding="utf-8"))
    assert [r["office"] for r in de["races"]] == ["GOVERNOR", "US President"]
    president = de["races"][1]
    # Candidates in order of statewide votes; county votes follow that order.
    assert president["candidates"] == [
        ["KAMALA D HARRIS", "DEM", 289758],
        ["DONALD J TRUMP", "REP", 214351],
    ]
    assert president["counties"]["10005"] == ["SUSSEX", [64836, 81025]]

    national = json.loads((out / "2024" / "national.json").read_text(encoding="utf-8"))
    assert set(national["races"]) == {"US President", "Governor"}
    assert national["races"]["Governor"]["DE"]["office"] == "GOVERNOR"
    assert set(national["races"]["US President"]) == {"AK", "DE", "SD"}


def test_build_results_marks_measures_and_their_outcome(tmp_path: Path) -> None:
    fl3 = "AMENDMENT NO 3: ADULT PERSONAL USE OF MARIJUANA"
    wya = "CONSTITUTIONAL AMENDMENT A LOCAL GOVERNMENT INVESTMENT IN EQUITIES"
    path = results_path(tmp_path / "data", "medsl", 2024)
    write_csv(
        path,
        [
            # County totals from MEDSL's Florida 2024 and Wyoming 2022 files.
            CountyResult(
                "FL",
                2024,
                "2024-11-05",
                "general",
                fl3,
                "12001",
                "ALACHUA",
                "Yes",
                "",
                90219,
                "medsl",
            ),
            CountyResult(
                "FL",
                2024,
                "2024-11-05",
                "general",
                fl3,
                "12001",
                "ALACHUA",
                "No",
                "",
                45673,
                "medsl",
            ),
            CountyResult(
                "FL", 2024, "2024-11-05", "general", fl3, "12003", "BAKER", "Yes", "", 6513, "medsl"
            ),
            CountyResult(
                "FL", 2024, "2024-11-05", "general", fl3, "12003", "BAKER", "No", "", 8385, "medsl"
            ),
            CountyResult(
                "FL",
                2024,
                "2024-11-05",
                "general",
                "US President",
                "12003",
                "BAKER",
                "DONALD J TRUMP",
                "REP",
                13183,
                "medsl",
            ),
            CountyResult(
                "WY",
                2024,
                "2024-11-05",
                "general",
                wya,
                "56001",
                "ALBANY",
                "Yes",
                "",
                6734,
                "medsl",
            ),
            CountyResult(
                "WY", 2024, "2024-11-05", "general", wya, "56001", "ALBANY", "No", "", 5103, "medsl"
            ),
        ],
    )

    build_results(tmp_path / "data" / "results", tmp_path / "out")

    fl = json.loads((tmp_path / "out" / "2024" / "FL.json").read_text(encoding="utf-8"))
    measure, president = fl["races"]
    assert "measure" not in president
    assert measure["measure"] is True
    # Always Yes then No, so the map can read county votes as [yes, no].
    assert measure["candidates"] == [["Yes", "", 96732], ["No", "", 54058]]
    assert measure["counties"]["12003"] == ["BAKER", [6513, 8385]]
    # These two counties alone give Yes 64.1%, past Florida's 60%.
    assert (measure["outcome"], measure["needs"]) == ("passed", "60%")

    wy = json.loads((tmp_path / "out" / "2024" / "WY.json").read_text(encoding="utf-8"))
    (amendment,) = wy["races"]
    # Wyoming counts amendments against every ballot cast, which results don't hold.
    assert amendment["measure"] is True
    assert "outcome" not in amendment

    national = json.loads((tmp_path / "out" / "2024" / "national.json").read_text("utf-8"))
    assert national["measures"] == {"FL": [[fl3, 96732, 54058]], "WY": [[wya, 6734, 5103]]}
    assert set(national["races"]["US President"]) == {"FL"}


def test_build_results_draws_renamed_counties_on_the_map_shapes(tmp_path: Path) -> None:
    out = tmp_path / "results"
    build_results(FIXTURES / "site_data" / "results", out)

    ak = json.loads((out / "2024" / "AK.json").read_text(encoding="utf-8"))
    (president,) = ak["races"]
    # Chugach and Copper River are one shape, Valdez-Cordova, on the map.
    assert president["counties"]["02261"] == ["VALDEZ-CORDOVA", [973 + 660, 603 + 217]]
    # Votes that weren't reported by county stay in the statewide total.
    assert president["counties"][""] == ["", [84952, 74853]]
    assert president["candidates"][0] == ["DONALD J TRUMP", "REP", 84952 + 973 + 660]

    sd = json.loads((out / "2024" / "SD.json").read_text(encoding="utf-8"))
    assert sd["races"][0]["counties"]["46102"][0] == "OGLALA LAKOTA"


def test_map_counties_point_at_map_shapes() -> None:
    for code, (target, _) in MAP_COUNTIES.items():
        state = STATE_FIPS[target[:2]]
        shapes = json.loads((WEB / "geo" / "counties" / f"{state}.json").read_text("utf-8"))
        assert target in shapes["shapes"], code
        assert code not in shapes["shapes"], code


def test_build_results_counts_state_level_tallies_as_not_by_county(tmp_path: Path) -> None:
    # From MEDSL's 2018 Maine file: overseas and military ballots are tallied statewide.
    path = results_path(tmp_path / "data", "medsl", 2018)
    write_csv(
        path,
        [
            CountyResult(
                "ME",
                2018,
                "2018-11-06",
                "general",
                "GOVERNOR",
                "23000",
                "STATE UOCAVA",
                "JANET T MILLS",
                "DEM",
                1978,
                "medsl",
            ),
            CountyResult(
                "ME",
                2018,
                "2018-11-06",
                "general",
                "GOVERNOR",
                "23001",
                "ANDROSCOGGIN",
                "JANET T MILLS",
                "DEM",
                22906,
                "medsl",
            ),
        ],
    )

    build_results(tmp_path / "data" / "results", tmp_path / "out")

    me = json.loads((tmp_path / "out" / "2018" / "ME.json").read_text(encoding="utf-8"))
    assert me["races"][0]["counties"] == {"": ["", [1978]], "23001": ["ANDROSCOGGIN", [22906]]}


def test_build_results_draws_kansas_city_in_jackson_county(tmp_path: Path) -> None:
    # MEDSL's 2024 Missouri file: Kansas City's election board, which counts the
    # part of the city in Jackson County, reports under a code of its own.
    rows = [
        ("36000", "KANSAS CITY", "MIKE KEHOE", "REP", 26577),
        ("36000", "KANSAS CITY", "CRYSTAL QUADE", "DEM", 93969),
        ("29095", "JACKSON", "MIKE KEHOE", "REP", 97216),
        ("29095", "JACKSON", "CRYSTAL QUADE", "DEM", 88479),
        ("29047", "CLAY", "MIKE KEHOE", "REP", 67765),
        ("29047", "CLAY", "CRYSTAL QUADE", "DEM", 58365),
    ]
    write_csv(
        results_path(tmp_path / "data", "medsl", 2024),
        [
            CountyResult("MO", 2024, "2024-11-05", "general", "GOVERNOR", *row, "medsl")
            for row in rows
        ],
    )

    build_results(tmp_path / "data" / "results", tmp_path / "out")

    mo = json.loads((tmp_path / "out" / "2024" / "MO.json").read_text(encoding="utf-8"))
    assert mo["races"][0]["counties"] == {
        "29095": ["JACKSON", [88479 + 93969, 97216 + 26577]],  # Quade leads statewide
        "29047": ["CLAY", [58365, 67765]],
    }


def _result(stage: str, when: str, name: str, party: str, votes: int) -> CountyResult:
    return CountyResult(
        "GA", 2022, when, stage, "US Senate", "13021", "BIBB", name, party, votes, "x"
    )


def _ok(stage: str, when: str, name: str, party: str, votes: int) -> CountyResult:
    return CountyResult(
        "OK", 2018, when, stage, "GOVERNOR", "40019", "CARTER", name, party, votes, "x"
    )


def test_build_results_keeps_runoffs_and_primaries_apart(tmp_path: Path) -> None:
    rows = [
        _result("general", "2022-11-08", "RAPHAEL WARNOCK", "DEM", 1946117),
        _result("general", "2022-11-08", "HERSCHEL JUNIOR WALKER", "REP", 1908442),
        _result("runoff", "2022-12-06", "RAPHAEL WARNOCK", "DEM", 1820633),
        _result("runoff", "2022-12-06", "HERSCHEL JUNIOR WALKER", "REP", 1721244),
    ]
    primaries = [
        _ok("primary", "2018-06-26", "DREW EDMONDSON", "DEM", 242764),
        _ok("primary", "2018-06-26", "CONNIE JOHNSON", "DEM", 152730),
        _ok("primary", "2018-06-26", "MICK CORNETT", "REP", 132806),
        _ok("primary", "2018-06-26", "KEVIN STITT", "REP", 110479),
        _ok("primary runoff", "2018-08-28", "KEVIN STITT", "REP", 164892),
        _ok("primary runoff", "2018-08-28", "MICK CORNETT", "REP", 137316),
    ]
    write_csv(results_path(tmp_path / "data", "medsl", 2018), primaries)
    write_csv(results_path(tmp_path / "data", "medsl", 2022), rows)

    built = build_results(tmp_path / "data" / "results", tmp_path / "out")

    ga = json.loads((tmp_path / "out" / "2022" / "GA.json").read_text(encoding="utf-8"))
    assert [(r["stage"], r["date"]) for r in ga["races"]] == [
        ("general", "2022-11-08"),
        ("runoff", "2022-12-06"),
    ]
    assert ga["races"][0]["candidates"][0] == ["RAPHAEL WARNOCK", "DEM", 1946117]
    assert ga["races"][1]["candidates"] == [
        ["RAPHAEL WARNOCK", "DEM", 1820633],
        ["HERSCHEL JUNIOR WALKER", "REP", 1721244],
    ]
    ok = json.loads((tmp_path / "out" / "2018" / "OK.json").read_text(encoding="utf-8"))
    # Each party's primary is its own race.
    assert [(r["stage"], r["party"], [c[0] for c in r["candidates"]]) for r in ok["races"]] == [
        ("primary", "DEM", ["DREW EDMONDSON", "CONNIE JOHNSON"]),
        ("primary", "REP", ["MICK CORNETT", "KEVIN STITT"]),
        ("primary runoff", "REP", ["KEVIN STITT", "MICK CORNETT"]),
    ]
    # The national map shows general elections only.
    national = json.loads((tmp_path / "out" / "2022" / "national.json").read_text("utf-8"))
    assert national["races"]["US Senate"]["GA"]["candidates"][0][2] == 1946117
    assert [b["national"] for b in built] == [[], ["US Senate"]]


def test_build_results_reads_files_from_before_stages(tmp_path: Path) -> None:
    path = results_path(tmp_path / "data", "medsl", 2024)
    path.parent.mkdir(parents=True)
    path.write_text(
        "state,election_year,office,county_fips,county,candidate_name,party,votes,source\n"
        "DE,2024,GOVERNOR,10001,KENT,MATT MEYER,DEM,43179,medsl\n",
        encoding="utf-8",
    )

    build_results(tmp_path / "data" / "results", tmp_path / "out")

    de = json.loads((tmp_path / "out" / "2024" / "DE.json").read_text(encoding="utf-8"))
    assert [(r["stage"], r["date"], r["office"]) for r in de["races"]] == [
        ("general", "", "GOVERNOR")
    ]


def test_site_manifest_lists_results(tmp_path: Path) -> None:
    manifest = build(FIXTURES / "site_data", WEB, tmp_path / "_site")

    assert [r["year"] for r in manifest["results"]] == [2024]
    # Result CSVs aren't candidate files.
    assert {f["source"] for f in manifest["files"]} == {"fec", "nc"}
    assert (tmp_path / "_site" / "map.js").exists()
    assert (tmp_path / "_site" / "geo" / "states.json").exists()


# --- geo ------------------------------------------------------------------------

# Two unit squares sharing an edge, as us-atlas would encode them: arc 0 is the
# shared edge, and each state's ring runs along it in opposite directions.
TOPOLOGY: dict[str, Any] = {
    "type": "Topology",
    "transform": {"scale": [0.5, 0.5], "translate": [10, 20]},
    "arcs": [
        [[2, 0], [0, 2]],  # (11, 20) -> (11, 21)
        [[2, 2], [-2, 0], [0, -2], [2, 0]],  # (11, 21) -> (10, 21) -> (10, 20) -> (11, 20)
        [[2, 0], [2, 0], [0, 2], [-2, 0]],  # (11, 20) -> (12, 20) -> (12, 21) -> (11, 21)
    ],
    "objects": {
        "states": {
            "type": "GeometryCollection",
            "geometries": [
                {
                    "type": "Polygon",
                    "id": "10",
                    "properties": {"name": "Delaware"},
                    "arcs": [[0, 1]],
                },
                {
                    "type": "MultiPolygon",
                    "id": "24",
                    "properties": {"name": "Maryland"},
                    "arcs": [[[2, -1]]],
                },
            ],
        },
        "counties": {
            "type": "GeometryCollection",
            "geometries": [
                {
                    "type": "Polygon",
                    "id": "10001",
                    "properties": {"name": "Kent"},
                    "arcs": [[0, 1]],
                },
                {
                    "type": "Polygon",
                    "id": "24001",
                    "properties": {"name": "Allegany"},
                    "arcs": [[2, -1]],
                },
            ],
        },
    },
}


def test_decode_and_draw_rings() -> None:
    arcs = geo.decode_arcs(TOPOLOGY)
    assert arcs[0] == [(11.0, 20.0), (11.0, 21.0)]

    (west,) = geo.rings_of(TOPOLOGY["objects"]["states"]["geometries"][0], arcs)
    assert west == [(11, 20), (11, 21), (10, 21), (10, 20), (11, 20)]
    (east,) = geo.rings_of(TOPOLOGY["objects"]["states"]["geometries"][1], arcs)
    # A negative index walks the shared arc backwards.
    assert east == [(11, 20), (12, 20), (12, 21), (11, 21), (11, 20)]

    assert geo.svg_path([west], 1) == "M11 20l0 1 -1 0 0 -1 1 0z"
    # Points that round to the same spot are dropped; slivers with them.
    assert geo.svg_path([[(0, 0), (0.001, 0), (1, 1), (0, 0)]], 1) == "M0 0l1 1 -1 -1z"
    assert geo.svg_path([[(0, 0), (0.01, 0.01), (0, 0)]], 1) == ""
    assert geo.bounds([west]) == [9.98, 19.98, 1.04, 1.04]


def test_build_geo_writes_states_and_counties(tmp_path: Path) -> None:
    web = FakeWeb({"/counties-albers-10m.json": json.dumps(TOPOLOGY).encode()})

    assert geo.fetch_and_build(web, tmp_path) == 2

    states = json.loads((tmp_path / "states.json").read_text(encoding="utf-8"))
    assert states["shapes"]["10"] == {
        "name": "Delaware",
        "abbr": "DE",
        "d": "M11 20l0 1 -1 0 0 -1 1 0z",
    }
    md = json.loads((tmp_path / "counties" / "MD.json").read_text(encoding="utf-8"))
    assert list(md["shapes"]) == ["24001"]
    assert md["shapes"]["24001"]["name"] == "Allegany"
    assert md["outline"] == "M11 20l1 0 0 1 -1 0 0 -1z"


def test_governor_spellings() -> None:
    (governor,) = NATIONAL_RACES["Governor"]
    for office in ("GOVERNOR", "GOVERNOR/LIEUTENANT GOVERNOR", "Governor / Lt. Governor"):
        assert governor.match(office), office
    assert not governor.match("MEMBER OF THE WAYNE STATE UNIVERSITY BOARD OF GOVERNORS")
    assert not governor.match("LIEUTENANT GOVERNOR")


def test_dc_shadow_senator_is_not_a_senate_race() -> None:
    row = (
        "1,US SENATE,DEMOCRAT,DEMOCRAT,TOTAL,5,DISTRICT OF COLUMBIA,11001,DISTRICT OF COLUMBIA,"
        "11001,MICHAEL D BROWN,STATEWIDE,SENATE,2018,GEN,DISTRICT OF COLUMBIA,FALSE,FALSE,DC,11,"
        "53,55,2018-11-06,1"
    )

    (candidacy,), (result,) = parse_with_results([HEADER, row])

    assert candidacy.office == result.office == "US Senate (shadow)"
    assert not any(p.match(result.office) for p in NATIONAL_RACES["US Senate"])


def test_dc_house_member_is_a_delegate() -> None:
    # A real row of MEDSL's 2018 DC file, with its own header.
    header = (
        '"precinct","office","party_detailed","party_simplified","mode","votes","county_name",'
        '"county_fips","jurisdiction_name","jurisdiction_fips","candidate","district","magnitude",'
        '"dataverse","year","stage","state","special","writein","state_po","state_fips",'
        '"state_cen","state_ic","date","readme_check"'
    )
    row = (
        '"1","DELEGATE TO THE US HOUSE","DEMOCRAT","DEMOCRAT","TOTAL",2390,"DISTRICT OF COLUMBIA",'
        '"11001","WARD 6","11001","ELEANOR HOLMES NORTON","000",1.0,"HOUSE",2018,"GEN",'
        '"DISTRICT OF COLUMBIA","FALSE","FALSE","DC","11","53","55","2018-11-06","FALSE"'
    )

    (candidacy,), (result,) = parse_with_results([header, row])

    assert candidacy.office == result.office == "US House Delegate"


def test_senate_races_have_no_district() -> None:
    # Arizona 2024 files one county's US Senate rows under district "007".
    results = _results(
        _row(
            "1", "US SENATE", "RUBEN GALLEGO", "DEMOCRAT", "TOTAL", 5, "PIMA", "4019", "", "SENATE"
        ),
        _row(
            "2",
            "US SENATE",
            "RUBEN GALLEGO",
            "DEMOCRAT",
            "TOTAL",
            7,
            "SANTA CRUZ",
            "4023",
            "007",
            "SENATE",
        ),
    )

    assert _votes(results) == {("04019", "RUBEN GALLEGO"): 5, ("04023", "RUBEN GALLEGO"): 7}
