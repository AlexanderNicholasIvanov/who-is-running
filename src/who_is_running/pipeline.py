"""Pull a source's data and write it as normalized CSV."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

from who_is_running.http import NotFound
from who_is_running.models import Candidacy
from who_is_running.results import results_path
from who_is_running.results import write_csv as write_results
from who_is_running.rounds import rounds_path
from who_is_running.rounds import write_csv as write_rounds
from who_is_running.sources import Source

log = logging.getLogger(__name__)


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
    wipe data.
    """
    written: dict[int, int] = {}
    for year in years:
        try:
            rows = source.candidacies(year)
        except NotFound:
            log.info("%s: no data for %d", source.name, year)
            continue
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
        results = source.results(year)
        if results:
            path = results_path(data_dir, source.name, year)
            write_results(path, results)
            log.info("%s: wrote %d county results for %d", source.name, len(results), year)
            status = source.results_status(year)
            if status:
                path.with_suffix(".json").write_text(
                    json.dumps(status, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
    return written
