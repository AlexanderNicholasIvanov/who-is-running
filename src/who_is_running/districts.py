"""Which counties a district covers, from Census Bureau relationship files.

Candidate lists name a race's district ("US House district 3") but not the
counties it spans. The Census Bureau publishes, for each set of district
lines, every district-county pair that shares land, which is what the
county view needs.

https://www.census.gov/geographies/reference-files/time-series/geo/relationship-files.html
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable

from who_is_running.http import Fetcher, NotFound
from who_is_running.models import STATEWIDE, join_counties
from who_is_running.text import decode

log = logging.getLogger(__name__)

RELFILES = "https://www2.census.gov/geo/relfiles"
REL2020 = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/cd-sld"
COUNTY_NAMES_2010 = "https://www2.census.gov/geo/docs/reference/codes/files/national_county.txt"
"""County FIPS codes and names, for the older files, which give codes only."""

CD109 = f"{RELFILES}/cd109th/natl_code/cou_cd109_natl.txt"
CD110 = f"{RELFILES}/cd110th/natl_code/cou_cd110_natl.txt"
CD113 = f"{RELFILES}/cdsld13/natl/natl_cocd_delim.txt"
CD115 = f"{RELFILES}/cdsld16/natl/natl_cocd_delim.txt"
CD116 = f"{RELFILES}/cdsld18/natl/natl_cocd_delim.txt"
CD118 = f"{REL2020}/tab20_cd11820_county20_natl.txt"
CD119 = f"{REL2020}/tab20_cd11920_county20_natl.txt"
SLDU_2024 = f"{REL2020}/tab20_sldu202420_county20_natl.txt"
SLDL_2024 = f"{REL2020}/tab20_sldl202420_county20_natl.txt"

HOUSE_LINES = {
    # Election year -> the Congress it elected. A Congress's file is only
    # used for the elections held on its lines; the 2000 and 2002 lines
    # have no national file, so those years stay unplaced.
    2004: CD109,
    2006: CD110,
    2008: CD110,
    2010: CD110,
    2012: CD113,
    2014: CD113,
    2016: CD115,
    2018: CD116,
    2020: CD116,
    2022: CD118,
    2024: CD119,
    2026: CD119,
}
REDRAWN = {
    # States whose House map for that election differs from the file's.
    2020: frozenset({"NC"}),  # court-ordered map, December 2019
    # New maps for 2026 (RevAddress, "New Congressional Maps 2026", Aug 2026).
    2026: frozenset({"AL", "CA", "FL", "LA", "MO", "NC", "OH", "TN", "TX", "UT"}),
}

STATE_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY", "72": "PR",
}  # fmt: skip

Pairs = Iterable[tuple[str, str, str]]
"""(state, district, county name) triples."""


class Plan:
    """One set of district lines: the counties each district touches."""

    def __init__(self, pairs: Pairs, *, skip_states: frozenset[str] = frozenset()) -> None:
        by_district: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
        by_state: defaultdict[str, set[str]] = defaultdict(set)
        for state, district, county in pairs:
            by_state[state].add(county)
            if state not in skip_states:
                by_district[state, district_key(district)].add(county)
        self._counties = {
            key: STATEWIDE if names >= by_state[key[0]] else join_counties(names)
            for key, names in by_district.items()
        }
        seats = Counter(state for state, _ in by_district)
        self._one_seat = {state for state, n in seats.items() if n == 1}

    def counties(self, state: str, district: str) -> str:
        """The ``counties`` value for a district; empty when the lines don't cover it."""
        if state in self._one_seat:
            return STATEWIDE  # filers in at-large states sometimes give district "01"
        return self._counties.get((state, district_key(district)), "")

    def __len__(self) -> int:
        return len(self._counties)


EMPTY = Plan(())


def district_key(district: str) -> str:
    """Spellings of a district number compare equal: "03", "3" and "003"; "011B" and "11B"."""
    return district.strip().upper().lstrip("0")


def county_name(name: str) -> str:
    """Census names ("Wake County", "Acadia Parish") in the style sources use ("WAKE")."""
    name = " ".join(name.upper().split())
    for suffix in (" COUNTY", " PARISH"):
        name = name.removesuffix(suffix)
    return name


class Districts:
    """Fetches and caches district lines. Missing files mean "unknown", never an error."""

    def __init__(self, fetch: Fetcher) -> None:
        self.fetch = fetch
        self._pairs: dict[str, list[tuple[str, str, str]]] = {}
        self._plans: dict[tuple[str, frozenset[str]], Plan] = {}
        self._names: dict[str, tuple[str, str]] | None = None

    def house(self, year: int) -> Plan:
        """US House lines for an election year (odd years are specials on the prior lines)."""
        regular = year - year % 2
        url = HOUSE_LINES.get(regular)
        if url is None:
            return EMPTY
        skip = REDRAWN.get(regular, frozenset())
        return self._plan(url, skip)

    def state_senate(self) -> Plan:
        """State legislative upper-house lines in effect for the 2024 elections."""
        return self._plan(SLDU_2024)

    def state_house(self) -> Plan:
        """State legislative lower-house lines in effect for the 2024 elections."""
        return self._plan(SLDL_2024)

    def _plan(self, url: str, skip: frozenset[str] = frozenset()) -> Plan:
        if (url, skip) not in self._plans:
            self._plans[url, skip] = Plan(self._pairs_in(url), skip_states=skip)
        return self._plans[url, skip]

    def _pairs_in(self, url: str) -> list[tuple[str, str, str]]:
        if url not in self._pairs:
            try:
                text = decode(self.fetch(url))
                if url.startswith(REL2020):
                    pairs = list(parse_2020(text))
                else:
                    if self._names is None:
                        self._names = parse_county_names(decode(self.fetch(COUNTY_NAMES_2010)))
                    pairs = list(parse_coded(text, self._names))
            except NotFound:
                log.warning("district lines not found: %s", url)
                pairs = []
            self._pairs[url] = pairs
        return self._pairs[url]


def parse_2020(text: str) -> Pairs:
    """The 2020-census files: pipe-delimited, one row per district-county pair."""
    lines = text.splitlines()
    for line in lines[1:]:
        f = line.split("|")
        if len(f) < 17:
            continue
        district, county_fips, county, land = f[1], f[8], f[9], f[15]
        # Rows without a district are county area outside every district
        # (e.g. open water), and "ZZ" districts are unassigned water.
        if not district or district.endswith("ZZ") or land in ("", "0"):
            continue
        state = STATE_FIPS.get(county_fips[:2])
        if state:
            yield state, district[2:], county_name(county)


def parse_coded(text: str, names: dict[str, tuple[str, str]]) -> Pairs:
    """The 2000s and 2010s files: a title line, a header, then "state,county,district" codes."""
    for line in text.splitlines()[2:]:
        f = [x.strip() for x in line.split(",")]
        if len(f) < 3:
            continue
        found = names.get(f[0] + f[1])
        if found:  # counties abolished before 2010 aren't in the name list
            state, county = found
            yield state, f[2], county


def parse_county_names(text: str) -> dict[str, tuple[str, str]]:
    """``national_county.txt`` ("AL,01,001,Autauga County,H1") as FIPS -> (state, name)."""
    out = {}
    for line in text.splitlines():
        f = line.split(",")
        if len(f) >= 4:
            out[f[1] + f[2]] = (f[0], county_name(f[3]))
    return out
