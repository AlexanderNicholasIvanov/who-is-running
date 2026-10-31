"""Ranked-choice round-by-round tallies from Alaska's and Maine's official tabulations.

Alaska (since 2022) and Maine (since 2018) count some races by ranked-choice
voting: if nobody has a majority of first choices, the last-place candidate is
eliminated and their ballots move to each voter's next choice, round by round.
MEDSL's precinct results hold only first choices, so the eventual winner can
differ from the first-round leader (Maine's 2nd District in 2018, for example).

Both states publish each tabulated race's rounds as a separate report, in
whatever format that year's software or staff produced:

- Alaska: the Division of Elections' "RCV Detailed Report" for every race
  that went past round 1, as tab-separated text (2022) or PDF (2024).
  https://www.elections.alaska.gov/election-results/
- Maine: the Secretary of State's "Summary Report" spreadsheet, as ``.xls``
  (2018, 2020) or a PDF printout of it (2022). Maine's 2024 summary is a
  scanned image with no text, so it isn't included.
  https://www.maine.gov/sos/elections-voting/election-results-data

The report URLs are listed in ``REPORTS``, because neither state publishes a
machine-readable index. Office and district are spelled the way MEDSL spells
them, so the site can match a tabulation to the race it decided. Races decided
in round 1 have no report and aren't listed.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from who_is_running import pdf, xls
from who_is_running.http import NotFound
from who_is_running.models import Candidacy
from who_is_running.rounds import EXHAUSTED, RcvRound
from who_is_running.sources.base import Source
from who_is_running.text import decode

log = logging.getLogger(__name__)

AK = "https://www.elections.alaska.gov/results/"
ME = "https://www.maine.gov/sos/sites/maine.gov.sos/files/"


@dataclass(frozen=True)
class Report:
    state: str
    election_date: str
    election_type: str
    office: str
    district: str
    url: str
    party: str = ""
    """For a party primary, the party every candidate ran in."""


def _ak(when: str, path: str, office: str, district: str = "") -> Report:
    return Report("AK", when, "general", office, district, AK + path)


def _me(when: str, kind: str, path: str, office: str, district: str, party: str = "") -> Report:
    return Report("ME", when, kind, office, district, ME + path, party)


AK22, AK22S, AK24 = "2022-11-08", "2022-08-16", "2024-11-05"
ME18P, ME18, ME20P, ME22 = "2018-06-12", "2018-11-06", "2020-07-14", "2022-11-08"
REPORTS: dict[int, list[Report]] = {
    2018: [
        _me(ME18P, "primary", "content/assets/democraticgovfinal.xls", "GOVERNOR", "", "DEM"),
        _me(ME18P, "primary", "content/assets/Democraticcgfinal.xls", "US House", "02", "DEM"),
        # Replaced the first summary (SummaryReport-CongressionalDistrict2.xls)
        # once every ballot was in.
        _me(ME18, "general", "content/assets/updated-summary-report-CD2.xls", "US House", "02"),
    ],
    2020: [
        _me(ME20P, "primary", "content/assets/offcg2summary.xls", "US House", "02", "REP"),
        _me(ME20P, "primary", "content/assets/offss11summary.xls", "STATE SENATE", "11", "DEM"),
        _me(ME20P, "primary", "content/assets/offsr41summary.xls", "STATE HOUSE", "41", "DEM"),
        _me(ME20P, "primary", "content/assets/offsr47summary.xls", "STATE HOUSE", "47", "DEM"),
        _me(ME20P, "primary", "content/assets/offsr49summary.xls", "STATE HOUSE", "49", "DEM"),
        _me(ME20P, "primary", "content/assets/offsr90summary.xls", "STATE HOUSE", "90", "DEM"),
    ],
    2022: [
        _ak(AK22S, "22SSPG/RcvDetailedReport.txt", "US House (special)"),
        _ak(AK22, "22GENR/US%20SEN.txt", "US Senate"),
        _ak(AK22, "22GENR/US%20REP.txt", "US House"),
        _ak(AK22, "22GENR/11.txt", "STATE HOUSE", "11"),
        _ak(AK22, "22GENR/15_recount_rcv.txt", "STATE HOUSE", "15"),  # after the recount
        _ak(AK22, "22GENR/18.txt", "STATE HOUSE", "18"),
        _ak(AK22, "22GENR/28.pdf", "STATE HOUSE", "28"),  # no text version
        _ak(AK22, "22GENR/30.txt", "STATE HOUSE", "30"),
        _ak(AK22, "22GENR/31.txt", "STATE HOUSE", "31"),
        _ak(AK22, "22GENR/34.txt", "STATE HOUSE", "34"),
        _ak(AK22, "22GENR/D.txt", "STATE SENATE", "D"),
        _ak(AK22, "22GENR/E_recount_rcv.txt", "STATE SENATE", "E"),  # after the recount
        _ak(AK22, "22GENR/N.txt", "STATE SENATE", "N"),
        _me(ME22, "general", "inline-files/Rep%20to%20Congress%20Dist%202%20RCV%20results.pdf",
            "US House", "02"),
    ],
    2024: [
        _ak(AK24, "24GENR/RCV-USRep.pdf", "US House"),
        _ak(AK24, "24GENR/RCV-HD6.pdf", "STATE HOUSE", "06"),
        _ak(AK24, "24GENR/RCV-HD28.pdf", "STATE HOUSE", "28"),
        _ak(AK24, "24GENR/RCV-HD36.pdf", "STATE HOUSE", "36"),
        _ak(AK24, "24GENR/RCV-HD38.pdf", "STATE HOUSE", "38"),
        _ak(AK24, "24GENR/RCV-HD40.pdf", "STATE HOUSE", "40"),
        _ak(AK24, "24GENR/RCV-SenD.pdf", "STATE SENATE", "D"),
        _ak(AK24, "24GENR/RCV-SenF.pdf", "STATE SENATE", "F"),
        _ak(AK24, "24GENR/RCV-SenL.pdf", "STATE SENATE", "L"),
    ],
}  # fmt: skip

# Lines in the reports that count ballots rather than a candidate's votes.
NOT_CANDIDATES = {"BLANKS", "EXHAUSTED", "OVERVOTES", "UNDERVOTES", "REMAINDER POINTS"}
# Maine 2018's general-election summary puts the party before the name: "DEM Golden, Jared F.".
PARTY_PREFIX = re.compile(r"^([A-Z]{3}) (?=\S.*,)")
ROUND = re.compile(r"^Round\s*(\d+)$")
VOTES = re.compile(r"^-?[\d,]+(\.0+)?$")

Tally = dict[int, dict[str, int]]
"""round -> candidate -> votes, candidates in the report's order."""


