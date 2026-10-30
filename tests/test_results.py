from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import FIXTURES, FakeWeb, fixture_bytes

from who_is_running import geo
from who_is_running.build_site import build, build_results
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
