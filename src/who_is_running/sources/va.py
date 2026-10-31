"""Virginia candidates from the Virginia Department of Elections (ELECT).

For each election ELECT posts an "all offices" candidate list as an .xlsx
workbook: one row per candidate per locality (county or independent city) the
race is on the ballot in. It covers federal races, General Assembly specials
and local offices (town and city councils, school boards, mayors, boards of
supervisors and constitutional officers such as sheriffs).

https://www.elections.virginia.gov/casting-a-ballot/candidate-list/

The workbook's name changes with each revision ("...(rev-10-9-2026).xlsx"),
so the source reads the election's list page and follows its .xlsx link. Add
an entry to ``ELECTIONS`` for each new election. The workbook also gives
campaign emails, phone numbers and addresses; they're never read.
"""

from __future__ import annotations

import html
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, urljoin

from who_is_running.districts import county_name
from who_is_running.http import NotFound
from who_is_running.models import STATEWIDE, Candidacy, Level, house_office, join_counties
from who_is_running.sources.base import Source
from who_is_running.text import decode
from who_is_running.xlsx import sheets

SITE = "https://www.elections.virginia.gov/casting-a-ballot/candidate-list"


@dataclass(frozen=True)
class Election:
    date: str
    kind: str
    """"primary" or "general"."""
    page: str
    """The list's page, which links the current revision of the workbook."""


ELECTIONS: dict[int, list[Election]] = {
    2026: [Election("2026-11-03", "general", f"{SITE}/november-3-2026-gen-elect-all-offices/")],
}

OFFICES = {
    "Member, United States Senate": ("US Senate", Level.FEDERAL),
    "Member, House of Representatives": (house_office("VA"), Level.FEDERAL),
    "Member, Senate of Virginia": ("State Senate", Level.STATE),
    "Member, House of Delegates": ("House of Delegates", Level.STATE),
}
PARTIES = {
    "Democratic": "DEM",
    "Republican": "REP",
    "Libertarian": "LIB",
    "Green": "GRE",
    "Independent": "IND",
}
NUMBERED = re.compile(r"^(\d+)(?:st|nd|rd|th) District$")
"""Congressional and General Assembly districts: "5th District"."""
LOCAL_DISTRICT = re.compile(r"\b(district|ward|borough|seat)\b", re.I)
"""Local district columns that name a district ("Ward 3", "Central District"), not a place."""


class VaSource(Source):
    name = "va"
    description = "Virginia Department of Elections candidate lists: all offices (2026-present)"

    def available_years(self) -> list[int]:
        return sorted(ELECTIONS)

    def refresh_years(self, today: date) -> list[int]:
        return [y for y in (today.year - 1, today.year) if y in ELECTIONS]

    def candidacies(self, year: int) -> list[Candidacy]:
        out: list[Candidacy] = []
        for election in ELECTIONS.get(year, []):
            url = workbook_url(decode(self.fetch(election.page)), election.page)
            out.extend(parse(self.fetch(url), election=election))
        return sorted(out)


def workbook_url(page: str, base: str) -> str:
    """The candidate workbook linked from a list page."""
    links = [html.unescape(h) for h in re.findall(r'href="([^"]+\.xlsx)"', page, re.I)]
    if not links:
        raise NotFound(f"no candidate workbook linked from {base}")
    best = next((h for h in links if "all-offices" in h.lower()), links[0])
    return quote(urljoin(base, best), safe=":/()-._~%")


def with_place(title: str, district: str, counties: list[str]) -> str:
    """Name the town in a town race's office when only the district column gives it.

    Most town races carry the town in the title ("Mayor - Belle Haven"), but
    some say just "Mayor" with the town as the district ("Farmville"). The
    district column of a city or county race is the locality itself.
    """
    place = re.sub(r"^(Town|City) of ", "", district)
    if not place or "," in place or place.lower() in title.lower():
        return title
    if county_name(place) in counties or county_name(f"{place} City") in counties:
        return title
    return f"{title} - {place}"


def parse(raw: bytes, *, election: Election) -> list[Candidacy]:
    """One candidacy per candidate per race, with the race's localities as ``counties``.

    The workbook repeats a race in every locality it's on the ballot in. Rows
    are grouped by office, district and locality, and localities whose groups
    list the same candidates are one race. That keeps apart the many races
    with the same title ("Member City Council", "District 2") in different
    cities, and joins the ones that cross locality lines (a town in two
    counties, a sheriff shared by a city and a county).
    """
    (rows, *_) = sheets(raw).values()
    header = [h.strip() for h in rows[0]]
    col = {name: i for i, name in enumerate(header)}

    def cell(row: list[str], name: str) -> str:
        i = col[name]
        return " ".join(row[i].split()) if i < len(row) else ""

    # (title, district, locality) -> {(name, party): incumbent}, plus the seat count.
    groups: dict[tuple[str, str, str], dict[tuple[str, str], bool]] = defaultdict(dict)
    seats: dict[tuple[str, str, str], str] = {}
    for row in rows[1:]:
        title, name = cell(row, "Office Title"), cell(row, "Candidate Name")
        if not title or not name:
            continue
        key = (title, cell(row, "District"), cell(row, "Locality"))
        groups[key][name, cell(row, "Candidate Party")] = cell(row, "Incumbent") == "Yes"
        seats[key] = cell(row, "Number of Seats")

    races: dict[tuple[str, str, frozenset[tuple[str, str]]], set[str]] = defaultdict(set)
    for (title, district, locality), found in groups.items():
        races[title, district, frozenset(found)].add(locality)

    out = []
    for (title, district, candidates), localities in races.items():
        some = min(localities)
        found = groups[title, district, some]
        office, level = OFFICES.get(title, (title, Level.LOCAL))
        counties = sorted({county_name(loc) for loc in localities})
        number = NUMBERED.match(district)
        if level is not Level.LOCAL:
            seat = number.group(1) if number else ""
        elif LOCAL_DISTRICT.search(district):
            seat = district
        else:
            seat = ""
            office = with_place(office, district, counties)
        single = seats[title, district, some] == "1"
        holder = any(found.values())
        for name, party in sorted(candidates):
            if found[name, party]:
                status = "incumbent"
            elif single:
                status = "challenger" if holder else "open_seat"
            else:
                status = ""  # several seats: an empty one may be open
            out.append(
                Candidacy(
                    state="VA",
                    election_year=int(election.date[:4]),
                    election_date=election.date,
                    level=level,
                    office=office,
                    district=seat,
                    jurisdiction=counties[0] if level is Level.LOCAL and len(counties) == 1 else "",
                    counties=STATEWIDE if district == "Statewide" else join_counties(counties),
                    candidate_name=name,
                    party=PARTIES.get(party, party),
                    election_type=election.kind,
                    incumbent_status=status,
                    candidate_status="",
                    source="va",
                    source_candidate_id="",
                )
            )
    return out
