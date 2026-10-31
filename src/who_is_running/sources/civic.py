"""Upcoming ballots from Google's Civic Information API, all states.

Google's ``voterinfo`` endpoint returns the contests on one address's ballot,
from data states and counties send to the Voting Information Project (VIP):
https://developers.google.com/civic-information/docs/v2/elections/voterInfoQuery

There is no "every contest in the state" query, so this source samples
addresses: for each county, the street address of its county government, then
of its cities, townships and school districts, from the Census Bureau's list
of every US government (the 2022 Census of Governments). Every county with a
street address on that list gets one lookup; the remaining budget goes to more
addresses in the most populous counties. Contests are deduplicated across
addresses. A contest that contains none of the sampled addresses is missed, so
local coverage is partial, and it also depends on what each state sends VIP.

The API needs a key (env ``GOOGLE_CIVIC_API_KEY``); without one the source is
skipped. Google's terms allow keeping contest data for at most 24 hours, so
``expires`` is set: the daily refresh replaces the file, and once Google stops
serving an election the file is deleted rather than kept.
"""

from __future__ import annotations

import heapq
import io
import json
import logging
import os
import re
import threading
import time
import urllib.error
import zipfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, NoReturn
from urllib.parse import urlencode
from xml.etree import ElementTree

from who_is_running.districts import Districts, Plan, county_name
from who_is_running.http import Fetcher, NotFound, http_get
from who_is_running.models import STATEWIDE, Candidacy, Level, join_counties
from who_is_running.sources.base import Source
from who_is_running.text import decode

log = logging.getLogger(__name__)

API = "https://civicinfo.googleapis.com/civicinfo/v2"
KEY_ENV = "GOOGLE_CIVIC_API_KEY"
GOVT_UNITS = "https://www2.census.gov/programs-surveys/gus/datasets/2022/govt_units_2022.ZIP"
"""Census of Governments 2022: name, mailing address and county of every US government."""
FIRST_YEAR = 2026
OFFICIAL_LISTS = frozenset({"MD", "NC"})
"""States with an official candidate-list source (``md``, ``nc``): not sampled, to avoid
listing their races twice."""

MAX_CALLS = 4000
"""voterinfo lookups per election-year run. Google's free quota is 25,000 a day."""
RATE = 8.0
"""Requests per second to start at, across all workers. Each 429 answer halves it."""
WORKERS = 4
THROTTLE_WAIT = 60.0
"""Seconds every worker pauses after a 429 (over the per-minute quota), unless Google's
Retry-After asks for longer."""
MAX_THROTTLES = 8
"""429 answers tolerated in one run before giving up."""
TIME_BUDGET = 40 * 60.0
"""Seconds of lookups per election-year run. The lookups left over are skipped, keeping what
was found, so the refresh job (60-minute limit) still finishes and commits."""
MAX_TRANSIENT = 25
"""Network errors and 5xx answers tolerated (after http_get's retries) before giving up."""

SHARED_SCOPES = frozenset({"statewide", "stateUpper", "stateLower", "judicial"})
"""District scopes whose contests can span counties, so they're merged across addresses.
Contests of other scopes are kept per sampled county: their names ("DISTRICT 1") aren't
unique across a state."""
STATE_SCOPES = SHARED_SCOPES
FEDERAL_SCOPES = frozenset({"congressional", "national"})
FEDERAL_OFFICE = re.compile(
    r"^\s*(UNITED STATES|U\.?\s?S\.?)\s+(SENAT|REPRESENTATIVE|HOUSE|CONGRESS)"
    r"|REPRESENTATIVE IN CONGRESS|\bMEMBER OF CONGRESS\b|\bPRESIDENT OF THE UNITED STATES\b",
    re.IGNORECASE,
)
DISTRICT_NUMBER = re.compile(
    r"\b(?:DISTRICT|DIST\.?|WARD|SEAT|PLACE|ZONE|SUBDISTRICT)\s*(?:NO\.?|#)?\s*(\d+[A-Z]?)\s*$"
    r"|^\s*(\d+)(?:ST|ND|RD|TH)\b",
    re.IGNORECASE,
)
STREET = re.compile(r"^\d+[A-Z]?(?:-\d+)?\s+\S")
PO_BOX = re.compile(r"\bBOX\b", re.IGNORECASE)
WRITE_IN = re.compile(r"^\s*write[\s-]*in\b", re.IGNORECASE)
PARTIES = {
    "democratic": "DEM",
    "democrat": "DEM",
    "democratic party": "DEM",
    "republican": "REP",
    "republican party": "REP",
    "libertarian": "LIB",
    "libertarian party": "LIB",
    "green": "GRE",
    "green party": "GRE",
    "unaffiliated": "UNA",
}
NO_PARTY = frozenset({"none", "nonpartisan", "non-partisan", "no party preference"})
ELECTION_TYPES = {
    "general": "general",
    "primary": "primary",
    "run-off": "runoff",
    "runoff": "runoff",
}


