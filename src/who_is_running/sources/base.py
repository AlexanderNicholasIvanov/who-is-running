"""The interface every data source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

from who_is_running.http import Fetcher, http_get
from who_is_running.models import Candidacy
from who_is_running.results import CountyResult
from who_is_running.rounds import RcvRound


class Source(ABC):
    """A publisher of candidate data.

    A source groups its data into "years" (a calendar year, or a two-year
    cycle for the FEC). Each year becomes one output file:
    ``data/<name>/<year>.csv``.
    """

    name: str
    """Short identifier, used as the output directory name."""
    description: str
    expires = False
    """True when the data mustn't outlive the publisher's feed (Google's Civic Information
    terms): an empty result then deletes the year's file instead of keeping it."""

    def __init__(self, fetch: Fetcher = http_get) -> None:
        self.fetch = fetch

    @abstractmethod
    def available_years(self) -> list[int]:
        """Every year this source publishes, oldest first."""

    @abstractmethod
    def refresh_years(self, today: date) -> list[int]:
        """The years the daily refresh re-pulls: the current one plus the one
        before it, so late updates around a year boundary aren't missed."""

    @abstractmethod
    def candidacies(self, year: int) -> list[Candidacy]:
        """Download and normalize one year's data."""

    def results(self, year: int) -> list[CountyResult]:
        """County vote totals for the year's statewide races, if the source has votes.

        Called after ``candidacies`` for the same year.
        """
        return []

    def rounds(self, year: int) -> list[RcvRound]:
        """Round-by-round tallies of the year's ranked-choice races, if the source has them."""
        return []

    def results_status(self, year: int) -> dict[str, Any]:
        """For live results: how far the count has got, per state. Called after ``results``.

        Written beside the results as ``data/results/<source>/<year>.json``.
        """
        return {}

    def kept_states(self, year: int) -> set[str]:
        """States this run couldn't read, though they may have data. Called after
        ``candidacies`` and again after ``results``.

        The pipeline keeps their rows (and results status) from the files it's
        replacing, so one state's outage doesn't drop it from the site.
        """
        return set()
