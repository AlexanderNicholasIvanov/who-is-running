"""Maryland candidates from the Maryland State Board of Elections.

For each gubernatorial and presidential primary and general election, SBE
publishes two candidate lists: a "statewide" one (federal, state, legislative
and judicial offices) and a "by county" one (county executive and council,
commissioners, school board, sheriff, state's attorney, orphans' court, party
central committees, ...).

https://elections.maryland.gov/candidacy/index.html

File names change every cycle (and current-cycle files move under /archive/
later), so known files are listed explicitly below. Add an entry for each new
election. Lists before 2022 have no header row and are not supported.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date

from who_is_running.districts import Districts, Plan
from who_is_running.http import NotFound
from who_is_running.models import STATEWIDE, Candidacy, Level
from who_is_running.sources.base import Source
from who_is_running.text import decode

SITE = "https://elections.maryland.gov/elections"


@dataclass(frozen=True)
class Election:
    date: str
    kind: str
    """"primary" or "general"."""
    statewide: tuple[str, ...]
    """Candidate URLs for the statewide list, tried in order."""
    counties: tuple[str, ...]


def _archived(year: int, kind: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    base = f"{SITE}/archive/{year}/{kind}_candidates/gen_cand_lists_{year}_1"
    return (f"{base}_ALL.csv",), (f"{base}_by_county_ALL.csv",)


def _current(year: int, kind: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    code = "GP" if kind == "primary" else "GG"
    paths = [f"{SITE}/{year}/{kind}_candidates", f"{SITE}/archive/{year}/{kind}_candidates"]
    return (
        tuple(f"{p}/{year}_{code}_statewide_candidatelist.csv" for p in paths),
        tuple(f"{p}/{year}_{code}_all_counties_candidatelist.csv" for p in paths),
    )


ELECTIONS: dict[int, list[Election]] = {
    2022: [
        Election("2022-07-19", "primary", *_archived(2022, "primary")),
        Election("2022-11-08", "general", *_archived(2022, "general")),
    ],
    2024: [
        Election("2024-05-14", "primary", *_archived(2024, "primary")),
        Election("2024-11-05", "general", *_archived(2024, "general")),
    ],
    2026: [
        Election("2026-06-23", "primary", *_current(2026, "primary")),
        Election("2026-11-03", "general", *_current(2026, "general")),
    ],
}

FEDERAL_OFFICES = (
    "President and Vice President",
    "U.S. Senator",
    "Representative in Congress",
)
PARTIES = {
    "Democratic": "DEM",
    "Republican": "REP",
    "Libertarian": "LIB",
    "Green": "GRE",
    "Unaffiliated": "UNA",
    "Non-Partisan": "",
    "Judicial": "",
}
DISTRICT_PATTERN = re.compile(r"(?:District|Circuit) (\w+)$")


class MdSource(Source):
    name = "md"
    description = "Maryland State Board of Elections candidate lists: all levels (2022-present)"

    def available_years(self) -> list[int]:
        return sorted(ELECTIONS)

    def refresh_years(self, today: date) -> list[int]:
        return [y for y in (today.year - 1, today.year) if y in ELECTIONS]

    def candidacies(self, year: int) -> list[Candidacy]:
        out: set[Candidacy] = set()
        for election in ELECTIONS.get(year, []):
            for urls, local in ((election.statewide, False), (election.counties, True)):
                text = self._first_available(urls)
                if text is not None:
                    out.update(parse(text, election=election, local=local))
        return sorted(place(out, Districts(self.fetch)))

    def _first_available(self, urls: tuple[str, ...]) -> str | None:
        for url in urls:
            try:
                body = self.fetch(url)
            except NotFound:
                continue
            # Missing pages come back as "200 OK" with an HTML error page.
            if body.lstrip()[:1] == b"<":
                continue
            return decode(body)
        return None


def district_of(contest_run_by: str) -> str:
    match = DISTRICT_PATTERN.search(contest_run_by.strip())
    return match.group(1) if match else ""


def parse(text: str, *, election: Election, local: bool) -> list[Candidacy]:
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        office = row["Office Name"].strip()
        last = row["Candidate Ballot Last Name and Suffix"].strip()
        if not office or not last:
            continue
        first = row["Candidate First Name and Middle Name"].strip()
        run_by = row["Contest Run By District Name and Number"].strip()
        party = row["Office Political Party"].strip()
        residence = row["Candidate Residential Jurisdiction"].strip()
        # County races require living in the county, so the residence column
        # is the race's county; it is blank only when suppressed. Circuit court
        # judges are listed by circuit but stand for election in the county
        # they sit in, and the list gives that county as their residence.
        county_race = local or run_by.startswith("Judicial Circuit")
        if county_race:
            counties = residence
        elif run_by.lower() == "state of maryland":
            counties = STATEWIDE
        else:
            counties = ""  # e.g. "Congressional District 3": see place()
        if office.startswith(FEDERAL_OFFICES):
            level = Level.FEDERAL
        elif local:
            level = Level.LOCAL
        else:
            level = Level.STATE
        out.append(
            Candidacy(
                state="MD",
                election_year=int(election.date[:4]),
                election_date=election.date,
                level=level,
                office=office,
                district=district_of(run_by),
                jurisdiction=residence if county_race else "",
                counties=counties,
                candidate_name=f"{first} {last}".strip(),
                party=PARTIES.get(party, party),
                election_type=election.kind,
                incumbent_status="",
                candidate_status=row["Candidate Status"].split(" - ")[0].strip().lower(),
                source="md",
                source_candidate_id="",
            )
        )
    return out


def place(rows: Iterable[Candidacy], districts: Districts) -> list[Candidacy]:
    """Fill in the counties of district races from the Census district lines.

    Legislative seats are elected in 2022 and 2026, both on the map adopted
    in 2022, which is also what the Census 2024 lines record.
    """

    def lines(row: Candidacy) -> Plan | None:
        if row.office == "Representative in Congress" or "National Convention" in row.office:
            return districts.house(row.election_year)  # delegates are elected by district
        if row.office == "State Senator":
            return districts.state_senate()
        if row.office == "House of Delegates":
            return districts.state_house()
        return None

    out = []
    for row in rows:
        plan = None if row.counties else lines(row)
        if plan is not None:
            row = replace(row, counties=plan.counties(row.state, row.district))
        out.append(row)
    return out
