"""Build the static website: copy ``web/`` and convert ``data/`` CSVs to JSON.

Output layout (served as-is by GitHub Pages)::

    _site/index.html, app.js, style.css       copied from web/
    _site/data/manifest.json                  what exists, for the page's pickers
    _site/data/<source>/<year>/<state>.json   {"columns": [...], "rows": [[...], ...]}
    _site/data/<source>/<year>.csv            the original CSV, for download
    _site/results/<year>/<state>.json         county vote totals per statewide race
    _site/results/<year>/national.json        statewide totals for the national map
    _site/results/<year>.csv                  the original results CSV
    _site/results/<year>/rcv.json             ranked-choice rounds, every race that year
    _site/results/<year>/rcv.csv              the original rounds CSV

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
from who_is_running.results import CountyResult
from who_is_running.rounds import EXHAUSTED, RcvRound
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
        "rcv": build_rounds(data_dir / "rounds", out_dir / "results"),
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


def build_results(results_dir: Path, out_dir: Path) -> list[dict[str, Any]]:
    """County results per state, plus statewide totals for the national map."""
    built = []
    for csv_path in sorted(results_dir.glob("*/*.csv")):
        year = int(csv_path.stem)
        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            columns = next(reader)
            if columns != CountyResult.columns():
                raise ValueError(f"{csv_path}: unexpected columns {columns}")
            rows = [dict(zip(columns, row, strict=True)) for row in reader]

        # state -> office -> candidate (name, party) -> county fips -> votes
        races: defaultdict[str, defaultdict[str, defaultdict[tuple[str, str], Counter[str]]]]
        races = defaultdict(lambda: defaultdict(lambda: defaultdict(Counter)))
        county_names: dict[str, str] = {}
        for r in rows:
            fips, county = MAP_COUNTIES.get(r["county_fips"], (r["county_fips"], r["county"]))
            if fips[2:] == "000" and STATE_FIPS.get(fips[:2]) == r["state"]:
                fips = county = ""  # a state-level tally, e.g. Maine's overseas ballots
            races[r["state"]][r["office"]][r["candidate_name"], r["party"]][fips] += int(r["votes"])
            county_names[fips] = county

        dest = out_dir / str(year)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(csv_path, out_dir / csv_path.name)
        national: defaultdict[str, dict[str, Any]] = defaultdict(dict)
        for state, offices in sorted(races.items()):
            state_races: list[dict[str, Any]] = []
            for office, candidates in sorted(offices.items()):
                ranked = sorted(candidates.items(), key=lambda kv: -sum(kv[1].values()))
                counties = sorted({f for _, by_county in ranked for f in by_county})
                race = {
                    "office": office,
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
            for choice, patterns in NATIONAL_RACES.items():
                found = next(
                    (r for p in patterns for r in state_races if p.match(r["office"])), None
                )
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


# Name parts that one source writes and another drops: "Begich III, Nicholas J." vs "NICK BEGICH".
NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV"}


def build_rounds(rounds_dir: Path, out_dir: Path) -> list[dict[str, Any]]:
    """Ranked-choice rounds per year, as ``results/<year>/rcv.json``.

    Runs after ``build_results``: a statewide race's tabulation rarely gives
    parties, so a candidate's party is taken from the same race's first-choice
    results when exactly one candidate there has their surname.
    """
    built = []
    for csv_path in sorted(rounds_dir.glob("*/*.csv")):
        year = int(csv_path.stem)
        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.reader(fh)
            columns = next(reader)
            if columns != RcvRound.columns():
                raise ValueError(f"{csv_path}: unexpected columns {columns}")
            rows = [dict(zip(columns, row, strict=True)) for row in reader]

        # (state, office, district, type, date) -> candidate -> [party, {round: votes}]
        races: defaultdict[tuple[str, ...], dict[str, list[Any]]] = defaultdict(dict)
        for r in rows:
            key = (r["state"], r["office"], r["district"], r["election_type"], r["election_date"])
            entry = races[key].setdefault(r["candidate_name"], [r["party"], {}])
            entry[1][int(r["round"])] = int(r["votes"])

        dest = out_dir / str(year)
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(csv_path, dest / "rcv.csv")
        out = []
        for (state, office, district, kind, when), by_name in sorted(races.items()):
            count = max(n for _, votes in by_name.values() for n in votes)
            exhausted = by_name.pop(EXHAUSTED, ["", {}])[1]
            first = _first_choices(dest / f"{state}.json", office) if not district else []
            candidates = [
                [
                    name,
                    party or _party_of(name, first),
                    [votes.get(n, 0) for n in range(1, count + 1)],
                ]
                for name, (party, votes) in by_name.items()
            ]
            # The winner first: most votes in the last round, then in earlier ones.
            candidates.sort(key=lambda c: [-int(v) for v in reversed(c[2])])
            out.append(
                {
                    "state": state,
                    "office": office,
                    "district": district,
                    "election_type": kind,
                    "election_date": when,
                    "candidates": candidates,
                    "exhausted": [exhausted.get(n, 0) for n in range(1, count + 1)],
                }
            )
        (dest / "rcv.json").write_text(
            json.dumps({"races": out}, separators=(",", ":")), encoding="utf-8"
        )
        built.append({"year": year, "states": sorted({str(r["state"]) for r in out})})
    return built


def _first_choices(path: Path, office: str) -> list[list[Any]]:
    if not path.exists():
        return []
    races = json.loads(path.read_text(encoding="utf-8"))["races"]
    return next((r["candidates"] for r in races if r["office"] == office), [])


def _party_of(name: str, candidates: list[list[Any]]) -> str:
    """The party of the one first-choice candidate sharing ``name``'s surname ("Last, First")."""
    surname = set(re.findall(r"[A-Z]+", name.split(",")[0].upper())) - NAME_SUFFIXES
    if "," not in name or not surname:
        return ""
    matches = [c for c in candidates if surname <= set(re.findall(r"[A-Z]+", c[0].upper()))]
    return str(matches[0][1]) if len(matches) == 1 else ""
