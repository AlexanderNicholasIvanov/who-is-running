"""Build the static website: copy ``web/`` and convert ``data/`` CSVs to JSON.

Output layout (served as-is by GitHub Pages)::

    _site/index.html, app.js, style.css       copied from web/
    _site/data/manifest.json                  what exists, for the page's pickers
    _site/data/<source>/<year>/<state>.json   {"columns": [...], "rows": [[...], ...]}
    _site/data/<source>/<year>.csv            the original CSV, for download
    _site/results/<year>/<state>.json         county vote totals per statewide race
    _site/results/<year>/national.json        statewide totals for the national map
    _site/results/<year>.csv                  the original results CSV

JSON is split by state so viewing one state never downloads a nationwide file.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from who_is_running.districts import STATE_FIPS
from who_is_running.models import Candidacy
from who_is_running.results import STAGES, CountyResult
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
        "results": build_results(data_dir / "results", out_dir / "results"),
    }
    (out_dir / "data").mkdir(parents=True, exist_ok=True)
    (out_dir / "data" / "manifest.json").write_text(
        json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
    )
    return manifest


NATIONAL_RACES = {
    # Map choice -> the matching office in each state, regular election first.
    "US President": [re.compile(r"^US President$")],
    "US Senate": [re.compile(r"^US Senate$"), re.compile(r"^US Senate \(special\)$")],
    # "GOVERNOR", "GOVERNOR/LIEUTENANT GOVERNOR", "Governor / Lt. Governor" (Wisconsin 2018)
    "Governor": [re.compile(r"^GOVERNOR( ?(/|AND) ?(LIEUTENANT|LT\.?) GOVERNOR)?$", re.I)],
}


# Results county code -> (map county code, name), where the results and the
# map's boundaries use different Census codes for the same ground.
MAP_COUNTIES = {
    "02063": ("02261", "VALDEZ-CORDOVA"),  # Chugach: split from Valdez-Cordova in 2019,
    "02066": ("02261", "VALDEZ-CORDOVA"),  # with Copper River; the map predates that
    "46113": ("46102", "OGLALA LAKOTA"),  # Shannon County's code until its 2015 rename
}


# Results files written before the stage columns existed hold general elections.
LEGACY_COLUMNS = [c for c in CountyResult.columns() if c not in ("election_date", "stage")]

# A race: (stage, office, primary party, date); stage first so general races list first.
Race = tuple[int, str, str, str]


def build_results(results_dir: Path, out_dir: Path) -> list[dict[str, Any]]:
    """County results per state, plus statewide totals for the national map."""
    built = []
    for csv_path in sorted(results_dir.glob("*/*.csv")):
        year = int(csv_path.stem)
        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            columns = next(reader)
            if columns == LEGACY_COLUMNS:
                legacy = {"election_date": "", "stage": "general"}
                rows = [legacy | dict(zip(columns, row, strict=True)) for row in reader]
            elif columns == CountyResult.columns():
                rows = [dict(zip(columns, row, strict=True)) for row in reader]
            else:
                raise ValueError(f"{csv_path}: unexpected columns {columns}")

        # state -> race -> candidate (name, party) -> county fips -> votes
        races: defaultdict[str, defaultdict[Race, defaultdict[tuple[str, str], Counter[str]]]]
        races = defaultdict(lambda: defaultdict(lambda: defaultdict(Counter)))
        county_names: dict[str, str] = {}
        for r in rows:
            fips, county = MAP_COUNTIES.get(r["county_fips"], (r["county_fips"], r["county"]))
            if fips[2:] == "000" and STATE_FIPS.get(fips[:2]) == r["state"]:
                fips = county = ""  # a state-level tally, e.g. Maine's overseas ballots
            # Each party's primary is its own race; a general election's runoff is one race.
            party = r["party"] if r["stage"].startswith("primary") else ""
            race_key = (STAGES.index(r["stage"]), r["office"], party, r["election_date"])
            races[r["state"]][race_key][r["candidate_name"], r["party"]][fips] += int(r["votes"])
            county_names[fips] = county

        dest = out_dir / str(year)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(csv_path, out_dir / csv_path.name)
        national: defaultdict[str, dict[str, Any]] = defaultdict(dict)
        for state, offices in sorted(races.items()):
            state_races: list[dict[str, Any]] = []
            for (stage, office, party, when), candidates in sorted(offices.items()):
                ranked = sorted(candidates.items(), key=lambda kv: -sum(kv[1].values()))
                counties = sorted({f for _, by_county in ranked for f in by_county})
                race = {
                    "office": office,
                    "stage": STAGES[stage],
                    "date": when,
                    "party": party,  # the primary's party
                    "candidates": [[n, p, sum(v.values())] for (n, p), v in ranked],
                    # fips -> [county name, votes per candidate, in the order above];
                    # "" holds votes the state didn't report by county.
                    "counties": {
                        f: [county_names[f], [by_county[f] for _, by_county in ranked]]
                        for f in counties
                    },
                }
                state_races.append(race)
            (dest / f"{state}.json").write_text(
                json.dumps({"races": state_races}, separators=(",", ":")), encoding="utf-8"
            )
            general = [r for r in state_races if r["stage"] == "general"]
            for choice, patterns in NATIONAL_RACES.items():
                found = next((r for p in patterns for r in general if p.match(r["office"])), None)
                if found:
                    national[choice][state] = {
                        "office": found["office"],
                        "candidates": found["candidates"],
                    }
        (dest / "national.json").write_text(
            json.dumps({"races": national}, separators=(",", ":")), encoding="utf-8"
        )
        built.append(
            {
                "source": csv_path.parent.name,
                "year": year,
                "states": sorted(races),
                "national": [c for c in NATIONAL_RACES if c in national],
            }
        )
    return built
