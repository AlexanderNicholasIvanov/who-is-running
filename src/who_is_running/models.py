"""The normalized record every source produces."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import astuple, dataclass, fields
from enum import StrEnum

STATEWIDE = "*"
"""``counties`` value for a race on the ballot in every county of the state."""


class Level(StrEnum):
    FEDERAL = "federal"
    STATE = "state"
    LOCAL = "local"


@dataclass(frozen=True, order=True)
class Candidacy:
    """One person running for one office in one election.

    Field order is the sort order and the CSV column order. Empty string means
    "not provided by the source"; we never guess a value we were not given.
    """

    state: str
    """Two-letter postal code of the state the office belongs to ("US" for president)."""
    election_year: int
    election_date: str
    """ISO date (YYYY-MM-DD) when the source gives one."""
    level: Level
    office: str
    district: str
    jurisdiction: str
    """County, city or other sub-state area, when the race is confined to one."""
    counties: str
    """Counties the race is on the ballot in, ";"-separated; "*" for the whole state."""
    candidate_name: str
    party: str
    election_type: str
    """"primary", "general", or empty when the source doesn't say."""
    incumbent_status: str
    """"incumbent", "challenger", "open_seat", or empty."""
    candidate_status: str
    source: str
    source_candidate_id: str

    @classmethod
    def columns(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_row(self) -> list[str]:
        return [str(v) for v in astuple(self)]


def join_counties(counties: Iterable[str]) -> str:
    """The ``counties`` value for a set of county names (blanks dropped, sorted)."""
    return ";".join(sorted({c.strip() for c in counties if c.strip()}))
