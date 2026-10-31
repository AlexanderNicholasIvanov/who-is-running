"""Ranked-choice rounds from Alaska's and Maine's official tabulation reports.

Fixtures are the real report files. The PDFs are trimmed to their pages' text
(the same content streams, without the embedded fonts) to keep them small.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from conftest import FakeWeb, fixture_bytes

from who_is_running import xls
from who_is_running.build_site import build_rounds
from who_is_running.pipeline import run
from who_is_running.rounds import EXHAUSTED, RcvRound, rounds_path, write_csv
from who_is_running.sources.rcv import REPORTS, RcvSource, parse_report
from who_is_running.text import decode


def _final(rows: list[RcvRound], office: str, district: str = "") -> dict[str, int]:
    race = [r for r in rows if r.office == office and r.district == district]
    last = max(r.round for r in race)
    return {r.candidate_name: r.votes for r in race if r.round == last and r.votes}


def _source(year: int, routes: dict[str, str]) -> RcvSource:
    return RcvSource(FakeWeb({url: fixture_bytes(name) for url, name in routes.items()}))


def test_alaska_text_report_2022_senate() -> None:
    # UTF-16 text export of the RCV Detailed Report.
    rows = _source(2022, {"22GENR/US%20SEN.txt": "rcv_ak_2022_us_sen.txt"}).rounds(2022)

    assert {r.round for r in rows} == {1, 2, 3}
    assert _final(rows, "US Senate") == {
        "Murkowski, Lisa": 136330,
        "Tshibaka, Kelly C.": 117534,
        EXHAUSTED: 9163,  # 9,107 exhausted + 56 more overvotes than in round 1
    }
    first = {r.candidate_name: r.votes for r in rows if r.round == 1}
    assert first == {
        "Chesbro, Patricia R.": 28233,
        "Kelley, Buzz A.": 8575,
        "Murkowski, Lisa": 114118,
        "Tshibaka, Kelly C.": 112101,
        EXHAUSTED: 0,
    }
    senate = next(r for r in rows if r.candidate_name == "Murkowski, Lisa")
    assert (senate.state, senate.election_date, senate.election_type) == (
        "AK",
        "2022-11-08",
        "general",
    )
    assert senate.party == ""  # the report doesn't say; left empty


def test_alaska_text_report_in_utf8() -> None:
    # One 2022 report (House District 18) was exported as UTF-8 instead.
    tally = parse_report("https://x/22GENR/18.txt", fixture_bytes("rcv_ak_2022_hd18.txt"))

    assert tally[2] == {"Franks, Lyn D.": 0, "Nelson, David": 968, "Groh, Cliff": 1045}


def test_alaska_pdf_report_2024_house() -> None:
    rows = _source(2024, {"RCV-USRep.pdf": "rcv_ak_2024_us_rep.pdf"}).rounds(2024)

    assert _final(rows, "US House") == {
        "Begich, Nick": 164861,
        "Peltola, Mary S.": 156985,
        EXHAUSTED: 7647,
    }
    assert {r.candidate_name: r.votes for r in rows if r.round == 1} == {
        "Begich, Nick": 159777,
        "Hafner, Eric": 3558,
        "Howe, John Wayne": 13210,
        "Peltola, Mary S.": 152948,
        EXHAUSTED: 0,
    }


def test_alaska_pdf_line_without_a_readable_percentage() -> None:
    # Kowalski's percentage is drawn in a font we can't read; his votes still count.
    tally = parse_report("https://x/RCV-HD36.pdf", fixture_bytes("rcv_ak_2024_hd36.pdf"))

    assert tally[3] == {
        "Goode, Pamela": 0,
        "Kowalski, Brandon P.": 3155,
        "Fields, James": 0,
        "Schwanke, Rebecca A.": 4070,
    }


def test_maine_xls_summary_2018_cd2() -> None:
    rows = _source(2018, {"updated-summary-report-CD2.xls": "rcv_me_2018_cd2.xls"}).rounds(2018)

    assert _final(rows, "US House", "02") == {
        "Golden, Jared F.": 142440,
        "Poliquin, Bruce": 138931,
        EXHAUSTED: 8253,
    }
    # The 2018 summary puts party codes before the names.
    parties = {r.candidate_name: r.party for r in rows if r.round == 1}
    assert parties == {
        "Bond, Tiffany L.": "",
        "Golden, Jared F.": "DEM",
        "Hoar, William R.S.": "",
        "Poliquin, Bruce": "REP",
        EXHAUSTED: "",
    }
    assert {r.election_type for r in rows} == {"general"}


def test_maine_primary_gives_every_candidate_the_party() -> None:
    # A small workbook, so it's stored in the compound file's mini stream.
    rows = _source(2020, {"offss11summary.xls": "rcv_me_2020_ss11.xls"}).rounds(2020)

    assert _final(rows, "STATE SENATE", "11") == {
        "Curry, Glenn Chip": 2807,
        "Stanicki, Robyn M.": 2212,
        EXHAUSTED: 260,
    }
    assert {r.party for r in rows if r.candidate_name != EXHAUSTED} == {"DEM"}
    assert {r.election_type for r in rows} == {"primary"}


def test_maine_pdf_printout_2022_cd2() -> None:
    url = "Rep%20to%20Congress%20Dist%202%20RCV%20results.pdf"
    rows = _source(2022, {url: "rcv_me_2022_cd2.pdf"}).rounds(2022)

    assert _final(rows, "US House", "02") == {
        "Golden, Jared Forrest": 165136,
        "Poliquin, Bruce": 146142,
        EXHAUSTED: 5104,  # 68 + 3,708 + 1,328 newly exhausted in round 2
    }
    assert {r.candidate_name for r in rows if r.round == 1} == {
        "Bond, Tiffany",
        "Golden, Jared Forrest",
        "Poliquin, Bruce",
        "Write-in",
        EXHAUSTED,
    }


def test_missing_reports_are_skipped() -> None:
    web = FakeWeb({"22GENR/US%20SEN.txt": fixture_bytes("rcv_ak_2022_us_sen.txt")})

    rows = RcvSource(web).rounds(2022)

    assert {r.office for r in rows} == {"US Senate"}
    assert len(web.requested) == len(REPORTS[2022])


def test_xls_reads_cells() -> None:
    ((name, sheet),) = xls.read_sheets(fixture_bytes("rcv_me_2018_cd2.xls"))

    grid = [row for row in xls.rows(sheet) if any(row)]
    assert name == "RCV Report"
    assert grid[3][:2] == ["Office Title", "Congressional District 2"]
    assert grid[7][:2] == ["DEM Golden, Jared F.", 132013.0]


def test_decode_reads_utf16_with_bom() -> None:
    assert decode("Round 1\tMurkowski".encode("utf-16")) == "Round 1\tMurkowski"


def test_pipeline_writes_rounds(tmp_path: Path) -> None:
    source = _source(2018, {"updated-summary-report-CD2.xls": "rcv_me_2018_cd2.xls"})

    assert run(source, [2018], tmp_path) == {}  # no candidacies of its own

    path = rounds_path(tmp_path, "rcv", 2018)
    assert path == tmp_path / "rounds" / "rcv" / "2018.csv"
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == RcvRound.columns()
    assert rows[-1][:7] == ["ME", "2018", "2018-11-06", "general", "US House", "02", "2"]
    assert not (tmp_path / "rcv").exists()


def test_build_rounds_for_the_site(tmp_path: Path) -> None:
    rows = _source(2024, {"RCV-USRep.pdf": "rcv_ak_2024_us_rep.pdf"}).rounds(2024)
    write_csv(rounds_path(tmp_path / "data", "rcv", 2024), rows)
    out = tmp_path / "results"
    # build_results has already written the state's first-choice results (names as MEDSL has them).
    (out / "2024").mkdir(parents=True)
    first_choices = [
        ["NICK BEGICH", "REP", 159777],
        ["MARY S PELTOLA", "DEM", 152948],
        ["JOHN WAYNE HOWE", "ALASKAN INDEPENDENCE", 13210],
        ["ERIC HAFNER", "DEM", 3558],
    ]
    (out / "2024" / "AK.json").write_text(
        json.dumps({"races": [{"office": "US House", "candidates": first_choices}]}),
        encoding="utf-8",
    )

    built = build_rounds(tmp_path / "data" / "rounds", out)

    assert built == [{"year": 2024, "states": ["AK"]}]
    (race,) = json.loads((out / "2024" / "rcv.json").read_text(encoding="utf-8"))["races"]
    assert race["office"] == "US House"
    assert race["exhausted"] == [0, 1317, 7647]
    # Winner first, parties matched by surname to the first-choice results.
    assert race["candidates"] == [
        ["Begich, Nick", "REP", [159777, 160044, 164861]],
        ["Peltola, Mary S.", "DEM", [152948, 154261, 156985]],
        ["Howe, John Wayne", "ALASKAN INDEPENDENCE", [13210, 13871, 0]],
        ["Hafner, Eric", "DEM", [3558, 0, 0]],
    ]
    assert (out / "2024" / "rcv.csv").exists()
