"""Pull a source's data and write it as normalized CSV."""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from who_is_running.http import NotFound
from who_is_running.models import Candidacy, Level
from who_is_running.results import CountyResult, results_path
from who_is_running.results import write_csv as write_results
from who_is_running.rounds import rounds_path
from who_is_running.rounds import write_csv as write_rounds
from who_is_running.sources import Source

log = logging.getLogger(__name__)

Row = TypeVar("Row", Candidacy, CountyResult)


def output_path(data_dir: Path, source: Source, year: int) -> Path:
    return data_dir / source.name / f"{year}.csv"


def write_csv(path: Path, rows: list[Candidacy]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(Candidacy.columns())
        writer.writerows(row.as_row() for row in sorted(rows))


def run(source: Source, years: list[int], data_dir: Path) -> dict[int, int]:
    """Write one CSV per year. Returns row counts for the years that had data.

    Years the source doesn't publish are skipped, and an existing file is
    never replaced with an empty one, so a transient upstream glitch can't
    wipe data. The exception is a source that ``expires``: an empty (but
    successful) result deletes its file. Rows of the states a source couldn't
    read (``Source.kept_states``) are carried over from the file being replaced.
    """
    written: dict[int, int] = {}
    for year in years:
        try:
            rows = source.candidacies(year)
        except NotFound as err:
            log.info("%s: no data for %d (%s)", source.name, year, err)
            continue
        rows = rows + _previous(
            output_path(data_dir, source, year), source.kept_states(year), _candidacy
        )
        rounds = source.rounds(year)
        if rounds:
            write_rounds(rounds_path(data_dir, source.name, year), rounds)
            log.info("%s: wrote %d ranked-choice rows for %d", source.name, len(rounds), year)
        if rows:
            write_csv(output_path(data_dir, source, year), rows)
            written[year] = len(rows)
            log.info("%s: wrote %d rows for %d", source.name, len(rows), year)
        else:
            # Sources of results or rounds only (enr, rcv) have no rows.
            log.info("%s: no rows for %d", source.name, year)
            path = output_path(data_dir, source, year)
            if source.expires and path.exists():
                path.unlink()
                log.info("%s: deleted %s, which the publisher no longer serves", source.name, path)
        results = source.results(year)
        path = results_path(data_dir, source.name, year)
        kept = _previous(path, source.kept_states(year), _result)
        if results or kept:
            status = {**source.results_status(year), **_previous_status(path, kept)}
            results = results + kept
            write_results(path, results)
            log.info("%s: wrote %d county results for %d", source.name, len(results), year)
            if status:
                path.with_suffix(".json").write_text(
                    json.dumps(status, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
    return written


def _previous(path: Path, states: set[str], parse: Callable[[dict[str, str]], Row]) -> list[Row]:
    """The rows for ``states`` in an existing file."""
    if not states or not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = [parse(row) for row in csv.DictReader(fh) if row["state"] in states]
    log.info("kept %d rows for %s from %s", len(rows), ", ".join(sorted(states)), path)
    return rows


def _previous_status(path: Path, kept: list[CountyResult]) -> dict[str, Any]:
    """The results status of the states whose results were kept."""
    status_path = path.with_suffix(".json")
    if not kept or not status_path.exists():
        return {}
    status: dict[str, Any] = json.loads(status_path.read_text(encoding="utf-8"))
    states = {r.state for r in kept}
    return {k: v for k, v in status.items() if k in states}


def _candidacy(row: dict[str, str]) -> Candidacy:
    values: dict[str, Any] = dict(row)
    values.update(election_year=int(row["election_year"]), level=Level(row["level"]))
    return Candidacy(**values)


def _result(row: dict[str, str]) -> CountyResult:
    values: dict[str, Any] = dict(row)
    values.update(election_year=int(row["election_year"]), votes=int(row["votes"]))
    return CountyResult(**values)
