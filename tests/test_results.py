from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import FIXTURES, FakeWeb, fixture_bytes

from who_is_running import geo
from who_is_running.build_site import MAP_COUNTIES, NATIONAL_RACES, build, build_results
from who_is_running.districts import STATE_FIPS
from who_is_running.results import CountyResult, results_path, write_csv
from who_is_running.sources.medsl import parse_with_results

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


def test_ballot_question_answers_are_not_candidates() -> None:
    results = _results(
        _row("1", "INITIATIVE 83", "YES TO APPROVE", "", "TOTAL", 5, "KENAI", "2122", "", "STATE"),
        _row("2", "INITIATIVE 83", "NO TO REJECT", "", "TOTAL", 5, "NOME", "2180", "", "STATE"),
    )

    assert results == []


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
    header = "precinct,office,party,mode,votes,county,candidate,district,dataverse,state_po,year"
    rows = [
        f"Ward {n},US Senate,{party},total,{votes},{county},{name},statewide,senate,WI,2018"
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


def test_write_csv_round_trip(tmp_path: Path) -> None:
    row = CountyResult("DE", 2024, "GOVERNOR", "10001", "KENT", "MATT MEYER", "DEM", 43179, "medsl")
    path = results_path(tmp_path, "medsl", 2024)

    write_csv(path, [row])

    assert path == tmp_path / "results" / "medsl" / "2024.csv"
    assert path.read_text(encoding="utf-8").splitlines() == [
        ",".join(CountyResult.columns()),
        "DE,2024,GOVERNOR,10001,KENT,MATT MEYER,DEM,43179,medsl",
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
