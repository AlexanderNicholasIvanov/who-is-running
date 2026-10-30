"""Every state's general-election ballot, from MIT Election Data + Science Lab results.

MEDSL publishes precinct-level returns for all 50 states and DC in one schema,
covering federal, state and (where the state reports them) local offices. We
reduce each file to the unique candidates per race, so this is who *appeared
on the ballot* in a past November general election, not who filed to run.

https://github.com/MEDSL  (one repository per election year)

Coverage is uneven: some states report no local races in some years (e.g.
California 2018), and a few files carry only surnames (Texas 2022, 2024). Write-ins,
ballot questions and turnout lines are dropped. 2020 is only on Harvard
Dataverse, behind an interactive download, so it isn't included.
"""

from __future__ import annotations

import csv
import html
import io
import logging
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import date

from who_is_running.http import NotFound
from who_is_running.models import STATEWIDE, Candidacy, Level, join_counties
from who_is_running.sources.base import Source
from who_is_running.text import decode_lines

log = logging.getLogger(__name__)

RAW = "https://raw.githubusercontent.com/MEDSL/{year}-elections-official/HEAD/individual_states"
FILES = {
    2018: "2018-{st}-precinct-general.zip",
    2022: "2022-{st}-local-precinct-general.zip",
    2024: "{st}24.zip",
}
STATES = [
    *("AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA"),
    *("ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS"),
    *("MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA"),
    *("RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY"),
]

FEDERAL = {"PRESIDENT": "US President", "SENATE": "US Senate", "HOUSE": "US House"}
STAGES = {"GEN": "general", "PRI": "primary", "RUN": "runoff", "RUNOFF": "runoff"}
PARTIES = {
    "DEMOCRAT": "DEM",
    "REPUBLICAN": "REP",
    "LIBERTARIAN": "LIB",
    "GREEN": "GRE",
    "INDEPENDENT": "IND",
}
NO_PARTY = {"", '""', "NONPARTISAN", "NON", "OTHER", "NONE", "NO"}

# Turnout, over/undervote and write-in lines that some states report as if
# they were candidates (sometimes per candidate: "JANE DOE - OVERVOTES"), and
# straight-ticket lines named after a party.
NOT_CANDIDATES = re.compile(
    r"^$|^(YES|NO|FOR|AGAINST|APPROVE|REJECT|REJECTED|VOID|SPOILED|EXHAUSTED|NONE|TOTAL|"
    r"BLANKS?|BLANK/VOID|ALL OTHERS|OTHERS|NONE OF THESE CANDIDATES|SCATTER(ING|ED)?|"
    r"TURNOUT|SPANISH SURNAME TURNOUT|AFFIDAVIT|FEDERAL|PUBLIC COUNTER|ABSENTEE / MILITARY|"
    r"OTHER|NO VOTE|NO CANDIDATE FILED|CONTEST TOTAL|"
    r"DEM|REP|LIB|DEMOCRAT|DEMOCRATIC|REPUBLICAN|LIBERTARIAN)$|"
    r"\b(VOTES|VOTERS|BALLOTS|VOTER REGISTRATION|TIMES BLANK VOTED|OVER ?VOTES?|UNDER ?VOTES?|"
    r"WRITE[- ]?INS?|STRAIGHT (TICKET|PARTY))\b"
)
# Ballot questions and ticket/turnout lines filed under an "office".
NOT_OFFICES = re.compile(
    r"^(STRAIGHT[ -](PARTY|TICKET)|TOTAL |REGISTERED VOTERS|BALLOTS (CAST|COUNTED)|\d+\. |"
    r"PROPOSED )|"
    r"\b(QUESTION|PROPOSITION|AMENDMENTS?|AMENDS|BONDS|REFERENDUM|MEASURE|ISSUE|"
    r"ADVISORY (VOTE|NO)|BOND PROCEEDS)\b|"
    # "... OVERRIDE LEVY", "... REVENUE BOND", but not Delaware's LEVY COURT or Bond County.
    r"\b(LEVY|BOND)$"
)
US_DATE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})$")
# Rows with no dataverse (a few files leave it blank) are local unless the
# office is plainly a state one.
STATE_OFFICES = re.compile(
    r"^(GOVERNOR|LIEUTENANT GOVERNOR|ATTORNEY GENERAL|SECRETARY OF STATE|STATE |COMPTROLLER|"
    r"LAND COMMISSIONER|AGRICULTURE COMMISSIONER|RAILROAD COMMISSIONER|INSURANCE COMMISSIONER|"
    r"SUPERINTENDENT OF PUBLIC INSTRUCTION|JUSTICE OF THE SUPREME COURT|SUPREME COURT|"
    r"COURT OF CRIMINAL APPEALS|COURT OF APPEALS|CHIEF JUSTICE|DISTRICT COURT JUDGE|"
    r"DISTRICT ATTORNEY|BOARD OF EDUCATION|STATE BOARD)"
)
# New England runs local government by town, so a town is the jurisdiction there.
TOWN_STATES = {"CT", "MA", "ME", "NH", "RI", "VT"}


