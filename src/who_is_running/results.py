"""Vote totals by county for statewide races, for the results map."""

from __future__ import annotations

import csv
from dataclasses import astuple, dataclass, fields
from pathlib import Path

STAGES = ("general", "runoff", "primary", "primary runoff")
"""Election stages, in the order the map offers them. A primary (and its runoff) is
one party's contest: the race is the office plus the candidates' ``party``."""


@dataclass(frozen=True, order=True)
class CountyResult:
    """One candidate's votes in one county in one statewide race.

    Field order is the sort order and the CSV column order.
    """

    state: str
    election_year: int
    election_date: str
    """ISO date, when the source gives one."""
    stage: str
    """One of ``STAGES``. A race's general election and its runoff are separate contests."""
    office: str
    county_fips: str
    """Five-digit Census code, which is how the map finds the county's shape."""
    county: str
    candidate_name: str
    party: str
    votes: int
    source: str

    @classmethod
    def columns(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_row(self) -> list[str]:
        return [str(v) for v in astuple(self)]


def results_path(data_dir: Path, source: str, year: int) -> Path:
    return data_dir / "results" / source / f"{year}.csv"


def write_csv(path: Path, rows: list[CountyResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(CountyResult.columns())
        writer.writerows(row.as_row() for row in sorted(rows))
