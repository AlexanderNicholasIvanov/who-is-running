"""North Carolina candidates from the NC State Board of Elections.

NCSBE publishes one candidate-filing CSV per year covering every contest it
administers: federal, state, judicial, county, municipal, school board and
special districts, for primaries and general elections.

https://www.ncsbe.gov/results-data/candidate-lists
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from xml.etree import ElementTree

from who_is_running.http import NotFound
from who_is_running.models import STATEWIDE, Candidacy, Level, join_counties
from who_is_running.sources.base import Source
from who_is_running.text import decode

FIRST_YEAR = 2010
COUNTY_COUNT = 100
BUCKET = "https://s3.amazonaws.com/dl.ncsbe.gov"
# File names vary by year (Candidate_listing_2010.csv, Candidate_Listing_2014_rev1.csv),
# so list the year's folder and pick the CSV instead of guessing the name.
KEY_PATTERN = re.compile(r"/candidate_listing_\d{4}[^/]*\.csv$", re.IGNORECASE)
DISTRICT_PATTERN = re.compile(r"\bDISTRICT ([0-9]+[A-Z]?|[A-Z])\b")
# Contest names don't carry a level. Since ~2016 state races start with "NC ";
# older files name judicial and prosecutorial races without it.
FEDERAL_PREFIXES = ("US ", "PRESIDENTIAL PREFERENCE")
STATE_PREFIXES = (
    "NC ",
    "DISTRICT ATTORNEY",
    "COURT OF APPEALS",
    "SUPREME COURT",
    "SUPERIOR COURT JUDGE",
    "DISTRICT COURT JUDGE",
)


class NcSource(Source):
    name = "nc"
    description = "NC State Board of Elections candidate listings: all levels (2010-present)"

    def available_years(self) -> list[int]:
        return [y for y in range(FIRST_YEAR, date.today().year + 1) if self._csv_key(y)]

    def refresh_years(self, today: date) -> list[int]:
        return [today.year - 1, today.year]

    def candidacies(self, year: int) -> list[Candidacy]:
        key = self._csv_key(year)
        if key is None:
            return []
        raw = self.fetch(f"{BUCKET}/{_quote(key)}")
        return parse(decode(raw))

    def _csv_key(self, year: int) -> str | None:
        prefix = _quote(f"Elections/{year}/Candidate Filing/")
        try:
            listing = self.fetch(f"{BUCKET}?list-type=2&prefix={prefix}")
        except NotFound:
            return None
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys = [
            el.text or ""
            for el in ElementTree.fromstring(listing).findall("s3:Contents/s3:Key", ns)
        ]
        matches = sorted(k for k in keys if KEY_PATTERN.search(k))
        return matches[-1] if matches else None


def _quote(path: str) -> str:
    return path.replace(" ", "%20")


def classify(contest: str) -> Level:
    if contest.startswith(FEDERAL_PREFIXES):
        return Level.FEDERAL
    if contest.startswith(STATE_PREFIXES):
        return Level.STATE
    return Level.LOCAL


def district_of(contest: str) -> str:
    match = DISTRICT_PATTERN.search(contest)
    return match.group(1) if match else ""


def _iso(us_date: str) -> str:
    return datetime.strptime(us_date.strip(), "%m/%d/%Y").date().isoformat()


def parse(text: str) -> list[Candidacy]:
    """Parse one year's listing.

    The file has one row per candidate per county the contest appears in, so
    a statewide race is repeated ~100 times. Collapse those, keeping the
    counties the race appears in; the county is also the jurisdiction when
    the race is confined to one.
    """
    counties: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
    for row in csv.DictReader(io.StringIO(text)):
        key = (
            row["election_dt"],
            row["contest_name"].strip(),
            row["name_on_ballot"].strip(),
            row["party_candidate"].strip(),
            row["party_contest"].strip(),
        )
        counties[key].add(row["county_name"].strip())

    out = []
    for (election_dt, contest, name, party, party_contest), seen_in in counties.items():
        election_date = _iso(election_dt)
        if party_contest:
            election_type = "primary"
        elif election_date[5:7] == "11":
            election_type = "general"
        else:
            election_type = ""
        out.append(
            Candidacy(
                state="NC",
                election_year=int(election_date[:4]),
                election_date=election_date,
                level=classify(contest),
                office=contest,
                district=district_of(contest),
                jurisdiction=next(iter(seen_in)) if len(seen_in) == 1 else "",
                counties=STATEWIDE if len(seen_in) >= COUNTY_COUNT else join_counties(seen_in),
                candidate_name=name,
                party=party,
                election_type=election_type,
                incumbent_status="",
                candidate_status="",
                source="nc",
                source_candidate_id="",
            )
        )
    return sorted(out)
