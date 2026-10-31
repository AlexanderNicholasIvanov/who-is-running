"""County results for the statewide races MEDSL's precinct files get wrong.

A few of MEDSL's files miss counties (Indiana 2022 has 39 of 92) or whole
races (California 2022 has no US Senate rows), so ``medsl`` leaves those races
off the map rather than show a wrong county breakdown. Each fill below
re-reads one state's race from the state's own certified results and replaces
MEDSL's rows for it, never adding to them.

Candidates are matched to MEDSL's spelling of the same race, so a race reads
the same in the candidate list and on the map. A fill that fails to download
is skipped with a warning, which leaves the race off the map as before.

Holes no fill covers, and why, are listed under "Known limits" in the README
(New York's State Board of Elections, for one, answers scripts with a
browser challenge).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from typing import Any

from who_is_running.districts import COUNTY_NAMES_2010, county_name, parse_county_names
from who_is_running.http import Fetcher
from who_is_running.models import STATEWIDE, Candidacy, Level
from who_is_running.results import CountyResult
from who_is_running.text import decode
from who_is_running.xlsx import sheets

log = logging.getLogger(__name__)

PARTY_CODES = {
    "DEMOCRATIC": "DEM",
    "DEMOCRAT": "DEM",
    "D": "DEM",
    "REPUBLICAN": "REP",
    "R": "REP",
    "LIBERTARIAN": "LIB",
    "L": "LIB",
    "GREEN": "GRE",
    "INDEPENDENT": "IND",
}
WRITE_IN = re.compile(r"\bWRITE[- ]?INS?\b|\(W/I\)", re.I)


@dataclass(frozen=True)
class Vote:
    """One candidate's votes in one county, as a state's file gives them."""

    office: str
    """MEDSL's name for the race, e.g. "US Senate" or "GOVERNOR"."""
    county: str
    """County name ("DeKalb") or five-digit code ("18033")."""
    candidate: str
    party: str
    votes: int


@dataclass(frozen=True)
class Fill:
    state: str
    year: int
    source: str
    """Who published the numbers; becomes ``CountyResult.source``."""
    urls: tuple[str, ...]
    parse: Callable[[list[bytes]], Iterable[Vote]]


def apply(
    year: int, fetch: Fetcher, candidacies: list[Candidacy], results: list[CountyResult]
) -> tuple[list[Candidacy], list[CountyResult]]:
    """MEDSL's candidacies and county results for a year, with every fill for that year applied.

    A filled race's MEDSL results are dropped and the fill's take their place.
    Its candidacies stay, but are marked as on every county's ballot, which
    MEDSL couldn't see when its file missed counties.
    """
    counties: dict[str, tuple[str, str]] = {}
    for fill in FILLS:
        if fill.year != year or not any(c.state == fill.state for c in candidacies):
            continue  # a fill corrects MEDSL's file for the state; no file, nothing to fix
        try:
            counties = counties or census_counties(fetch)
            rows = fill_results(fill, fetch, candidacies, counties)
        except Exception as err:  # a failed fill leaves the race off the map, as before
            log.warning(
                "medsl: couldn't fill %s %d from %s: %s", fill.state, year, fill.source, err
            )
            continue
        filled = {(r.state, r.office) for r in rows}
        log.info("medsl: filled %s from %s", sorted(o for _, o in filled), fill.source)
        results = [r for r in results if (r.state, r.office) not in filled] + rows
        candidacies = [
            replace(c, counties=STATEWIDE)
            if (c.state, c.office) in filled and c.district == "" and c.level is not Level.LOCAL
            else c
            for c in candidacies
        ]
    return candidacies, results


