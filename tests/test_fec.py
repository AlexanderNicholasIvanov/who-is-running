from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from datetime import date

from conftest import FakeWeb, fixture_bytes

from who_is_running.districts import CD119, Districts
from who_is_running.models import Level
from who_is_running.sources.fec import FecSource, current_cycle, parse, place


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
    assert house.counties == ""  # the FEC doesn't say which counties a district covers; see place()

    senate = rows["S6NC00001"]
    assert senate.office == "US Senate"
    assert senate.district == ""  # FEC uses "00" for statewide offices
    assert senate.counties == "*"

    assert rows["H6TX18001"].election_year == 2025


# Real 2024 and 2026 cn.txt rows (addresses blanked). DC and the territories elect
# one non-voting member, filed under district "00", "01" or nothing.
DELEGATE_ROWS = """\
H4PR01010|HERNANDEZ RIVERA, PABLO JOSE|DEM|2024|PR|H|00|C|C|C00831487|||||
H4MP01022|KING-HINDS, KIMBERLYN KAY|REP|2026|MP|H|01|I|C|C00873554|||||
H6GU00059|TORRES, MARY CAMACHO|REP|2026|GU|H|||C|C00928119|||||
H0DC00058|NORTON, ELEANOR HOLMES|DEM|2026|DC|H|00|I|C|C00244335|||||
"""


def test_delegates_and_resident_commissioner_are_named_and_at_large() -> None:
    rows = parse(DELEGATE_ROWS, cycle=2024) + parse(DELEGATE_ROWS, cycle=2026)

    got = {r.state: (r.office, r.district, r.counties) for r in rows}
    assert got == {
        "PR": ("US House Resident Commissioner", "00", "*"),
        "MP": ("US House Delegate", "00", "*"),
        "GU": ("US House Delegate", "00", "*"),
        "DC": ("US House Delegate", "00", "*"),
    }
    # Already statewide, so the Census lines (which have no territories) aren't consulted.
    assert place(rows, Districts(FakeWeb({}))) == rows


def test_parse_dedupes_repeated_lines() -> None:
    rows = parse(fixture_bytes("cn.txt").decode(), cycle=2026)
    assert len(rows) == len(set(rows))


def test_candidacies_downloads_the_cycle_zip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("cn.txt", fixture_bytes("cn.txt"))
    web = FakeWeb({"/2026/cn26.zip": buf.getvalue(), CD119: fixture_bytes("census_cd119.txt")})

    rows = FecSource(web).candidacies(2026)

    assert web.requested == ["https://www.fec.gov/files/bulk-downloads/2026/cn26.zip", CD119]
    assert len(rows) == 3


def test_house_districts_get_their_counties_from_census_lines() -> None:
    web = FakeWeb({CD119: fixture_bytes("census_cd119.txt")})
    house = parse(fixture_bytes("cn.txt").decode(), cycle=2026)[0]
    rows = [
        replace(house, state="MD", election_year=2024),
        replace(house, state="TX", election_year=2026),  # new map for 2026
        replace(house, office="US Senate", counties="*"),
    ]

    placed = place(rows, Districts(web))

    assert [r.counties for r in placed] == ["BALTIMORE;CAROLINE;CECIL", "", "*"]


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
