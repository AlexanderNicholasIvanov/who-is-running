"""The interface every data source implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from who_is_running.http import Fetcher, http_get
from who_is_running.models import Candidacy
from who_is_running.results import CountyResult


class Source(ABC):
    """A publisher of candidate data.

    A source groups its data into "years" (a calendar year, or a two-year
    cycle for the FEC). Each year becomes one output file:
    ``data/<name>/<year>.csv``.
    """

    name: str
    """Short identifier, used as the output directory name."""
    description: str

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
