from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from who_is_running.cli import main, parse_years
from who_is_running.http import NotFound
from who_is_running.models import Candidacy, Level
from who_is_running.pipeline import run
from who_is_running.sources.base import Source
from who_is_running.text import decode


def _candidacy(name: str, state: str = "NC") -> Candidacy:
    return Candidacy(
        state=state,
        election_year=2026,
        election_date="2026-11-03",
        level=Level.LOCAL,
        office="MAYOR",
        district="",
        jurisdiction="WAKE",
        candidate_name=name,
        party="",
        election_type="general",
        incumbent_status="",
        candidate_status="",
        source="stub",
        source_candidate_id="",
    )


class StubSource(Source):
    name = "stub"
    description = "test double"

    def __init__(self, data: dict[int, list[Candidacy]]) -> None:
        self.data = data

    def available_years(self) -> list[int]:
        return sorted(self.data)

    def refresh_years(self, today: date) -> list[int]:
        return [today.year]

    def candidacies(self, year: int) -> list[Candidacy]:
        if year not in self.data:
            raise NotFound(str(year))
        return self.data[year]


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def test_run_writes_sorted_csv(tmp_path: Path) -> None:
    source = StubSource({2026: [_candidacy("Zed"), _candidacy("Amy", state="AK")]})

    assert run(source, [2026], tmp_path) == {2026: 2}

    out = tmp_path / "stub" / "2026.csv"
    rows = _read(out)
    assert [r["candidate_name"] for r in rows] == ["Amy", "Zed"]
    assert list(rows[0]) == Candidacy.columns()
    assert rows[0]["level"] == "local"
    assert b"\r\n" not in out.read_bytes()


def test_run_skips_missing_years_and_never_writes_empty_files(tmp_path: Path) -> None:
    existing = tmp_path / "stub" / "2024.csv"
    existing.parent.mkdir(parents=True)
    existing.write_text("keep me")
    source = StubSource({2024: [], 2026: [_candidacy("Amy")]})

    assert run(source, [2022, 2024, 2026], tmp_path) == {2026: 1}

    assert existing.read_text() == "keep me"
    assert not (tmp_path / "stub" / "2022.csv").exists()


def test_decode_handles_bom_and_stray_cp1252() -> None:
    assert decode(b"\xef\xbb\xbfa,b\n") == "a,b\n"
    assert decode("Peña\n".encode() + b"\xab quoted \xbb\n") == "Peña\n« quoted »\n"


def test_parse_years() -> None:
    assert parse_years("2026") == [2026]
    assert parse_years("2020-2022") == [2020, 2021, 2022]
    assert parse_years("2024,2020-2021") == [2020, 2021, 2024]


def test_cli_lists_sources(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sources"]) == 0
    out = capsys.readouterr().out
    assert "fec" in out
    assert "nc" in out