class CivicError(Exception):
    """A failure talking to the API. The message never contains the API key."""


@dataclass(frozen=True)
class Seed:
    """One address to look up, and the county it's in."""

    state: str
    county: str
    address: str
    population: int = 0
    """The county's, used to give big counties more lookups."""


@dataclass(frozen=True)
class Election:
    id: str
    name: str
    day: str
    state: str
    """Two-letter state the election is limited to, or "" for a national election."""


@dataclass
class Found:
    """A contest seen at one or more sampled addresses."""

    election: Election
    state: str
    contest: dict[str, Any]
    county: str
    """The sampled county, for contests kept per county; "" for shared ones."""
    counties: set[str] = field(default_factory=set)


class CivicSource(Source):
    name = "civic"
    description = (
        "Google Civic Information API (Voting Information Project): upcoming ballots, "
        "all states, sampled by county (needs GOOGLE_CIVIC_API_KEY)"
    )
    expires = True

    def __init__(
        self,
        fetch: Fetcher = http_get,
        *,
        key: str | None = None,
        today: Callable[[], date] = date.today,
        max_calls: int = MAX_CALLS,
        rate: float = RATE,
        workers: int = WORKERS,
        budget: float = TIME_BUDGET,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(fetch)
        self._key = key
        self.today = today
        self.max_calls = max_calls
        self.rate = rate
        self.workers = workers
        self.budget = budget
        self.clock = clock
        self.sleep = sleep

    def available_years(self) -> list[int]:
        return list(range(FIRST_YEAR, self.today().year + 1))

    def refresh_years(self, today: date) -> list[int]:
        # Past elections are gone from the API; asking again just clears the file.
        return [today.year - 1, today.year] if today.year > FIRST_YEAR else [today.year]

    def candidacies(self, year: int) -> list[Candidacy]:
        key = self._key if self._key is not None else os.environ.get(KEY_ENV, "")
        if not key:
            raise NotFound(f"{KEY_ENV} is not set, so the Civic Information API is skipped")
        client = _Client(
            self.fetch, key, rate=self.rate, budget=self.budget, clock=self.clock, sleep=self.sleep
        )
        elections = upcoming(client.elections(), year, self.today())
        if not elections:
            return []
        seeds = [
            s for s in parse_govt_units(client.get(GOVT_UNITS)) if s.state not in OFFICIAL_LISTS
        ]
        calls = plan_calls(elections, seeds, self.max_calls)
        found: dict[tuple[str, ...], Found] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            answers = list(pool.map(lambda call: client.voterinfo(*call), calls))
        with_contests = 0
        for (election, seed), answer in zip(calls, answers, strict=True):
            if answer is None:
                continue
            where = answer.get("normalizedInput") or {}
            if where.get("state", seed.state).upper() != seed.state:
                continue  # geocoded into another state: its contests aren't this county's
            contests = answer.get("contests") or []
            with_contests += bool(contests)
            for contest in contests:
                collect(found, election, seed, contest)
        log.info(
            "civic: %d lookups for %s, %d skipped (out of time), %d with no data, "
            "%d with contests, %d contests kept",
            len(calls),
            ", ".join(e.name for e in elections),
            client.skipped,
            sum(a is None for a in answers) - client.skipped,
            with_contests,
            len(found),
        )
        return sorted(to_candidacies(found.values(), year, Districts(self.fetch)))


class _Client:
    """API calls, paced and with the key kept out of every error message."""

    def __init__(
        self,
        fetch: Fetcher,
        key: str,
        *,
        rate: float,
        budget: float = TIME_BUDGET,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.fetch = fetch
        self.key = key
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._next = 0.0
        self._deadline = clock() + budget
        self._paused_until = 0.0
        self._throttles = 0
        self._transient = 0
        self._stop: CivicError | None = None
        self.skipped = 0

    def redact(self, text: str) -> str:
        text = re.sub(r"([?&]key=)[^&\s]*", r"\1REDACTED", text)
        return text.replace(self.key, "REDACTED") if self.key else text

    def get(self, url: str) -> bytes:
        """A plain GET (public files). Fails loudly: without addresses there's nothing to do."""
        try:
            return self.fetch(url)
        except Exception as err:
            raise CivicError(f"{type(err).__name__}: {self.redact(str(err))}") from None

    def elections(self) -> list[dict[str, Any]]:
        url = f"{API}/elections?{urlencode({'key': self.key})}"
        try:
            body = self.fetch(url)
        except NotFound as err:
            code = getattr(err.__cause__, "code", 404)
            raise CivicError(
                f"elections query refused (HTTP {code}): check that the key is allowed to use "
                "the Civic Information API"
            ) from None
        except urllib.error.HTTPError as err:
            raise CivicError(
                f"elections query failed (HTTP {err.code}); is the key valid?"
            ) from None
        except Exception as err:
            raise CivicError(f"elections query failed: {self.redact(str(err))}") from None
        found: list[dict[str, Any]] = json.loads(decode(body)).get("elections") or []
        return found

    def voterinfo(self, election: Election, seed: Seed) -> dict[str, Any] | None:
        """The address's ballot, or None when Google has no data for it or time ran out."""
        query = urlencode({"address": seed.address, "electionId": election.id, "key": self.key})
        while True:
            if self._stop is not None:
                # A new exception each time: re-raising one object grows its traceback.
                raise CivicError(str(self._stop)) from None
            if not self._pace():
                with self._lock:
                    self.skipped += 1
                return None
            try:
                body = self.fetch(f"{API}/voterinfo?{query}")
                answer: dict[str, Any] = json.loads(decode(body))
                return answer
            except NotFound as err:
                code = getattr(err.__cause__, "code", 404)
                if code == 404:  # "No information for this address"
                    return None
                self._fail(f"voterinfo refused (HTTP {code}): quota or key restrictions?")
            except urllib.error.HTTPError as err:
                if err.code == 400:  # "Failed to parse address", "Election unknown"
                    return None
                if err.code == 429:  # over quota: wait, then retry this address
                    self._throttle(err)
                    continue
                if err.code < 500:
                    self._fail(f"voterinfo failed (HTTP {err.code})")
                self._transient_error(f"HTTP {err.code}")
            except (OSError, ValueError) as err:  # URLError, timeouts, bad JSON
                self._transient_error(f"{type(err).__name__}: {self.redact(str(err))}")
            return None

    def _pace(self) -> bool:
        """Wait for this lookup's turn. False once the run's time budget is spent."""
        with self._lock:
            now = self.clock()
            if now >= self._deadline:
                return False
            wait = self._next - now
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            self.sleep(wait)
        return True

    def _throttle(self, err: urllib.error.HTTPError) -> None:
        """Over the per-minute quota: every worker waits, then goes on at half the rate."""
        reason = self.redact(_google_error(err))
        if "per day" in reason.lower():
            self._fail(f"voterinfo failed (HTTP 429), daily quota used up: {reason}")
        with self._lock:
            now = self.clock()
            if now < self._paused_until:
                return  # another worker's 429 already paused everyone
            self._throttles += 1
            count = self._throttles
            if count <= MAX_THROTTLES:
                self.interval = self.interval * 2 if self.interval else 1.0
                wait = max(THROTTLE_WAIT, _retry_after(err))
                self._paused_until = self._next = now + wait
                rate = 1 / self.interval
        if count > MAX_THROTTLES:
            self._fail(f"voterinfo failed (HTTP 429) {count} times; last: {reason}")
        log.warning(
            "civic: over the API quota (%s); pausing %.0fs, then %.2f lookups a second",
            reason,
            wait,
            rate,
        )

    def _transient_error(self, what: str) -> None:
        with self._lock:
            self._transient += 1
            count = self._transient
        log.warning("civic: voterinfo lookup failed (%s)", what)
        if count > MAX_TRANSIENT:
            self._fail(f"more than {MAX_TRANSIENT} voterinfo lookups failed; last: {what}")

    def _fail(self, message: str) -> NoReturn:
        # Stop the other workers too, so a quota error doesn't burn the rest of the calls.
        self._stop = CivicError(message)
        raise self._stop


def _google_error(err: urllib.error.HTTPError) -> str:
    """Google's explanation of an error answer, e.g. which quota was exceeded."""
    try:
        message = str(json.loads(decode(err.read()))["error"]["message"])
    except Exception:
        return f"HTTP {err.code}"
    return " ".join(message.split())[:300]


def _retry_after(err: urllib.error.HTTPError) -> float:
    try:
        return float(err.headers.get("Retry-After", 0))
    except (AttributeError, TypeError, ValueError):
        return 0.0


def upcoming(elections: list[dict[str, Any]], year: int, today: date) -> list[Election]:
    """The year's elections still being served (not yet held), national ones first."""
    out = []
    for e in elections:
        day = str(e.get("electionDay", ""))
        name = str(e.get("name", ""))
        if not day.startswith(f"{year}-") or day < today.isoformat() or "test" in name.lower():
            continue
        state = re.search(r"/state:([a-z]{2})\b", str(e.get("ocdDivisionId", "")))
        out.append(Election(str(e["id"]), name, day, state.group(1).upper() if state else ""))
    return sorted(out, key=lambda e: (e.state != "", e.day, e.id))


def plan_calls(
    elections: list[Election], seeds: list[Seed], budget: int
) -> list[tuple[Election, Seed]]:
    """One address per county per election, then more addresses in proportion to population.

    ``seeds`` lists each county's addresses best first. The extra lookups go to the county
    with the most people per lookup so far, so a county of a million gets about ten times
    the lookups of one with a hundred thousand.
    """
    by_county: dict[tuple[str, str], list[Seed]] = defaultdict(list)
    for seed in seeds:
        by_county[seed.state, seed.county].append(seed)
    first: list[tuple[Election, Seed]] = []
    extra: list[tuple[float, int, Election, Seed]] = []
    for election in elections:
        for county, found in by_county.items():
            if election.state in ("", county[0]):
                first.append((election, found[0]))
                # The k-th extra lookup's priority: people per lookup once it's made.
                extra.extend(
                    (-seed.population / (k + 1), len(extra) + k, election, seed)
                    for k, seed in enumerate(found[1:], start=1)
                )
    room = max(budget - len(first), 0)
    return first[:budget] + [(e, s) for _, _, e, s in heapq.nsmallest(room, extra)]


def collect(
    found: dict[tuple[str, ...], Found], election: Election, seed: Seed, contest: Any
) -> None:
    """Add one contest seen at one address (referendums and federal races are skipped)."""
    if not isinstance(contest, dict) or not contest.get("candidates"):
        return  # referendums and ballot measures have no candidates
    if contest.get("referendumTitle") or str(contest.get("type", "")).lower() in (
        "referendum",
        "ballot-measure",
    ):
        return
    if level_of(contest) is None:
        return  # federal: the fec source covers these, for every district
    district = contest.get("district") or {}
    shared = district.get("scope", "") in SHARED_SCOPES or (
        not district.get("scope") and level_of(contest) is Level.STATE
    )
    key = (
        election.id,
        seed.state,
        office_of(contest),
        str(contest.get("type", "")),
        ",".join(contest.get("primaryParties") or []),
        str(district.get("scope", "")),
        str(district.get("id", "")),
        str(district.get("name", "")),
        "" if shared else seed.county,
    )
    entry = found.get(key)
    if entry is None:
        entry = found[key] = Found(election, seed.state, contest, "" if shared else seed.county)
    entry.counties.add(seed.county)


def office_of(contest: dict[str, Any]) -> str:
    return str(contest.get("office") or contest.get("ballotTitle") or "").strip()


def level_of(contest: dict[str, Any]) -> Level | None:
    """The contest's level; None for federal offices."""
    levels = contest.get("level") or []
    scope = (contest.get("district") or {}).get("scope", "")
    if "country" in levels or scope in FEDERAL_SCOPES or FEDERAL_OFFICE.search(office_of(contest)):
        return None
    if levels:
        return Level.STATE if "administrativeArea1" in levels else Level.LOCAL
    return Level.STATE if scope in STATE_SCOPES else Level.LOCAL


def district_of(district: dict[str, Any]) -> str:
    if district.get("scope") == "statewide":
        return ""
    raw = str(district.get("id") or "").strip()
    if raw.startswith("ocd-division/"):
        raw = raw.rsplit(":", 1)[-1]
    if raw:
        return raw
    match = DISTRICT_NUMBER.search(str(district.get("name") or ""))
    return (match.group(1) or match.group(2)) if match else ""


def party_code(party: str) -> str:
    party = re.sub(r"^\s*party preference:\s*", "", party or "", flags=re.IGNORECASE).strip()
    if party.lower() in NO_PARTY:
        return ""
    return PARTIES.get(party.lower(), party)


def election_type(contest: dict[str, Any], election: Election) -> str:
    kind = ELECTION_TYPES.get(str(contest.get("type", "")).strip().lower())
    if kind:
        return kind
    name = election.name.lower()
    for word, value in (("runoff", "runoff"), ("primary", "primary"), ("general", "general")):
        if word in name:
            return value
    return ""


def to_candidacies(found: Iterable[Found], year: int, districts: Districts) -> list[Candidacy]:
    out: set[Candidacy] = set()
    for entry in found:
        contest = entry.contest
        level = level_of(contest)
        assert level is not None
        district = contest.get("district") or {}
        scope = district.get("scope", "")
        seat = district_of(district)
        plan: Plan | None = None
        if scope == "stateUpper":
            plan = districts.state_senate()
        elif scope == "stateLower":
            plan = districts.state_house()
        if scope == "statewide":
            counties = STATEWIDE
        else:
            # Legislative districts: the 2024 Census lines, unless an address was found
            # outside them (the district was redrawn); else the counties it was seen in.
            lines = plan.counties(entry.state, seat) if plan is not None and seat else ""
            fits = lines == STATEWIDE or entry.counties <= set(lines.split(";"))
            counties = lines if lines and fits else join_counties(entry.counties)
        for candidate in contest.get("candidates") or []:
            name = str(candidate.get("name") or "").strip() if isinstance(candidate, dict) else ""
            if not name or WRITE_IN.match(name):
                continue
            out.add(
                Candidacy(
                    state=entry.state,
                    election_year=year,
                    election_date=entry.election.day,
                    level=level,
                    office=office_of(contest),
                    district=seat,
                    jurisdiction=entry.county if level is Level.LOCAL else "",
                    counties=counties,
                    candidate_name=name,
                    party=party_code(str(candidate.get("party") or "")),
                    election_type=election_type(contest, entry.election),
                    incumbent_status="",
                    candidate_status="",
                    source="civic",
                    source_candidate_id="",
                )
            )
    return sorted(out)


# --- Census of Governments: one or more street addresses per county ---

SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def parse_govt_units(raw: bytes) -> list[Seed]:
    """Street addresses from the Census list, each county's best first, biggest counties first.

    Per county: the county government, then cities and townships by population, then
    school districts by enrollment. PO boxes are skipped (the API needs a street address).
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as outer:
        inner = next(n for n in outer.namelist() if n.lower().endswith(".xlsx"))
        book = zipfile.ZipFile(io.BytesIO(outer.read(inner)))
    strings = shared_strings(book)
    candidates: dict[tuple[str, str], list[tuple[tuple[int, int], Seed]]] = defaultdict(list)
    size: dict[tuple[str, str], int] = defaultdict(int)
    for sheet, size_column in (
        ("General Purpose", "POPULATION"),
        ("School District", "ENROLLMENT"),
    ):
        for row in read_sheet(book, sheet, strings):
            if row.get("IS_ACTIVE", "Y") == "N":
                continue
            state, county = (
                row.get("STATE", "").strip(),
                county_name(row.get("COUNTY_AREA_NAME", "")),
            )
            street = next(
                (a for a in (row.get("ADDRESS1", ""), row.get("ADDRESS2", "")) if is_street(a)), ""
            )
            people = _int(row.get(size_column, ""))
            county_gov = row.get("UNIT_TYPE", "").startswith("1")
            if sheet == "General Purpose":
                size[state, county] = max(size[state, county], people)
            if not (state and county and street and row.get("CITY")):
                continue
            zip_code = row.get("ZIP", "").strip()
            zip_code = zip_code.zfill(5) if zip_code.isdigit() else zip_code
            address = (
                f"{' '.join(street.split())}, {row['CITY'].strip()}, {state} {zip_code}".strip()
            )
            rank = (0 if county_gov else 1 if sheet == "General Purpose" else 2, -people)
            candidates[state, county].append((rank, Seed(state, county, address)))
    out = []
    for place in sorted(candidates, key=lambda c: (-size[c], c)):
        seen: set[str] = set()
        for _, seed in sorted(candidates[place], key=lambda x: x[0]):
            if seed.address not in seen:
                seen.add(seed.address)
                out.append(replace(seed, population=size[place]))
    return out


def is_street(address: str) -> bool:
    address = address.strip()
    return bool(STREET.match(address)) and not PO_BOX.search(address)


def _int(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return 0


def read_sheet(book: zipfile.ZipFile, name: str, strings: list[str]) -> Iterator[dict[str, str]]:
    """Rows of one worksheet of an .xlsx as dicts keyed by the header row."""
    workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
    rels = ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target", "") for r in rels.iter(f"{PKG_REL_NS}Relationship")}
    rel_id = next(
        (s.get(f"{REL_NS}id") for s in workbook.iter(f"{SHEET_NS}sheet") if s.get("name") == name),
        None,
    )
    if rel_id is None:
        return
    target = targets[rel_id].lstrip("/")
    path = target if target.startswith("xl/") else f"xl/{target}"
    header: dict[str, str] | None = None
    for _, element in ElementTree.iterparse(book.open(path)):
        if element.tag != f"{SHEET_NS}row":
            continue
        cells = {}
        for cell in element.iter(f"{SHEET_NS}c"):
            column = re.sub(r"\d", "", cell.get("r", ""))
            kind = cell.get("t")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{SHEET_NS}t"))
            else:
                v = cell.find(f"{SHEET_NS}v")
                value = v.text or "" if v is not None else ""
                if kind == "s" and value:
                    value = strings[int(value)]
            cells[column] = value
        element.clear()
        if header is None:
            header = cells
        else:
            yield {header[c]: v for c, v in cells.items() if c in header}


def shared_strings(book: zipfile.ZipFile) -> list[str]:
    """The workbook's string table (cells of type "s" hold an index into it)."""
    strings: list[str] = []
    if "xl/sharedStrings.xml" in book.namelist():
        for _, element in ElementTree.iterparse(book.open("xl/sharedStrings.xml")):
            if element.tag == f"{SHEET_NS}si":
                strings.append("".join(t.text or "" for t in element.iter(f"{SHEET_NS}t")))
                element.clear()
    return strings
