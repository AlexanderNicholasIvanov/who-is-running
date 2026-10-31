"""Unofficial, election-night county results from state results sites.

Many states publish live results through Clarity (Scytl) Election Night
Reporting. Every Clarity site has the same layout::

    <site>/elections.json                      the site's elections: EID, name, date
    <site>/<EID>/current_ver.txt               the latest published version
    <site>/<EID>/<version>/reports/detailxml.zip
        detail.xml: every contest, each candidate's votes by county and vote type,
        and precincts reported per county

The results are unofficial and change all night (and for weeks of canvassing),
so the map labels them as such, and certified MEDSL results replace them once
they exist (see ``build_site.build_results``). This source only has results;
candidate lists come from the other sources.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from xml.etree import ElementTree

from who_is_running.districts import COUNTY_NAMES_2010, county_name, parse_county_names
from who_is_running.http import NotFound
from who_is_running.models import Candidacy
from who_is_running.results import CountyResult
from who_is_running.sources.base import Source
from who_is_running.sources.medsl import NOT_OFFICES
from who_is_running.text import decode

log = logging.getLogger(__name__)

CLARITY = "https://results.enr.clarityelections.com"
GZIP_MAGIC = bytes([0x1F, 0x8B])


@dataclass(frozen=True)
class Feed:
    """One state's Clarity results site."""

    site: str
    """The state's site root, e.g. ``https://results.enr.clarityelections.com/IA``."""
    election_id: str = ""
    """The general election's EID. Blank: look it up in the site's elections.json by date."""


ELECTION_DAYS = {2026: date(2026, 11, 3)}
LIVE_WINDOW = timedelta(days=4)
"""Election day to Nov 6: .github/workflows/live-results.yml's schedule."""

FEEDS: dict[int, dict[str, Feed]] = {
    2026: {
        # Statewide Clarity sites. A blank ID is looked up in elections.json on
        # each run, so a state that posts its election late is picked up then.
        # South Carolina's elections.json is empty, so its ID has to be filled
        # in here once scvotes.gov links the general election's results page.
        "CO": Feed(f"{CLARITY}/CO"),
        "IA": Feed(f"{CLARITY}/IA", "127077"),
        "SC": Feed("https://www.enr-scvotes.org/SC"),
    },
}

# Choices that aren't candidates.
NOT_CANDIDATES = re.compile(
    r"^(write[- ]?ins?|over ?votes?|under ?votes?|blank|scattering|total|times (blank|over) voted)"
    r"( votes?)?$",
    re.I,
)
# Ballot questions, and retention votes ("Supreme Court Justice - Boatright"):
# Colorado doesn't mark either as a question, but their choices are Yes/No.
YES_NO = re.compile(r"^(yes|no|for|against)\b", re.I)

# Clarity's contest names for the offices the national map shows, in the
# spelling MEDSL's results use (see build_site.NATIONAL_RACES).
OFFICES = [
    (re.compile(r"^(President(ial Electors)?)\b", re.I), "US President"),
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

TIME_ZONES = {
    "EST": -5, "EDT": -4, "CST": -6, "CDT": -5, "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
    "AKST": -9, "AKDT": -8, "HST": -10,
}  # fmt: skip


class EnrSource(Source):
    name = "enr"
    description = (
        "Unofficial election-night county results from state results sites (Clarity ENR), "
        "statewide races only"
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._status: dict[int, dict[str, Any]] = {}
        self._fips: dict[tuple[str, str], str] | None = None

    def available_years(self) -> list[int]:
        return sorted(FEEDS)

    def refresh_years(self, today: date) -> list[int]:
        # Counts keep changing through the canvass, so the daily refresh picks
        # them up for the rest of the year. Until then the live-results workflow
        # pulls them, and the two mustn't commit the same files at once.
        day = ELECTION_DAYS.get(today.year)
        return [today.year] if day and today >= day + LIVE_WINDOW else []

    def candidacies(self, year: int) -> list[Candidacy]:
        return []

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
            xml = decode(zf.read("detail.xml"))
        rows, status = parse_detail(xml, state, year, self._county_fips())
        if not any(r.votes for r in rows):
            log.info("enr: %s has no votes counted yet", state)
            return None
        status["url"] = f"{feed.site}/{eid}/"
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

    def _county_fips(self) -> dict[tuple[str, str], str]:
        if self._fips is None:
            names = parse_county_names(decode(self.fetch(COUNTY_NAMES_2010)))
            self._fips = {(st, county_key(name)): fips for fips, (st, name) in names.items()}
        return self._fips


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
    for pattern, office in OFFICES:
        if pattern.search(contest):
            return office
    return " ".join(contest.upper().split())


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


def parse_detail(
    xml: str, state: str, year: int, fips: dict[tuple[str, str], str]
) -> tuple[list[CountyResult], dict[str, Any]]:
    """A Clarity detail.xml as county results for its statewide races, plus reporting status."""
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("enr: refusing an XML file with a DTD")
    root = ElementTree.fromstring(xml)

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
