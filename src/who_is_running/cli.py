"""Command-line entry point: ``who-is-running fetch``, ``who-is-running sources``."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from who_is_running.build_site import build
from who_is_running.pipeline import run
from who_is_running.sources import SOURCES, get_source


def parse_years(spec: str) -> list[int]:
    """'2026' -> [2026]; '2000-2004' -> [2000, 2001, 2002, 2003, 2004]; '2020,2024' too."""
    years: list[int] = []
    for part in spec.split(","):
        start, _, end = part.strip().partition("-")
        years.extend(range(int(start), int(end or start) + 1))
    return sorted(set(years))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="who-is-running", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sources", help="list available data sources")

    fetch = sub.add_parser("fetch", help="download and normalize candidate data")
    fetch.add_argument(
        "--source",
        action="append",
        choices=sorted(SOURCES),
        help="source to pull (repeatable; default: all)",
    )
    when = fetch.add_mutually_exclusive_group(required=True)
    when.add_argument("--years", type=parse_years, help="e.g. 2026, 2000-2026 or 2022,2024")
    when.add_argument("--refresh", action="store_true", help="re-pull each source's recent years")
    when.add_argument("--all-years", action="store_true", help="backfill everything available")
    fetch.add_argument("--data-dir", type=Path, default=Path("data"))

    site = sub.add_parser("build-site", help="build the static website from data/")
    site.add_argument("--data-dir", type=Path, default=Path("data"))
    site.add_argument("--web-dir", type=Path, default=Path("web"))
    site.add_argument("--out", type=Path, default=Path("_site"))

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "sources":
        for name, cls in sorted(SOURCES.items()):
            print(f"{name:8} {cls.description}")
        return 0

    if args.command == "build-site":
        manifest = build(args.data_dir, args.web_dir, args.out)
        rows = sum(f["rows"] for f in manifest["files"])
        logging.info("built %s: %d files, %d rows", args.out, len(manifest["files"]), rows)
        return 0

    failed = False
    for name in args.source or sorted(SOURCES):
        source = get_source(name)
        if args.refresh:
            years = source.refresh_years(date.today())
        elif args.all_years:
            years = source.available_years()
        else:
            years = args.years
        try:
            run(source, years, args.data_dir)
        except Exception:
            # One broken source shouldn't stop the others from updating.
            logging.exception("%s failed", name)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
