"""Unofficial, election-night county results from state results sites.

Many states publish live results through Clarity (Scytl) Election Night
Reporting. Every Clarity site has the same layout::

    <site>/elections.json                      the site's elections: EID, name, date
    <site>/<EID>/current_ver.txt               the latest published version
    <site>/<EID>/<version>/reports/detailxml.zip
        detail.xml: every contest, each candidate's votes by county and vote type,
        and precincts reported per county

Florida and Minnesota publish their own files instead: one text file per
election with every race's votes by county (``parse_florida``, ``parse_minnesota``).

The results are unofficial and change all night (and for weeks of canvassing),
so the map labels them as such, and certified MEDSL results replace them once
they exist (see ``build_site.build_results``).

The same files list every contest on the state's ballot before any votes are
counted, so they double as candidate lists: state, legislative, judicial and
the local races the state's site carries (``candidacies``). Federal races are
left to ``fec``; ballot questions and retention votes have no candidates.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

from who_is_running.districts import COUNTY_NAMES_2010, county_name, parse_county_names
from who_is_running.http import NotFound
from who_is_running.models import STATEWIDE, Candidacy, Level, join_counties
from who_is_running.results import CountyResult
from who_is_running.sources.base import Source
from who_is_running.sources.medsl import NOT_OFFICES
from who_is_running.text import decode

log = logging.getLogger(__name__)

CLARITY = "https://results.enr.clarityelections.com"
GZIP_MAGIC = bytes([0x1F, 0x8B])


@dataclass(frozen=True)
class Feed:
    """One state's results site."""

    site: str
    """Clarity: the state's site root, e.g. ``https://results.enr.clarityelections.com/IA``.
    Other kinds: the results file's URL."""
    election_id: str = ""
    """Clarity: the general election's EID. Blank: look it up in elections.json by date."""
    kind: str = "clarity"
    """"clarity", "florida" or "minnesota": which parser reads it."""


ELECTION_DAYS = {2026: date(2026, 11, 3)}
LIVE_WINDOW = timedelta(days=4)
"""Election day to Nov 6: .github/workflows/live-results.yml's schedule."""

FEEDS: dict[int, dict[str, Feed]] = {
    2026: {
        # Statewide Clarity sites, with the 2026 general's ID (each checked on
        # 2026-10-31: zero votes, every statewide contest present). A blank ID
        # is looked up in the site's elections.json by date; South Carolina's
        # and West Virginia's lists are empty, so theirs must be set here.
        "CO": Feed(f"{CLARITY}/CO", "127407"),
        "IA": Feed(f"{CLARITY}/IA", "127077"),
        "SC": Feed("https://www.enr-scvotes.org/SC", "127300"),
        "WV": Feed(f"{CLARITY}/WV", "127496"),
        # The states' own files, named by election date.
        "FL": Feed(
            "https://flelectionfiles.floridados.gov/enightfilespublic/20261103_ElecResultsFL.txt",
            kind="florida",
        ),
        "MN": Feed(
            "https://electionresultsfiles.sos.mn.gov/20261103/allracesbycounty.txt",
            kind="minnesota",
        ),
    },
}

# Choices that aren't candidates.
NOT_CANDIDATES = re.compile(
    r"^(write[- ]?ins?|over ?votes?|under ?votes?|(estimated )?blanks?|scattering|total|"
    r"times (blank|over) voted)( votes?)?$",
    re.I,
)
# Ballot questions, and retention votes ("Supreme Court Justice - Boatright"):
# Colorado doesn't mark either as a question, but their choices are Yes/No.
YES_NO = re.compile(r"^(yes|no|for|against)\b", re.I)

# Clarity's contest names for the offices the national map shows, in the
# spelling MEDSL's results use (see build_site.NATIONAL_RACES).
OFFICES = [
    (re.compile(r"^(U\.?S\.? )?(President(ial Electors)?)\b", re.I), "US President"),
    (
        re.compile(r"^(United States|U\.? ?S\.?) Senat(e|or)\b.*\b(special|unexpired)\b", re.I),
        "US Senate (special)",
    ),
    (re.compile(r"^(United States|U\.? ?S\.?) Senat(e|or)\b", re.I), "US Senate"),
    (
        re.compile(r"^Governor( ?(/|and|&) ?(Lieutenant|Lt\.?) Governor)?$", re.I),
        "GOVERNOR",
    ),
]

