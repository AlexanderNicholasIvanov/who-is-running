"""Ranked-choice tabulations: each candidate's votes in each counting round."""

from __future__ import annotations

import csv
from dataclasses import astuple, dataclass, fields
from pathlib import Path

EXHAUSTED = "(exhausted)"
"""``candidate_name`` of the line counting ballots that no longer count for anyone."""


@dataclass(frozen=True, order=True)
class RcvRound:
    """One candidate's votes in one round of a ranked-choice count.

    A race's rounds also carry an ``EXHAUSTED`` line per round: ballots that
    counted for a candidate in round 1 but rank no candidate still in the count
    (exhausted, overvoted or skipped rankings). Field order is the sort order
    and the CSV column order.
    """

    state: str
    election_year: int
    election_date: str
    election_type: str
    office: str
    district: str
    round: int
    candidate_name: str
    party: str
    votes: int
    source: str

    @classmethod
    def columns(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def as_row(self) -> list[str]:
        return [str(v) for v in astuple(self)]


def rounds_path(data_dir: Path, source: str, year: int) -> Path:
    return data_dir / "rounds" / source / f"{year}.csv"


def write_csv(path: Path, rows: list[RcvRound]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(RcvRound.columns())
        writer.writerows(row.as_row() for row in sorted(rows))
