from __future__ import annotations

import io
import zipfile
from datetime import date

from conftest import FakeWeb, fixture_bytes

from who_is_running.models import Level
from who_is_running.sources.fec import FecSource, current_cycle, parse


def test_parse_keeps_only_elections_in_the_cycle() -> None:
    rows = parse(fixture_bytes("cn.txt").decode(), cycle=2026)

    ids = [r.source_candidate_id for r in rows]
    # 2024 (prior cycle) and 2028 (future) are dropped; the 2025 special stays.
    assert sorted(ids) == ["H0AL01055", "H6TX18001", "S6NC00001"]


def test_parse_normalizes_fields() -> None:
    rows = {r.source_candidate_id: r for r in parse(fixture_bytes("cn.txt").decode(), cycle=2026)}

    house = rows["H0AL01055"]
    assert house.office == "US House"
    assert house.district == "01"
    assert house.state == "AL"
    assert house.level is Level.FEDERAL
    assert house.incumbent_status == "open_seat"
    assert house.candidate_status == "statutory_candidate"
    assert house.candidate_name == "CARL, JERRY LEE, JR"

    senate = rows["S6NC00001"]
    assert senate.office == "US Senate"
    assert senate.district == ""  # FEC uses "00" for statewide offices

    assert rows["H6TX18001"].election_year == 2025


def test_parse_dedupes_repeated_lines() -> None:
    rows = parse(fixture_bytes("cn.txt").decode(), cycle=2026)
    assert len(rows) == len(set(rows))


def test_candidacies_downloads_the_cycle_zip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cn.txt", fixture_bytes("cn.txt"))
    web = FakeWeb({"/2026/cn26.zip": buf.getvalue()})

    rows = FecSource(web).candidacies(2026)

    assert web.requested == ["https://www.fec.gov/files/bulk-downloads/2026/cn26.zip"]
    assert len(rows) == 3


def test_odd_years_are_not_cycles() -> None:
    web = FakeWeb({})
    assert FecSource(web).candidacies(2025) == []
    assert web.requested == []


def test_cycles() -> None:
    assert current_cycle(date(2026, 10, 30)) == 2026
    assert current_cycle(date(2027, 1, 2)) == 2028
    assert FecSource(FakeWeb({})).refresh_years(date(2027, 1, 2)) == [2026, 2028]
    years = FecSource(FakeWeb({})).available_years()
    assert years[0] == 2000
    assert all(y % 2 == 0 for y in years)