class RcvSource(Source):
    name = "rcv"
    description = (
        "Ranked-choice rounds from the Alaska Division of Elections and Maine Secretary of State"
    )

    def available_years(self) -> list[int]:
        return sorted(REPORTS)

    def refresh_years(self, today: date) -> list[int]:
        # Certified past results. New elections need their report URLs added to REPORTS.
        return []

    def candidacies(self, year: int) -> list[Candidacy]:
        # Who ran is already in MEDSL; this source only adds the rounds.
        return []

    def rounds(self, year: int) -> list[RcvRound]:
        out: list[RcvRound] = []
        for report in REPORTS.get(year, []):
            try:
                raw = self.fetch(report.url)
            except NotFound:
                log.warning("rcv: missing %s", report.url)
                continue
            tally = parse_report(report.url, raw)
            if not tally:
                log.warning("rcv: no rounds found in %s", report.url)
                continue
            out.extend(to_rounds(report, tally))
        return sorted(out)


def parse_report(url: str, raw: bytes) -> Tally:
    path = url.lower()
    if path.endswith(".xls"):
        return parse_summary(xls_lines(raw))
    if path.endswith(".txt"):
        return parse_dominion_txt(decode(raw))
    lines = [line for page in pdf.pages(raw) for line in page]
    if any(line and line[0] == "Candidate Names" for line in lines):
        return parse_summary(lines)
    return parse_dominion_lines(lines)


def parse_dominion_txt(text: str) -> Tally:
    """Alaska's RCV Detailed Report exported as text: one row per choice per round.

    Columns 2-4 are round ("Round 1"), choice name and votes ("114,118"); the
    same choices repeat further right (choiceName3), which this ignores.
    """
    tally: Tally = defaultdict(dict)
    for row in csv.reader(io.StringIO(text), delimiter="\t"):
        if len(row) < 4 or not (m := ROUND.match(row[1].strip())):
            continue
        name, votes = row[2].strip(), row[3].strip()
        if name and name.upper() not in NOT_CANDIDATES and VOTES.match(votes):
            tally[int(m.group(1))][name] = _votes(votes)
    return dict(tally)