def fill_results(
    fill: Fill, fetch: Fetcher, medsl: Iterable[Candidacy], counties: dict[str, tuple[str, str]]
) -> list[CountyResult]:
    """One fill's county results. ``counties`` maps codes to (state, name), from Census."""
    by_name = {_name_key(name): fips for fips, (st, name) in counties.items() if st == fill.state}
    known = [c for c in medsl if c.state == fill.state and c.district == ""]
    spelling: dict[tuple[str, str, str], tuple[str, str]] = {}
    totals: Counter[tuple[str, str, str, str]] = Counter()
    for vote in fill.parse([fetch(url) for url in fill.urls]):
        if WRITE_IN.search(vote.candidate) or not vote.candidate.strip():
            continue
        fips = vote.county if vote.county.isdigit() else by_name.get(_name_key(vote.county), "")
        if not fips or counties.get(fips, ("", ""))[0] != fill.state:
            raise ValueError(f"{fill.source}: unknown {fill.state} county {vote.county!r}")
        who = (vote.office, vote.candidate, vote.party)
        if who not in spelling:
            spelling[who] = _medsl_spelling(vote, [c for c in known if c.office == vote.office])
        name, party = spelling[who]
        totals[vote.office, fips, name, party] += vote.votes
    return [
        CountyResult(
            state=fill.state,
            election_year=fill.year,
            office=office,
            county_fips=fips,
            county=counties[fips][1],
            candidate_name=name,
            party=party,
            votes=votes,
            source=fill.source,
        )
        for (office, fips, name, party), votes in totals.items()
    ]


def census_counties(fetch: Fetcher) -> dict[str, tuple[str, str]]:
    return parse_county_names(decode(fetch(COUNTY_NAMES_2010)))


def _name_key(name: str) -> str:
    """ "DeKalb", "De Kalb County" and "DEKALB" match, as do "St. Joseph" and "ST JOSEPH"."""
    return re.sub(r"[^A-Z]", "", county_name(name))


def _tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[A-Z]+", name.upper()) if len(t) > 2}


def _medsl_spelling(vote: Vote, known: list[Candidacy]) -> tuple[str, str]:
    """The candidate's name and party as MEDSL gives them in the race (``known``), if it has them.

    Names match when they share at least two words of three letters or more
    ("Thomas M. McDermott, Jr." and "THOMAS M MCDERMOTT JR"). Otherwise the
    state's spelling is kept, upper-cased like MEDSL's.
    """
    party = _party(vote.party)
    words = _tokens(vote.candidate)
    best, overlap = None, 1
    for c in known:
        shared = len(words & _tokens(c.candidate_name))
        if shared > overlap:
            best, overlap = c, shared
    if best:
        return best.candidate_name, best.party or party
    return " ".join(vote.candidate.upper().split()), party


def _party(raw: str) -> str:
    raw = " ".join(raw.upper().split()).removesuffix(" PARTY")
    return PARTY_CODES.get(raw, raw)


def _number(raw: str) -> int:
    return int(float(raw.replace(",", "") or 0))


# --- California: Secretary of State, Statement of Vote -------------------------------------

CA_2022_OFFICES = {
    "US Senate - 1": "US Senate",
    "US Senate - 3 Partial/Unexpired Term": "US Senate (special)",
}


def parse_ca_sov(bodies: list[bytes], offices: dict[str, str]) -> Iterator[Vote]:
    """The SoV's one-sheet list of every candidate's votes by county (``csv-candidates.xlsx``)."""
    for rows in sheets(bodies[0]).values():
        header = rows[0]
        for row in rows[1:]:
            r = dict(zip(header, row, strict=False))
            office = offices.get(r.get("Contest Name", ""))
            if office and r.get("Write-in Flag") != "Y":
                yield Vote(
                    office,
                    r["County Name"],
                    r["Candidate Name"],
                    r["Party Name"],
                    _number(r["Vote Total"]),
                )


# --- Tennessee: Secretary of State, results by precinct ------------------------------------


def parse_tn_precincts(bodies: list[bytes], offices: dict[str, str]) -> Iterator[Vote]:
    """One row per precinct and office, with up to ten (name, party, votes) column groups."""
    for rows in sheets(bodies[0]).values():
        header = rows[0]
        for row in rows[1:]:
            r = dict(zip(header, row, strict=False))
            office = offices.get(r.get("OFFICENAME", ""))
            if not office:
                continue
            n = 1
            while f"RNAME{n}" in r:
                if r[f"RNAME{n}"]:
                    votes = _number(r.get(f"PVTALLY{n}", ""))
                    yield Vote(office, r["COUNTY"], r[f"RNAME{n}"], r.get(f"PARTY{n}", ""), votes)
                n += 1


# --- Idaho: Secretary of State canvass (ElectionStats), county CSV per contest -------------

ID_CANVASS = (
    "https://canvass.sos.idaho.gov/eng/contests/download/{id}/show_granularity_dt_id:3/.csv"
)
ID_2022_CONTESTS = {
    17248: "US Senate",
    17252: "LIEUTENANT GOVERNOR",
    17257: "SUPERINTENDENT OF PUBLIC INSTRUCTION",
}