# Candidate lists: federal races are left to the fec source.
FEDERAL = re.compile(
    r"^(U\.? ?S\.? |United States |President)|\bUnited States (Senat|Congress|Represent)|"
    r"\bRepresentative in Congress\b",
    re.I,
)
# Offices of a county, city or special district; anything else not on every
# county's ballot is a state office (legislature, courts, district boards).
LOCAL_OFFICES = re.compile(
    r"\b(county|city|town|village|township|municipal|borough|school|watershed|soil and water|"
    r"fire|park|hospital|library|transportation district|development district|"
    r"improvement district|tax district|service area|mayor|council|sheriff|coroner|"
    r"recorder|surveyor|assessor)\b",
    re.I,
)
# "State Senator - District 8", "State Senate District 15 (Unexpired Term)",
# "Solicitor, Circuit 3", "School Trustee District 2 At Large".
DISTRICT_AFTER = re.compile(
    r"^(?P<office>.*?)[\s,-]*\b(?:Congressional\s+)?(?:District|Circuit)\s+"
    r"(?P<district>\d+[A-Z]?|[A-Z])(?P<rest>(?:\s+At[- ]Large|\s*[-,(].*)?)$",
    re.I,
)
# West Virginia: "STATE SENATOR, 3rd Senatorial District - UNEXPIRED TERM".
DISTRICT_BEFORE = re.compile(
    r"^(?P<office>.*?)[\s,-]*\b(?P<district>\d+)(?:st|nd|rd|th)\s+"
    r"(?:Senatorial\s+|Delegate\s+|Congressional\s+)?District(?P<rest>(?:\s*[-,(].*)?)$",
    re.I,
)
# Party codes that mean "no party": nonpartisan races, ballot-order numbers.
NO_PARTY = {"NON", "NOP", "NP", "NONE"}
WRITE_IN_PARTY = "WRI"
"""Florida's code for a qualified write-in candidate."""

TIME_ZONES = {
    "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
    "AKST": -9, "AKDT": -8, "HST": -10,
}  # fmt: skip