def parse_dominion_lines(lines: list[list[str]]) -> Tally:
    """The same report as PDF.

    Each round is a "Round | N" line, a "Candidate | Votes | Percentage"
    header, then "Name | votes | percent" lines up to "Continuing Ballots
    Total". A percentage is occasionally drawn in a font we can't read, so
    it's not required.
    """
    tally: Tally = defaultdict(dict)
    current, in_block = 0, False
    for line in lines:
        if len(line) == 2 and line[0] == "Round" and line[1].isdigit():
            current, in_block = int(line[1]), False
        elif current and line[:1] == ["Candidate"]:
            in_block = True
        elif in_block and line[:1] == ["Continuing Ballots Total"]:
            in_block = False
        elif (
            in_block
            and len(line) in (2, 3)
            and VOTES.match(line[1])
            and line[0].upper() not in NOT_CANDIDATES
        ):
            tally[current][line[0]] = _votes(line[1])
    return dict(tally)


def parse_summary(lines: list[list[str]]) -> Tally:
    """Maine's Summary Report: a row per candidate, (votes, transfer) per round.

    Percentage cells are dropped first (the PDF printout has them as "48.38%"),
    so a candidate row reads name, votes, transfer, votes, transfer, ...
    Candidate rows run from the "Candidate Names" header to "Ballot Exhausted".
    """
    rounds = 0
    tally: Tally = defaultdict(dict)
    in_table = False
    for line in lines:
        cells = [c for c in line if c and not c.endswith("%")]
        if not cells:
            continue
        label = cells[0].strip()
        if not in_table:
            found = [int(m.group(1)) for c in cells if (m := ROUND.match(c.strip()))]
            rounds = max([rounds, *found])
            in_table = label == "Candidate Names"
            continue
        if label.startswith("Ballot Exhausted") or label.startswith("Continuing Ballots"):
            break
        numbers = cells[1:]
        if not all(VOTES.match(n) for n in numbers):
            continue
        votes = [_votes(n) for n in numbers[0::2]][:rounds]
        if len(votes) != rounds:
            log.warning("rcv: %s has %d of %d rounds", label, len(votes), rounds)
            continue
        for n, v in enumerate(votes, start=1):
            tally[n][label] = v
    return dict(tally)


def xls_lines(raw: bytes) -> list[list[str]]:
    """The first sheet as text rows, without its Percentage columns (fractions, not "%")."""
    sheets = xls.read_sheets(raw)
    if not sheets:
        return []
    grid = xls.rows(sheets[0][1])
    header = next((r for r in grid if r and r[0] == "Candidate Names"), [])
    skip = {i for i, h in enumerate(header) if h == "Percentage"}
    return [[_cell(c) for i, c in enumerate(r) if i not in skip] for r in grid]


def _cell(value: xls.Cell) -> str:
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    return value.strip() if value.strip() else ""


def _votes(raw: str) -> int:
    return int(float(raw.replace(",", "")))


def to_rounds(report: Report, tally: Tally) -> list[RcvRound]:
    """Rows for each candidate in each round, plus each round's exhausted ballots.

    Exhausted = ballots counted for a candidate in round 1 that count for no
    one in this round. That equals the difference in candidates' total votes,
    which is how both states' reports add up.
    """
    first = sum(tally[min(tally)].values())
    year = int(report.election_date[:4])
    out = []
    for number, votes in sorted(tally.items()):
        for name, count in votes.items():
            party = report.party
            if m := PARTY_PREFIX.match(name):
                party, name = party or m.group(1), name[m.end() :]
            if name.upper().startswith("WRITE-IN"):
                party = ""  # write-ins taken together, not a candidate of the primary's party
            out.append(_row(report, year, number, name, party, count))
        out.append(_row(report, year, number, EXHAUSTED, "", first - sum(votes.values())))
    return out


def _row(report: Report, year: int, number: int, name: str, party: str, votes: int) -> RcvRound:
    return RcvRound(
        state=report.state,
        election_year=year,
        election_date=report.election_date,
        election_type=report.election_type,
        office=report.office,
        district=report.district,
        round=number,
        candidate_name=name,
        party=party,
        votes=votes,
        source="rcv",
    )
