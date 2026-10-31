"""Every state's general-election ballot, from MIT Election Data + Science Lab results.

MEDSL publishes precinct-level returns for all 50 states and DC in one schema,
covering federal, state and (where the state reports them) local offices. We
reduce each file to the unique candidates per race, so this is who *appeared
on the ballot* in a past November general election, not who filed to run.
Statewide races also keep their vote totals by county, for the results map,
with runoffs (Georgia 2022) apart from the general election. 2018 also adds
statewide primaries from MEDSL's separate primary returns, votes only.

https://github.com/MEDSL  (one repository per election year)

Coverage is uneven: some states report no local races in some years (e.g.
California 2018), and a few files carry only surnames (Texas 2022, 2024). Write-ins
and turnout lines are dropped. Statewide ballot measures are kept as results
only (see ``measures``); local ones are dropped.

MEDSL now publishes 2020 only on Harvard Dataverse, whose downloads need a
guestbook form (name, email, institution), so 2020 comes from the last copy
on GitHub, pinned by commit: state and federal offices, every state but Indiana,
which was added later. 2016 comes from MEDSL's older precinct repository, in an
older schema with office names as each county spelled them.
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
from who_is_running.models import STATEWIDE, Candidacy, Level, house_office, join_counties
from who_is_running.results import CountyResult
from who_is_running.sources import medsl_fill
from who_is_running.sources.base import Source
from who_is_running.text import decode_lines

log = logging.getLogger(__name__)

RAW = "https://raw.githubusercontent.com/MEDSL/{year}-elections-official/HEAD/individual_states"
FILES = {
    2018: "2018-{st}-precinct-general.zip",
    2022: "2022-{st}-local-precinct-general.zip",
    2024: "{st}24.zip",
    2016: "2016-{st}-precinct.zip",
    2020: "2020-{st}-precinct-general.zip",
}
# Years whose files aren't in RAW (see the module docstring).
BASES = {
    2016: "https://raw.githubusercontent.com/MEDSL/official-precinct-returns/HEAD/source",
    2020: "https://raw.githubusercontent.com/MEDSL/2020-elections-official/"
    "ad22f37e3f1e3355f078ba5a1efce6c221333ef1/individual_states",
}
STATES = [
    *("AK", "AL", "AR", "AZ", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA"),
    *("ID", "IL", "IN", "KS", "KY", "LA", "MA", "MD", "ME", "MI", "MN", "MO", "MS"),
    *("MT", "NC", "ND", "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA"),
    *("RI", "SC", "SD", "TN", "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY"),
]

FEDERAL = {"PRESIDENT": "US President", "SENATE": "US Senate", "HOUSE": "US House"}
# MEDSL's `stage`. Recounts ("GEN RECOUNT", "GEN RECOUNT 2") repeat a few
# counties' votes, so they get no stage and stay out of the vote totals.
STAGES = {
    "GEN": "general",
    "PRI": "primary",
    "RUN": "runoff",
    "RUNOFF": "runoff",
    "GEN RUNOFF": "runoff",  # Georgia 2022: the December 6 US Senate runoff
    "PRI RUNOFF": "runoff",
    "PRI, RUNOFF": "runoff",
}
# MEDSL's separate official primary returns (2018 only, about 20 states).
PRIMARIES = {
    2018: "https://raw.githubusercontent.com/MEDSL/primary-precinct-returns/HEAD/2018/"
    "2018_precinct_primary.csv.zip",
}
# The states whose governor and US Senate primary totals match the certified
# results (Florida's to within one vote, as MEDSL notes). Left out: MD (votes
# doubled), MA (about 500 short), NV (masked counts), SC (no votes), KS
# (counties missing), CT (off by up to 40), AZ and DE (wrong election dates),
# DC (not checked); NC had no statewide primaries.
PRIMARY_STATES = {
    2018: frozenset({"AK", "FL", "HI", "MN", "MT", "OH", "OK", "TN", "VA", "WI"}),
}
PARTIES = {
    "DEMOCRAT": "DEM",
    "DEMOCRATIC": "DEM",  # 2016
    "REPUBLICAN": "REP",
    "LIBERTARIAN": "LIB",
    "GREEN": "GRE",
    "INDEPENDENT": "IND",
}
# Vermont 2016 gives no party on any presidential line. The major-party
# nominees were on their party's line in every state.
NOMINEES_2016 = {"Hillary Clinton": "DEMOCRATIC", "Donald Trump": "REPUBLICAN"}
# New Hampshire 2016 gives its cities' wards no county; each city lies in one.
NH_CITIES = {
    "Berlin": ("Coos County", "33007"),
    "Claremont": ("Sullivan County", "33019"),
    "Concord": ("Merrimack County", "33013"),
    "Dover": ("Strafford County", "33017"),
    "Franklin": ("Merrimack County", "33013"),
    "Keene": ("Cheshire County", "33005"),
    "Laconia": ("Belknap County", "33001"),
    "Lebanon": ("Grafton County", "33009"),
    "Manchester": ("Hillsborough County", "33011"),
    "Nashua": ("Hillsborough County", "33011"),
    "Portsmouth": ("Rockingham County", "33015"),
    "Rochester": ("Strafford County", "33017"),
    "Somersworth": ("Strafford County", "33017"),
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
# Lines that sum a county's precincts, filed as one more precinct: Idaho 2022's
# "COUNTY TOTAL", Michigan 2022's "TOTALS" and "Mackinac County Michigan - Total".
TOTAL_LINES = re.compile(r"^((COUNTY|DISTRICT) )?TOTALS?$|\bCOUNTY\b.* - TOTAL$", re.I)
# Statewide courts and commissions with several seats file each seat as a
# district ("001", "PLACE 1"); North Carolina's one 2018 Supreme Court seat is "001".
SEAT_OFFICES = re.compile(r"\b(COURT|JUSTICE|JUDGE|COMMISSION|COMMISSIONER)\b")


class MedslSource(Source):
    name = "medsl"
    description = (
        "MIT Election Lab precinct results: every candidate on the November ballot, "
        "all states (2016, 2018, 2020, 2022, 2024)"
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
            base = BASES.get(year, RAW.format(year=year))
            url = f"{base}/{FILES[year].format(st=st.lower())}"
            try:
                archive = self.fetch(url)
            except NotFound:
                log.warning("medsl: no %d file for %s", year, st)
                continue
            with zipfile.ZipFile(io.BytesIO(archive)) as zf:
                for member in zf.namelist():
                    # North Carolina 2020 also ships its file re-sorted ("...-sorted.csv").
                    if (
                        member.lower().endswith(".csv")
                        and not member.startswith("__MACOSX")
                        and not member.lower().endswith("-sorted.csv")
                    ):
                        with zf.open(member) as fh:
                            candidacies, results = parse_with_results(decode_lines(fh))
                        out.extend(candidacies)
                        self._results[year].extend(results)
        out, self._results[year] = medsl_fill.apply(year, self.fetch, out, self._results[year])
        if year in PRIMARIES:
            self._results[year].extend(self._primary_results(year))
        return sorted(out)

    def _primary_results(self, year: int) -> list[CountyResult]:
        """County votes in statewide primaries. Only the votes: the candidate
        lists stay general-election ballots."""
        try:
            archive = self.fetch(PRIMARIES[year])
        except NotFound:
            log.warning("medsl: no %d primary file", year)
            return []
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            (member,) = (m for m in zf.namelist() if m.lower().endswith(".csv"))
            with zf.open(member) as fh:
                lines = decode_lines(fh)
                return parse_with_results(lines, primary_states=PRIMARY_STATES[year])[1]

    def results(self, year: int) -> list[CountyResult]:
        # Both come from the same (large) download, so it's parsed once.
        if year not in self._results:
            self.candidacies(year)
        return sorted(self._results[year])


def parse(lines: Iterable[str]) -> list[Candidacy]:
    return parse_with_results(lines)[0]


def parse_with_results(
    lines: Iterable[str], *, primary_states: frozenset[str] | None = None
) -> tuple[list[Candidacy], list[CountyResult]]:
    """Reduce precinct rows (header first) to one Candidacy per candidate per race.

    ``primary_states`` marks a primary file (all states in one file) and keeps
    only those states. Each party's primary is then its own race, and a
    runoff is a primary runoff.

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
    all_counties: defaultdict[str, set[str]] = defaultdict(set)  # by state
    surnames: dict[Candidacy, str] = {}
    candidate_counties: defaultdict[Candidacy, set[str]] = defaultdict(set)
    measures: set[Candidacy] = set()  # Yes/No answers, which aren't candidacies
    unsure: set[Candidacy] = set()  # answers whose row doesn't say it's a state measure
    reader = csv.reader(lines)
    header = next(reader, [])
    state_col = header.index("state_po") if "state_po" in header else -1
    for row in reader:
        if primary_states is not None and row[state_col] not in primary_states:
            continue
        r = dict(zip(header, row, strict=False))
        # Wisconsin 2018 and all of 2016 use MEDSL's older column names.
        r.setdefault("county_name", r.get("county", ""))
        # 2016 leaves some ballot lines' party blank but gives the candidate's own party.
        r.setdefault(
            "party_detailed",
            r.get("party", "")
            or r.get("candidate_party", "")
            or NOMINEES_2016.get(r.get("candidate", ""), ""),
        )
        r.setdefault("state_po", r.get("state_postal", ""))
        r.setdefault("jurisdiction_name", r.get("jurisdiction", ""))
        city = r["jurisdiction_name"].strip()
        if r["state_po"] == "NH" and not r["county_name"].strip() and city in NH_CITIES:
            r["county_name"], r["county_fips"] = NH_CITIES[city]
        if r.get("writein", "").upper() == "TRUE":
            continue
        # A few files carry HTML entities ("SCHOOLS&#39;").
        candidate = " ".join(html.unescape(r.get("candidate", "")).split())
        office = " ".join(html.unescape(r.get("office", "")).split())
        # 2016 sometimes gives the district after a bar: "Regent Of The
        # University Of Colorado|1", with "|0" for the at-large seat.
        name, bar, seat = office.rpartition("|")
        if bar and seat.isdigit() and not r.get("district", "").strip():
            office, r["district"] = name, seat
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
            elif office == "US House":
                office = house_office(state)  # DC's "US House Delegate"
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
            # Keeps a measure apart from a candidate race filed under the same office.
            candidate_status="measure" if choice else "",
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
        if primary_states is not None:
            key = replace(key, party=party)  # one race per party's primary
        seen = parties.setdefault(key, [])
        if party and party not in seen:
            seen.append(party)
        votes = votes_of(r.get("votes", ""))
        if county:
            race_counties[_race(key)].add(county)
            if not choice:  # measures must cover the counties races do, not add to them
                all_counties[state].add(county)
            if votes > 0:
                voted_in[_race(key)].add(county)
        if level is not Level.LOCAL and (
            key.district in ("", "AT-LARGE") or SEAT_OFFICES.search(office)
        ):
            if r.get("candidate_normalized", "").strip():
                surnames[key] = r["candidate_normalized"].strip().lower()
            if county and votes > 0:
                candidate_counties[key].add(county)
            # Alaska reports early and absentee votes by legislative district,
            # with no county: they count toward the state total under "".
            # Wisconsin 2016 appends a 5-digit town code ("5500100000").
            fips = r.get("county_fips", "").strip()[:5]
            fips = fips.zfill(5) if fips.isdigit() else ""
            # 2016 leaves some names blank, e.g. Kansas City's, but names the jurisdiction.
            if not choice or fips not in county_names:  # races' spelling wins
                county_names[fips] = (
                    (county or r.get("jurisdiction_name", "").strip()) if fips else ""
                )
            precinct = (key, fips, r.get("precinct", ""), r.get("party_detailed", ""))
            if r.get("mode", "").upper() == "TOTAL":
                totals[precinct] += votes
            else:
                by_mode[precinct] += votes

    if surnames:
        canon = _merge_spellings(surnames, candidate_counties, voted_in, totals, by_mode)
        merged: dict[Candidacy, list[str]] = {}
        for key, p in parties.items():
            seen = merged.setdefault(canon.get(key, key), [])
            seen.extend(x for x in p if x not in seen)
        parties = merged
        for counter in (totals, by_mode):
            moved: Counter[tuple[Candidacy, str, str, str]] = Counter()
            for k, v in counter.items():
                moved[canon.get(k[0], k[0]), *k[1:]] += v
            counter.clear()
            counter.update(moved)
        for key, best in canon.items():
            if key != best:
                candidate_counties[best] |= candidate_counties.pop(key, set())

    # Some files leave out the district of a per-district race (2016's State
    # House in New Hampshire and Vermont, Maryland 2018's county clerks), so
    # it looks statewide. In a real statewide race some candidate gets votes
    # in (nearly) every county; in these, each one is on a few counties' ballots.
    # (With only one or two counties there is nothing to tell them apart by.)
    best_cover: Counter[Candidacy] = Counter()
    for key, seen_in in candidate_counties.items():
        best_cover[_race(key)] = max(best_cover[_race(key)], len(seen_in))
    split = {
        race
        for race, n in best_cover.items()
        if len(voted_in[race]) > 2 and n < 0.9 * len(voted_in[race])
    }

    def counties_of(key: Candidacy) -> str:
        # Some files list a race's candidates with 0 votes in every precinct
        # of the state (Ohio's state house), so a county counts only if the
        # race got votes there, unless no county reported any. A small party's
        # primary can get no votes in a county it was on the ballot in.
        voted = voted_in.get(_race(key)) if primary_states is None else None
        seen_in = voted or race_counties.get(_race(key), set())
        statewide = seen_in and seen_in >= all_counties[key.state]
        return STATEWIDE if statewide else join_counties(seen_in)

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
        and (
            candidacies[key].counties == STATEWIDE or not (all_counties[key.state] or key in unsure)
        )
    }
    county_votes: Counter[tuple[Candidacy, str]] = Counter()
    precincts = totals.keys() | by_mode.keys()
    real_precincts = {(p[0], p[1]) for p in precincts if not TOTAL_LINES.search(p[2])}
    for precinct in precincts:
        key, fips = precinct[0], precinct[1]
        if TOTAL_LINES.search(precinct[2]) and (key, fips) in real_precincts:
            continue  # it repeats the county's precincts
        if key in measures and key not in kept:
            continue
        if not key.election_type:
            continue  # a recount of some counties, already counted
        # A file with no counties at all (Wisconsin 2018, Alaska 2022) still
        # gives statewide totals; they're all filed as not reported by county.
        state_counties = all_counties[key.state]
        if key.district not in ("", "AT-LARGE"):
            # A seat on a statewide court, not a district, when every county votes on it.
            if candidacies[key].counties != STATEWIDE or len(state_counties) < 2:
                continue
        elif state_counties and (candidacies[key].counties != STATEWIDE or _race(key) in split):
            continue
        county_votes[key, fips] += totals[precinct] if precinct in totals else by_mode[precinct]
    results = [
        CountyResult(
            state=key.state,
            election_year=key.election_year,
            election_date=key.election_date,
            stage=stage_of(key.election_type, primary=primary_states is not None),
            office=seat_office(key),
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


def _merge_spellings(
    surnames: dict[Candidacy, str],
    counties: dict[Candidacy, set[str]],
    race_counties: dict[Candidacy, set[str]],
    totals: Counter[tuple[Candidacy, str, str, str]],
    by_mode: Counter[tuple[Candidacy, str, str, str]],
) -> dict[Candidacy, Candidacy]:
    """Map each spelling of a statewide candidate to the spelling with the most votes.

    2016 files spell names as each county did ("Moran, Jerry" in some
    counties, "Jerry Moran" in the rest), but also give MEDSL's normalized
    surname, which is the same for all of them. Spellings sharing a surname
    are one candidate only if each county uses just one of them and together
    they cover every county the race got votes in. That keeps apart two
    people with one surname, such as two judges up for retention, or two
    State House candidates where the file gives no district.
    """
    votes: Counter[Candidacy] = Counter()
    for precinct in totals.keys() | by_mode.keys():
        votes[precinct[0]] += totals[precinct] if precinct in totals else by_mode[precinct]
    spellings: defaultdict[tuple[Candidacy, str], list[Candidacy]] = defaultdict(list)
    for key, surname in surnames.items():
        spellings[_race(key), surname].append(key)
    canon: dict[Candidacy, Candidacy] = {}
    for (race, _), keys in spellings.items():
        seen = [counties.get(k, set()) for k in keys]
        union = set().union(*seen)
        if len(keys) > 1 and sum(map(len, seen)) == len(union) == len(race_counties.get(race, ())):
            best = max(keys, key=lambda k: (votes[k], k.candidate_name))
            canon.update(dict.fromkeys(keys, best))
    return canon


def seat_office(key: Candidacy) -> str:
    """A statewide race's name in county results, which have no district column.

    A seat goes into the name: "SUPREME COURT ASSOCIATE JUSTICE, SEAT 1".
    """
    if key.district in ("", "AT-LARGE"):
        return key.office
    seat = key.district.removeprefix("STATEWIDE, ")
    if seat.isdigit():
        seat = f"SEAT {int(seat)}"
    elif len(seat) == 1:
        seat = f"SEAT {seat}"  # Nevada's "C"
    return f"{key.office}, {seat}"


def votes_of(raw: str) -> int:
    """Vote counts are integers, sometimes written "12.0"; blanks, "*" and -1 (suppressed) are 0."""
    try:
        return max(0, int(float(raw)))
    except (ValueError, OverflowError):
        return 0


def stage_of(election_type: str, *, primary: bool) -> str:
    """The results stage (``results.STAGES``): a runoff in a primary file is a primary runoff."""
    return "primary runoff" if primary and election_type == "runoff" else election_type


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