def parse_id_canvass(bodies: list[bytes], offices: list[str]) -> Iterator[Vote]:
    """Header cells are "Name\\nParty"; a "Totals" row and column sum the rest."""
    for body, office in zip(bodies, offices, strict=True):
        reader = csv.reader(io.StringIO(decode(body)))
        header = next(reader)
        for row in reader:
            if not row or row[0] in ("", "Totals"):
                continue
            for cell, raw in zip(header[1:], row[1:], strict=False):
                name, _, party = cell.partition("\n")
                if name != "Total Votes Cast":
                    yield Vote(office, row[0], name, party, _number(raw))


# --- Indiana: Election Division's certified results archive (ENR JSON) ---------------------

IN_ENR = "https://enr.indianavoters.in.gov/archive/2022General/data/OffCatC_{id}_A.json"
IN_2022_CATEGORIES = {
    1006: "US Senate",
    1040: "SECRETARY OF STATE",
    1046: "STATE AUDITOR",
    1036: "STATE TREASURER",
}


def parse_in_enr(bodies: list[bytes], offices: list[str]) -> Iterator[Vote]:
    """One file per office category, with each county's races and candidates."""
    for body, office in zip(bodies, offices, strict=True):
        root = json.loads(decode(body))["Root"]
        for region in _listed(root["OfficeCategory"]["Regions"]["Region"]):
            for race in _listed(region["Races"]["Race"]):
                for c in _listed(race["Candidates"]["Candidate"]):
                    yield Vote(
                        office,
                        region["MAP_FIPS"],
                        c["NAME_ON_BALLOT"],
                        c.get("PARTY_ABBREV", ""),
                        int(c["TOTAL_VOTES"]),
                    )


def _listed(value: Any) -> list[Any]:
    """The ENR files give a one-item list as the bare item."""
    return value if isinstance(value, list) else [value]


# --- North Dakota: OpenElections' county file, from the Secretary of State's results ------

OE_ND_2024 = (
    "https://raw.githubusercontent.com/openelections/openelections-data-nd/"
    "master/2024/20241105__nd__general__county.csv"
)
ND_2024_OFFICES = {"Governor and Lt. Governor": "GOVERNOR AND LIEUTENANT GOVERNOR"}


def parse_openelections(bodies: list[bytes], offices: dict[str, str]) -> Iterator[Vote]:
    for r in csv.DictReader(io.StringIO(decode(bodies[0]))):
        office = offices.get(r.get("office", ""))
        if office:
            yield Vote(office, r["county"], r["candidate"], r.get("party", ""), _number(r["votes"]))


FILLS = [
    # No US Senate rows at all, regular or special.
    Fill(
        "CA",
        2022,
        "ca-sos",
        ("https://elections.cdn.sos.ca.gov/sov/2022-general/sov/csv-candidates.xlsx",),
        lambda b: parse_ca_sov(b, CA_2022_OFFICES),
    ),
    # No governor race.
    Fill(
        "TN",
        2022,
        "tn-sos",
        ("https://sos-prod.tnsosgovfiles.com/s3fs-public/document/20221108AllbyPrecinct.xlsx",),
        lambda b: parse_tn_precincts(b, {"Governor": "GOVERNOR"}),
    ),
    # Power County is missing from these three; Oneida and Shoshone too from the last.
    Fill(
        "ID",
        2022,
        "id-sos",
        tuple(ID_CANVASS.format(id=i) for i in ID_2022_CONTESTS),
        lambda b: parse_id_canvass(b, list(ID_2022_CONTESTS.values())),
    ),
    # The file covers 39 of 92 counties.
    Fill(
        "IN",
        2022,
        "in-sos",
        tuple(IN_ENR.format(id=i) for i in IN_2022_CATEGORIES),
        lambda b: parse_in_enr(b, list(IN_2022_CATEGORIES.values())),
    ),
    # No governor rows for Burleigh or Divide. The state's own site only keeps
    # its latest election, so this uses OpenElections' copy, which matches the
    # certified statewide totals.
    Fill(
        "ND",
        2024,
        "openelections",
        (OE_ND_2024,),
        lambda b: parse_openelections(b, ND_2024_OFFICES),
    ),
]
