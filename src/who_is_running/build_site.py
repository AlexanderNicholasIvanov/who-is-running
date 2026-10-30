"""Build the static website: copy ``web/`` and convert ``data/`` CSVs to JSON.

Output layout (served as-is by GitHub Pages)::

    _site/index.html, app.js, style.css       copied from web/
    _site/data/manifest.json                  what exists, for the page's pickers
    _site/data/<source>/<year>/<state>.json   {"columns": [...], "rows": [[...], ...]}
    _site/data/<source>/<year>.csv            the original CSV, for download

JSON is split by state so viewing one state never downloads a nationwide file.
"""

from __future__ import annotations

import csv
import json
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from who_is_running.models import Candidacy
from who_is_running.sources import SOURCES


def build(data_dir: Path, web_dir: Path, out_dir: Path) -> dict[str, Any]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(web_dir, out_dir)

    files = []
    for csv_path in sorted(data_dir.glob("*/*.csv")):
        source, year = csv_path.parent.name, int(csv_path.stem)
        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            columns = next(reader)
            rows = list(reader)
        if columns != Candidacy.columns():
            raise ValueError(f"{csv_path}: unexpected columns {columns}")

        state_col, year_col = columns.index("state"), columns.index("election_year")
        coverage: defaultdict[str, Counter[str]] = defaultdict(Counter)
        by_state: defaultdict[str, list[list[str]]] = defaultdict(list)
        for row in rows:
            coverage[row[state_col]][row[year_col]] += 1
            by_state[row[state_col]].append(row)

        dest = out_dir / "data" / source
        (dest / str(year)).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(csv_path, dest / csv_path.name)
        for state, state_rows in by_state.items():
            (dest / str(year) / f"{state}.json").write_text(
                json.dumps({"columns": columns, "rows": state_rows}, separators=(",", ":")),
                encoding="utf-8",
            )
        files.append(
            {
                "source": source,
                "year": year,
                "rows": len(rows),
                # state -> election year -> row count, so the page only fetches
                # files that actually contain the state and year being viewed.
                "coverage": {s: dict(sorted(c.items())) for s, c in sorted(coverage.items())},
            }
        )

    manifest = {
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ"),
        "sources": {
            name: {"description": cls.description}
            for name, cls in sorted(SOURCES.items())
            if any(f["source"] == name for f in files)
        },
        "files": files,
    }
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    (out_dir / "data" / "manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
    return manifest
