"""Every state's general-election ballot, from MIT Election Data + Science Lab results.

MEDSL publishes precinct-level returns for all 50 states and DC in one schema,
covering federal, state and (where the state reports them) local offices. We
reduce each file to the unique candidates per race, so this is who *appeared
on the ballot* in a past November general election, not who filed to run.
Statewide races also keep their vote totals by county, for the results map.

https://github.com/MEDSL  (one repository per election year)

Coverage is uneven: some states report no local races in some years (e.g.
California 2018), and a few files carry only surnames (Texas 2022, 2024). Write-ins
and turnout lines are dropped. Statewide ballot measures are kept as results
only (see ``measures``); local ones are dropped. 2020 is only on Harvard
Dataverse, behind an interactive download, so it isn't included.
"""

from __future__ import annotations

import csv
import html
import io
import logging
import re
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from typing import Any

from who_is_running.http import NotFound
from who_is_running.measures import choice_of
from who_is_running.models import STATEWIDE, Candidacy, Level, join_counties
from who_is_running.results import CountyResult
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
    r"^$|^(YES|NO|FOR|AGAINST|APPROVE|REJECT|REJECTED|VOIDS?|SPOILED|EXHAUSTED|NONE|TOTAL|"
    r"ELIGIBLE|MANUALLY COUNTED EMERGENCY|"
    r"BLANKS?|BLANK/VOID|ALL OTHERS|OTHERS|NONE OF THESE CANDIDATES|SCATTER(ING|ED)?|"
    r"TURNOUT|SPANISH SURNAME TURNOUT|AFFIDAVIT|FEDERAL|PUBLIC COUNTER|ABSENTEE / MILITARY|"
    r"OTHER|NO VOTE|NO CANDIDATE FILED|CONTEST TOTAL|"
    r"DEM|REP|LIB|DEMOCRAT|DEMOCRATIC|REPUBLICAN|LIBERTARIAN)$|"
    r"\b(VOTES|VOTERS|BALLOTS|VOTER REGISTRATION|TIMES BLANK VOTED|OVER ?VOTES?|UNDER ?VOTES?|"
    r"WRITE[- ]?INS?|STRAIGHT (TICKET|PARTY))\b|"
    r"^(YES|NO) TO\b"  # "YES TO APPROVE"
)
# Ballot questions and ticket/turnout lines filed under an "office".
NOT_OFFICES = re.compile(
    r"^(STRAIGHT[ -](PARTY|TICKET)|TOTAL |REGISTERED VOTERS|BALLOTS (CAST|COUNTED)|\d+\. |"
    r"PROPOSED )|"
    r"\b(QUESTION|PROPOSITION|AMENDMENTS?|AMENDS|BONDS|REFERENDUM|MEASURE|ISSUE|"
    r"ADVISORY (VOTE|NO)|BOND PROCEEDS|INITIATIVE)\b|"
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._results: dict[int, list[CountyResult]] = {}

    def candidacies(self, year: int) -> list[Candidacy]:
        self._results[year] = []
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
                            candidacies, results = parse_with_results(decode_lines(fh))
                        out.extend(candidacies)
                        self._results[year].extend(results)
        return sorted(out)

    def results(self, year: int) -> list[CountyResult]:
        # Both come from the same (large) download, so it's parsed once.
        if year not in self._results:
            self.candidacies(year)
        return sorted(self._results[year])


def parse(lines: Iterable[str]) -> list[Candidacy]:
    return parse_with_results(lines)[0]


def parse_with_results(lines: Iterable[str]) -> tuple[list[Candidacy], list[CountyResult]]:
    """Reduce precinct rows (header first) to one Candidacy per candidate per race.

    A candidate on several party lines of the same race (fusion voting in
    New York, Connecticut, ...) becomes one row with the parties joined by "/".
    Each race records the counties its precincts are in, or "*" when that is
    every county in the file (one file is one state).

    Statewide races also yield each candidate's votes per county, and so do
    statewide ballot measures, with "Yes" and "No" as the candidates. States
    split a precinct's votes by how they were cast ("ELECTION DAY",
    "ABSENTEE", ...), and some also give a "TOTAL" row for the same
    precinct, so a precinct's TOTAL is used when there is one and its other
    rows are added up when there isn't.
    """
    parties: dict[Candidacy, list[str]] = {}
    totals: Counter[tuple[Candidacy, str, str, str]] = Counter()
    by_mode: Counter[tuple[Candidacy, str, str, str]] = Counter()
    county_names: dict[str, str] = {}
    race_counties: defaultdict[Candidacy, set[str]] = defaultdict(set)
    voted_in: defaultdict[Candidacy, set[str]] = defaultdict(set)
    all_counties: set[str] = set()
    measures: set[Candidacy] = set()  # Yes/No answers, which aren't candidacies
    unsure: set[Candidacy] = set()  # answers whose row doesn't say it's a state measure
    reader = csv.reader(lines)
    header = next(reader, [])
    for row in reader:
        r = dict(zip(header, row, strict=False))
        # Wisconsin 2018 uses MEDSL's older column names.
        r.setdefault("county_name", r.get("county", ""))
        r.setdefault("party_detailed", r.get("party", ""))
        if r.get("writein", "").upper() == "TRUE":
            continue
        # A few files carry HTML entities ("SCHOOLS&#39;").
        candidate = " ".join(html.unescape(r.get("candidate", "")).split())
        office = " ".join(html.unescape(r.get("office", "")).split())
        dataverse = r.get("dataverse", "").upper()
        # Statewide measures are STATE rows, or (in some files) rows with no dataverse.
        choice = choice_of(candidate, office) if dataverse in ("STATE", "") else ""
        if not office or (
            not choice and (NOT_CANDIDATES.search(candidate.upper()) or NOT_OFFICES.search(office))
        ):
            continue
        level = Level.STATE if choice else level_of(dataverse, office)
        candidate = choice or candidate
        state = r.get("state_po", "")
        county = r.get("county_name", "").strip()
        if county.upper() == "NA":
            county = ""  # Rhode Island 2022's US House rows outside any county
        if level is Level.LOCAL:
            town = r.get("jurisdiction_name", "").strip()
            jurisdiction = (town or county) if state in TOWN_STATES else (county or town)
        else:
            jurisdiction = ""
        if dataverse in FEDERAL:
            office = FEDERAL[dataverse]
            if state == "DC" and office == "US Senate":
                office = "US Senate (shadow)"  # DC elects a non-voting "shadow" senator
        if r.get("special", "").upper() == "TRUE":
            office += " (special)"
        when = iso_date(r.get("date", ""))
        key = Candidacy(
            state=state,
            election_year=int(r.get("year") or when[:4]),
            election_date=when,
            level=level,
            office=office,
            # Senate and presidential races have no districts, though Arizona
            # 2024 gives Santa Cruz County's Senate rows "007".
            district=""
            if dataverse in ("SENATE", "PRESIDENT") or choice
            else district_of(r.get("district", "")),
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
        if choice:
            measures.add(key)
            if not dataverse:
                unsure.add(key)
        party = (
            "" if choice else party_of(r.get("party_simplified", ""), r.get("party_detailed", ""))
        )
        seen = parties.setdefault(key, [])
        if party and party not in seen:
            seen.append(party)
        votes = votes_of(r.get("votes", ""))
        if county:
            race_counties[_race(key)].add(county)
            all_counties.add(county)
            if votes > 0:
                voted_in[_race(key)].add(county)
        if level is not Level.LOCAL and key.district in ("", "AT-LARGE"):
            # Alaska reports early and absentee votes by legislative district,
            # with no county: they count toward the state total under "".
            fips = r.get("county_fips", "").strip()
            fips = fips.zfill(5) if fips.isdigit() else ""
            county_names[fips] = county if fips else ""
            precinct = (key, fips, r.get("precinct", ""), r.get("party_detailed", ""))
            if r.get("mode", "").upper() == "TOTAL":
                totals[precinct] += votes
            else:
                by_mode[precinct] += votes

    def counties_of(key: Candidacy) -> str:
        # Some files list a race's candidates with 0 votes in every precinct
        # of the state (Ohio's state house), so a county counts only if the
        # race got votes there, unless no county reported any.
        seen_in = voted_in.get(_race(key)) or race_counties.get(_race(key), set())
        return STATEWIDE if seen_in and seen_in >= all_counties else join_counties(seen_in)

    candidacies = {
        key: replace(key, party="/".join(sorted(p)), counties=counties_of(key))
        for key, p in parties.items()
    }
    # A measure is kept if it was on every county's ballot (or, in a file
    # without counties, is marked as a state measure), offers both Yes and No,
    # and isn't really a candidate race that lists "REJECTED" write-ins.
    offices = {(k.state, k.office) for k in candidacies if k not in measures}
    choices: defaultdict[Candidacy, set[str]] = defaultdict(set)
    for key in measures:
        choices[_race(key)].add(key.candidate_name)
    kept = {
        key
        for key in measures
        if len(choices[_race(key)]) == 2
        and (key.state, key.office) not in offices
        and (candidacies[key].counties == STATEWIDE or not (all_counties or key in unsure))
    }
    county_votes: Counter[tuple[Candidacy, str]] = Counter()
    for precinct in totals.keys() | by_mode.keys():
        key, fips = precinct[0], precinct[1]
        if key in measures and key not in kept:
            continue
        # A file with no counties at all (Wisconsin 2018, Alaska 2022) still
        # gives statewide totals; they're all filed as not reported by county.
        if candidacies[key].counties == STATEWIDE or not all_counties:
            county_votes[key, fips] += totals[precinct] if precinct in totals else by_mode[precinct]
    results = [
        CountyResult(
            state=key.state,
            election_year=key.election_year,
            office=key.office,
            county_fips=fips,
            county=county_names[fips],
            candidate_name=key.candidate_name,
            party=candidacies[key].party,
            votes=votes,
            source="medsl",
        )
        for (key, fips), votes in county_votes.items()
    ]
    return [c for key, c in candidacies.items() if key not in measures], results


def votes_of(raw: str) -> int:
    """Vote counts are integers, sometimes written "12.0"; blanks and "*" (suppressed) are 0."""
    try:
        return int(float(raw))
    except (ValueError, OverflowError):
        return 0


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