class MedslSource(Source):
    name = "medsl"
    description = (
        "MIT Election Lab precinct results: every candidate on the November ballot, "
        "all states (2018, 2022, 2024)"
    )

    def available_years(self) -> list[int]:
        return sorted(FILES)

    def refresh_years(self, today: date) -> list[int]:
        # Past elections, revised rarely. Backfill with `fetch --all-years --source medsl`.
        return []

    def candidacies(self, year: int) -> list[Candidacy]:
        if year not in FILES:
            return []
        out: list[Candidacy] = []
        for st in STATES:
            url = f"{RAW.format(year=year)}/{FILES[year].format(st=st.lower())}"
            try:
                archive = self.fetch(url)
            except NotFound:
                log.warning("medsl: no %d file for %s", year, st)
                continue
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                for member in zf.namelist():
                    if member.lower().endswith(".csv") and not member.startswith("__MACOSX"):
                        with zf.open(member) as fh:
                            out.extend(parse(decode_lines(fh)))
        return sorted(out)


def parse(lines: Iterable[str]) -> list[Candidacy]:
    """Reduce precinct rows (header first) to one Candidacy per candidate per race.

    A candidate on several party lines of the same race (fusion voting in
    New York, Connecticut, ...) becomes one row with the parties joined by "/".
    Each race records the counties its precincts are in, or "*" when that is
    every county in the file (one file is one state).
    """
    parties: dict[Candidacy, list[str]] = {}
    race_counties: defaultdict[Candidacy, set[str]] = defaultdict(set)
    all_counties: set[str] = set()
    reader = csv.reader(lines)
    header = next(reader, [])
    for row in reader:
        r = dict(zip(header, row, strict=False))
        if r.get("writein", "").upper() == "TRUE":
            continue
        # A few files carry HTML entities ("SCHOOLS&#39;").
        candidate = " ".join(html.unescape(r.get("candidate", "")).split())
        office = " ".join(html.unescape(r.get("office", "")).split())
        if not office or NOT_CANDIDATES.search(candidate.upper()) or NOT_OFFICES.search(office):
            continue
        dataverse = r.get("dataverse", "").upper()
        level = level_of(dataverse, office)
        state = r.get("state_po", "")
        county = r.get("county_name", "").strip()
        if level is Level.LOCAL:
            town = r.get("jurisdiction_name", "").strip()
            jurisdiction = (town or county) if state in TOWN_STATES else (county or town)
        else:
            jurisdiction = ""
        if dataverse in FEDERAL:
            office = FEDERAL[dataverse]
        if r.get("special", "").upper() == "TRUE":
            office += " (special)"
        when = iso_date(r.get("date", ""))
        key = Candidacy(
            state=state,
            election_year=int(r.get("year") or when[:4]),
            election_date=when,
            level=level,
            office=office,
            district=district_of(r.get("district", "")),
            jurisdiction=jurisdiction,
            counties="",
            candidate_name=candidate,
            party="",
            election_type=STAGES.get(r.get("stage", "").upper(), ""),
            incumbent_status="",
            candidate_status="",
            source="medsl",
            source_candidate_id="",
        )
        party = party_of(r.get("party_simplified", ""), r.get("party_detailed", ""))
        seen = parties.setdefault(key, [])
        if party and party not in seen:
            seen.append(party)
        if county:
            race_counties[_race(key)].add(county)
            all_counties.add(county)

    def counties_of(key: Candidacy) -> str:
        seen_in = race_counties.get(_race(key), set())
        return STATEWIDE if seen_in and seen_in >= all_counties else join_counties(seen_in)

    return [
        replace(key, party="/".join(sorted(p)), counties=counties_of(key))
        for key, p in parties.items()
    ]


def _race(candidacy: Candidacy) -> Candidacy:
    """The race a candidacy belongs to (it with the candidate blanked out)."""
    return replace(candidacy, candidate_name="")


def level_of(dataverse: str, office: str) -> Level:
    if dataverse in FEDERAL:
        return Level.FEDERAL
    if dataverse == "STATE":
        return Level.STATE
    if dataverse == "" and STATE_OFFICES.match(office.upper()):
        return Level.STATE
    return Level.LOCAL


def district_of(raw: str) -> str:
    """Normalize: "001" and "1" become "01"; "000" and "STATEWIDE" mean no district."""
    raw = raw.strip()
    if raw.isdigit():
        return "" if int(raw) == 0 else f"{int(raw):02d}"
    if raw.upper() == "AT LARGE":
        return "AT-LARGE"
    return "" if raw.upper() in ("STATEWIDE", '""') else raw


def iso_date(raw: str) -> str:
    """Most files use YYYY-MM-DD; a few use M/D/YY."""
    m = US_DATE.match(raw.strip())
    if not m:
        return raw.strip()
    month, day, year = (int(g) for g in m.groups())
    return f"{year + 2000 if year < 100 else year:04d}-{month:02d}-{day:02d}"


def party_of(simplified: str, detailed: str) -> str:
    detailed = detailed.strip().upper().removesuffix(" PARTY")
    for value in (simplified.upper(), detailed):
        if value in PARTIES:
            return PARTIES[value]
    return "" if detailed in NO_PARTY else detailed