class EnrSource(Source):
    name = "enr"
    description = (
        "State results sites (Clarity ENR, Florida, Minnesota): the general election's "
        "state and local candidates, and unofficial election-night county results for "
        "statewide races"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._status: dict[int, dict[str, Any]] = {}
        self._counties: dict[str, tuple[str, str]] | None = None
        self._feeds: dict[tuple[int, str], tuple[str, str] | None] = {}

    def available_years(self) -> list[int]:
        return sorted(FEEDS)

    def refresh_years(self, today: date) -> list[int]:
        # Before election day the files list the candidates. Counts keep changing
        # through the canvass, so the daily refresh picks them up for the rest of
        # the year. In between the live-results workflow pulls them, and the two
        # mustn't commit the same files at once.
        day = ELECTION_DAYS.get(today.year)
        return [today.year] if day and (today < day or today >= day + LIVE_WINDOW) else []

    def candidacies(self, year: int) -> list[Candidacy]:
        out: set[Candidacy] = set()
        for state, feed in sorted(FEEDS.get(year, {}).items()):
            found = self._feed(year, state, feed)
            if found is None:
                continue
            text = found[0]
            if feed.kind == "clarity":
                contests, every = detail_contests(text)
            elif feed.kind == "florida":
                contests, every = line_contests(florida_lines(text, state, self._county_fips()))
            else:
                contests, every = line_contests(minnesota_lines(text, state, self._county_names()))
            out.update(to_candidacies(contests, every, state, year))
        return sorted(out)

    def results(self, year: int) -> list[CountyResult]:
        out: list[CountyResult] = []
        self._status[year] = {}
        for state, feed in sorted(FEEDS.get(year, {}).items()):
            found = self._state_results(year, state, feed)
            if found:
                rows, status = found
                out.extend(rows)
                self._status[year][state] = status
        return sorted(out)

    def results_status(self, year: int) -> dict[str, Any]:
        return self._status.get(year, {})

    def _state_results(
        self, year: int, state: str, feed: Feed
    ) -> tuple[list[CountyResult], dict[str, Any]] | None:
        found = self._feed(year, state, feed)
        if found is None:
            return None
        text, url = found
        if feed.kind == "florida":
            rows, status = parse_florida(text, state, year, self._county_fips())
        elif feed.kind == "minnesota":
            rows, status = parse_minnesota(text, state, year, self._county_names())
        else:
            rows, status = parse_detail(text, state, year, self._county_fips())
        return self._with_votes(state, rows, status, url)

    def _feed(self, year: int, state: str, feed: Feed) -> tuple[str, str] | None:
        """(text, URL) of a state's file, or None if it isn't posted. Downloaded once a
        run: ``candidacies`` and ``results`` read the same file."""
        if (year, state) not in self._feeds:
            self._feeds[year, state] = self._download(year, state, feed)
        return self._feeds[year, state]

    def _download(self, year: int, state: str, feed: Feed) -> tuple[str, str] | None:
        if feed.kind != "clarity":
            try:
                return decode(self._get(feed.site)), feed.site
            except NotFound:
                log.warning("enr: no results file for %s yet", state)
                return None
        eid = feed.election_id or self._find_election(feed, ELECTION_DAYS[year])
        if not eid:
            log.warning("enr: no %d general election listed for %s yet", year, state)
            return None
        try:
            version = decode(self._get(f"{feed.site}/{eid}/current_ver.txt")).strip()
        except NotFound:
            log.warning("enr: %s election %s not found", state, eid)
            return None
        # Missing or broken files past this point mean the site is mid-update:
        # let the error through so the previous file is kept, rather than
        # dropping the state from the map until the next run.
        archive = self._get(f"{feed.site}/{eid}/{version}/reports/detailxml.zip")
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            return decode(zf.read("detail.xml")), f"{feed.site}/{eid}/"

    @staticmethod
    def _with_votes(
        state: str, rows: list[CountyResult], status: dict[str, Any], url: str
    ) -> tuple[list[CountyResult], dict[str, Any]] | None:
        if not any(r.votes for r in rows):
            log.info("enr: %s has no votes counted yet", state)
            return None
        status["url"] = url
        return rows, status

    def _find_election(self, feed: Feed, day: date) -> str:
        try:
            listed = json.loads(decode(self._get(f"{feed.site}/elections.json")))
        except NotFound:
            return ""
        return find_election(listed, day)

    def _get(self, url: str) -> bytes:
        # Clarity gzips some files whether or not the client asked for it.
        body = self.fetch(url)
        return gzip.decompress(body) if body.startswith(GZIP_MAGIC) else body

    def _county_names(self) -> dict[str, tuple[str, str]]:
        """County code -> (state, name), from the Census list."""
        if self._counties is None:
            self._counties = parse_county_names(decode(self.fetch(COUNTY_NAMES_2010)))
        return self._counties

    def _county_fips(self) -> dict[tuple[str, str], str]:
        """(state, county_key(name)) -> county code."""
        names = self._county_names()
        return {(st, county_key(name)): fips for fips, (st, name) in names.items()}


def election_day(year: int) -> str:
    """The general election's ISO date, when ``ELECTION_DAYS`` has it."""
    day = ELECTION_DAYS.get(year)
    return day.isoformat() if day else ""


def find_election(listed: list[dict[str, str]], day: date) -> str:
    """The EID of the general election held on ``day``, from a site's elections.json."""
    on_day = [
        e for e in listed if e.get("Date", "").split(" ")[0] == f"{day.month}/{day.day}/{day.year}"
    ]
    general = [e for e in on_day if "general" in e.get("ElectionName", "").lower()]
    picked = (general or on_day)[:1]
    return picked[0].get("EID", "") if picked else ""


def county_key(name: str) -> str:
    """Spellings of a county name compare equal: "St. Mary's", "Saint Marys", "ST MARYS"."""
    key = county_name(name).replace("SAINT ", "ST ")
    return re.sub(r"[^A-Z]", "", key)


def office_of(contest: str) -> str:
    contest = " ".join(contest.split())  # South Carolina: "U.S.  Senate"
    for pattern, office in OFFICES:
        if pattern.search(contest):
            return office
    return contest.upper()


def candidate_of(text: str, party: str) -> str:
    """South Carolina prefixes names with the party: "REP Donald J Trump/JD Vance"."""
    text = " ".join(text.split())
    if party and text.upper().startswith(party.upper() + " "):
        text = text[len(party) + 1 :]
    return text


def updated_at(stamp: str) -> str:
    """Clarity's "11/3/2026 9:04:57 PM CST" as ISO 8601 with its UTC offset; "" if unreadable."""
    parts = stamp.split()
    if len(parts) != 4 or parts[3] not in TIME_ZONES:
        return ""
    try:
        local = datetime.strptime(" ".join(parts[:3]), "%m/%d/%Y %I:%M:%S %p")
    except ValueError:
        return ""
    return local.replace(tzinfo=timezone(timedelta(hours=TIME_ZONES[parts[3]]))).isoformat()


def _detail_root(xml: str) -> ElementTree.Element:
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("enr: refusing an XML file with a DTD")
    return ElementTree.fromstring(xml)


def parse_detail(
    xml: str, state: str, year: int, fips: dict[tuple[str, str], str]
) -> tuple[list[CountyResult], dict[str, Any]]:
    """A Clarity detail.xml as county results for its statewide races, plus reporting status."""
    root = _detail_root(xml)

    def code(county: str) -> str:
        found = fips.get((state, county_key(county)), "")
        if not found:
            log.warning("enr: no county code for %s, %s", county, state)
        return found

    counties: dict[str, list[int]] = {}
    for c in root.iterfind("ElectionVoterTurnout/Counties/County"):
        counties[c.get("name", "")] = [
            int(c.get("precinctsReported", "0") or 0),
            int(c.get("precinctsParticipating", "0") or 0),
        ]

    rows: list[CountyResult] = []
    for contest in root.iterfind("Contest"):
        text = contest.get("text", "")
        participating = {
            c.get("name", "") for c in contest.iterfind("ParticipatingCounties/County")
        }
        # Only races on every county's ballot; not ballot questions or party tickets.
        if (
            contest.get("isQuestion") == "true"
            or NOT_OFFICES.search(text.upper())
            or not counties
            or participating != set(counties)
        ):
            continue
        choices = [
            ch for ch in contest.iterfind("Choice") if not NOT_CANDIDATES.match(ch.get("text", ""))
        ]
        if not choices or all(YES_NO.match(ch.get("text", "").strip()) for ch in choices):
            continue
        office = office_of(text)
        for choice in choices:
            party = choice.get("party", "").strip()
            name = candidate_of(choice.get("text", ""), party)
            for county, votes in votes_by_county(choice).items():
                rows.append(
                    CountyResult(
                        state=state,
                        election_year=year,
                        election_date=election_day(year),
                        stage="general",
                        office=office,
                        county_fips=code(county),
                        county=county_name(county),
                        candidate_name=name,
                        party=party,
                        votes=votes,
                        source="enr",
                    )
                )

    reported = sum(r for r, _ in counties.values())
    total = sum(t for _, t in counties.values())
    status: dict[str, Any] = {
        "updated": updated_at(root.findtext("Timestamp", "").strip()),
        "precincts_reported": reported,
        "precincts_total": total,
        "counties": {f: v for f, v in ((code(c), v) for c, v in sorted(counties.items())) if f},
    }
    return rows, status


def votes_by_county(choice: ElementTree.Element) -> dict[str, int]:
    """Sum a candidate's vote types per county, or use a "Total" type when there is one."""
    types = choice.findall("VoteType")
    totals = [t for t in types if t.get("name", "").lower().startswith("total")]
    out: dict[str, int] = {}
    for vote_type in totals or types:
        for county in vote_type.iterfind("County"):
            name = county.get("name", "")
            out[name] = out.get(name, 0) + int(county.get("votes", "0") or 0)
    return out


@dataclass(frozen=True)
class Contest:
    """One contest on a state's ballot, for the candidate list."""

    name: str
    counties: frozenset[str]
    """The counties it's on the ballot in, spelled as ``county_name`` spells them."""
    candidates: tuple[tuple[str, str], ...]
    """(name, party code) in ballot order; write-ins and the like left out."""


def detail_contests(xml: str) -> tuple[list[Contest], frozenset[str]]:
    """A Clarity detail.xml's candidate contests, and every county the site covers."""
    root = _detail_root(xml)
    every = frozenset(
        county_name(c.get("name", ""))
        for c in root.iterfind("ElectionVoterTurnout/Counties/County")
    )
    contests = []
    for contest in root.iterfind("Contest"):
        if contest.get("isQuestion") == "true":
            continue
        candidates = []
        for choice in contest.iterfind("Choice"):
            party = choice.get("party", "").strip()
            text = " ".join(choice.get("text", "").split())
            if not NOT_CANDIDATES.match(text):
                candidates.append((candidate_of(text, party), party))
        contests.append(
            Contest(
                " ".join(contest.get("text", "").split()),
                frozenset(
                    county_name(c.get("name", ""))
                    for c in contest.iterfind("ParticipatingCounties/County")
                ),
                tuple(candidates),
            )
        )
    return contests, every


def line_contests(lines: list[Line]) -> tuple[list[Contest], frozenset[str]]:
    """A state results file's contests, and every county in the file.

    A race whose counties list different candidates is one race per county:
    Minnesota files every county's sheriff under one office ID.
    """
    races: dict[tuple[str, ...], list[Line]] = {}
    for line in lines:
        races.setdefault(line.race, []).append(line)
    contests = []
    for race in races.values():
        by_county: dict[str, list[Line]] = {}
        for x in race:
            by_county.setdefault(x.county, []).append(x)
        ballots = {frozenset(x.candidate for x in xs) for xs in by_county.values()}
        for group in [race] if len(ballots) == 1 else list(by_county.values()):
            candidates: dict[str, str] = {}
            for x in group:
                if not NOT_CANDIDATES.match(x.candidate):
                    candidates.setdefault(x.candidate, x.party)
            contests.append(
                Contest(
                    group[0].office,
                    frozenset(x.name for x in group if x.name),
                    tuple(candidates.items()),
                )
            )
    return contests, frozenset(x.name for x in lines if x.name)


def office_and_district(contest: str) -> tuple[str, str]:
    """ "State Senator - District 8" -> ("State Senator", "8"); ("<name>", "") if no district."""
    contest = " ".join(contest.split())
    for pattern in (DISTRICT_AFTER, DISTRICT_BEFORE):
        m = pattern.match(contest)
        if m and m["office"].strip(" ,-"):
            office = " ".join(f"{m['office'].strip(' ,-')}{m['rest']}".split())
            return office, m["district"].lstrip("0").upper() or "0"
    return contest, ""


def to_candidacies(
    contests: Iterable[Contest], every: frozenset[str], state: str, year: int
) -> list[Candidacy]:
    """Candidacies for a state's candidate contests (not federal ones, questions or retention
    votes). ``every`` is all the state's counties: a contest in each of them is statewide."""
    out = []
    for c in contests:
        if (
            not c.candidates
            or FEDERAL.search(c.name)
            or NOT_OFFICES.search(c.name.upper())
            or all(YES_NO.match(name) for name, _ in c.candidates)
        ):
            continue
        office, district = office_and_district(c.name)
        statewide = bool(every) and c.counties >= every
        local = not statewide and bool(LOCAL_OFFICES.search(office))
        for name, party in c.candidates:
            code = party.upper()
            if code in NO_PARTY or code == WRITE_IN_PARTY or code.isdigit():
                party = ""
            out.append(
                Candidacy(
                    state=state,
                    election_year=year,
                    election_date=election_day(year),
                    level=Level.LOCAL if local else Level.STATE,
                    office=office,
                    district=district,
                    jurisdiction=next(iter(c.counties)) if local and len(c.counties) == 1 else "",
                    counties=STATEWIDE if statewide else join_counties(c.counties),
                    candidate_name=name,
                    party=party,
                    election_type="general",
                    incumbent_status="",
                    candidate_status="write-in" if code == WRITE_IN_PARTY else "",
                    source="enr",
                    source_candidate_id="",
                )
            )
    return out


@dataclass(frozen=True)
class Line:
    """One candidate's votes in one county, from a state's own results file."""

    race: tuple[str, ...]
    """Identifies the contest within the file."""
    office: str
    county: str
    """The file's county code; a race on every county's ballot has all of them."""
    fips: str
    name: str
    """County name, upper case."""
    candidate: str
    party: str
    votes: int
    precincts: tuple[int, int]
    """(reported, total) for this race in this county."""


def from_lines(
    lines: list[Line], state: str, year: int
) -> tuple[list[CountyResult], dict[str, Any]]:
    """Keep the statewide candidate races, as county results plus reporting status."""
    counties = {line.county for line in lines}
    races: dict[tuple[str, ...], list[Line]] = {}
    for line in lines:
        races.setdefault(line.race, []).append(line)
    rows: list[CountyResult] = []
    precincts: dict[str, tuple[int, int]] = {}
    for race in races.values():
        office = race[0].office
        candidates = [x for x in race if not NOT_CANDIDATES.match(x.candidate)]
        ballots: dict[str, set[str]] = {}
        for x in race:
            ballots.setdefault(x.county, set()).add(x.candidate)
        if (
            set(ballots) != counties
            # Minnesota files every county's sheriff under one office ID.
            or len({frozenset(names) for names in ballots.values()}) > 1
            or NOT_OFFICES.search(office.upper())
            or not candidates
            or all(YES_NO.match(x.candidate) for x in candidates)
        ):
            continue
        for x in race:
            # Statewide races share their precincts; keep the fullest count.
            if x.fips and x.precincts[1] >= precincts.get(x.fips, (0, 0))[1]:
                precincts[x.fips] = x.precincts
        rows.extend(
            CountyResult(
                state=state,
                election_year=year,
                election_date=election_day(year),
                stage="general",
                office=office_of(office),
                county_fips=x.fips,
                county=x.name,
                candidate_name=x.candidate,
                party=x.party,
                votes=x.votes,
                source="enr",
            )
            for x in candidates
        )
    status: dict[str, Any] = {
        "updated": "",  # these files don't say when they were last updated
        "precincts_reported": sum(r for r, _ in precincts.values()),
        "precincts_total": sum(t for _, t in precincts.values()),
        "counties": {f: list(v) for f, v in sorted(precincts.items())},
    }
    return rows, status


def _int(raw: str) -> int:
    raw = raw.strip().replace(",", "")
    return int(raw) if raw.isdigit() else 0


def parse_florida(
    text: str, state: str, year: int, fips: dict[tuple[str, str], str]
) -> tuple[list[CountyResult], dict[str, Any]]:
    """Florida's election-night file: tab-separated, one row per candidate per county."""
    return from_lines(florida_lines(text, state, fips), state, year)


def florida_lines(text: str, state: str, fips: dict[tuple[str, str], str]) -> list[Line]:
    lines = []
    for r in csv.DictReader(io.StringIO(text), delimiter="\t"):
        county = " ".join((r.get("CountyName") or "").split())
        if not county:
            continue
        code = fips.get((state, county_key(county)), "")
        if not code:
            log.warning("enr: no county code for %s, %s", county, state)
        names = (r.get(k) or "" for k in ("CanNameFirst", "CanNameMiddle", "CanNameLast"))
        race = (r.get("RaceCode") or "", r.get("RaceName") or "")
        lines.append(
            Line(
                race=(
                    *race,
                    (r.get("Juris1num") or "").strip(),
                    (r.get("Juris2num") or "").strip(),
                ),
                office=" ".join(race[1].split()),
                county=r.get("CountyCode") or "",
                fips=code,
                name=county_name(county),
                # Tickets come as first "Donalds", middle "/", last "Avila".
                candidate=" ".join(" ".join(names).split()),
                party=(r.get("PartyCode") or "").strip(),
                votes=_int(r.get("CanVotes") or ""),
                precincts=(_int(r.get("PrecinctsReporting") or ""), _int(r.get("Precincts") or "")),
            )
        )
    return lines


# Minnesota party codes that would read wrongly elsewhere: "R" is the Republican
# Party and "NP" marks a nonpartisan office. (DFL, the Democratic-Farmer-Labor
# Party, counts as the Democrats on the map; see partyClass in web/map.js.)
MN_PARTIES = {"R": "REP", "NP": ""}


def parse_minnesota(
    text: str, state: str, year: int, counties: dict[str, tuple[str, str]]
) -> tuple[list[CountyResult], dict[str, Any]]:
    """Minnesota's media file: ";"-separated, one row per candidate per county.

    Fields: state; county ID; precinct; office ID; office; district; candidate
    ID; candidate; suffix; incumbent; party; precincts reporting; precincts;
    votes; percent; race total. County IDs number the counties alphabetically,
    as Census codes do with odd numbers (01 Aitkin = 27001 ... 87 = 27173).
    """
    return from_lines(minnesota_lines(text, state, counties), state, year)


def minnesota_lines(text: str, state: str, counties: dict[str, tuple[str, str]]) -> list[Line]:
    names = {code: name for code, (st, name) in counties.items() if st == state}
    lines = []
    for raw in text.splitlines():
        f = raw.split(";")
        if len(f) < 16 or not f[1].strip().isdigit():
            continue
        code = f"27{int(f[1]) * 2 - 1:03d}"
        party = f[10].strip()
        lines.append(
            Line(
                race=(f[3].strip(), f[5].strip()),
                office=" ".join(f[4].split()),
                county=f[1].strip(),
                fips=code if code in names else "",
                name=names.get(code, ""),
                candidate=" ".join(f"{f[7]} {f[8]}".split()),
                party=MN_PARTIES.get(party, party),
                votes=_int(f[13]),
                precincts=(_int(f[11]), _int(f[12])),
            )
        )
    return lines
