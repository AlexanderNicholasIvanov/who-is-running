from __future__ import annotations

from conftest import FakeWeb, fixture_bytes

from who_is_running.districts import (
    CD116,
    CD119,
    COUNTY_NAMES_2010,
    SLDL_2024,
    Districts,
    county_name,
    district_key,
)


def _districts() -> tuple[Districts, FakeWeb]:
    web = FakeWeb(
        {
            CD119: fixture_bytes("census_cd119.txt"),
            CD116: fixture_bytes("census_cd_coded.txt"),
            COUNTY_NAMES_2010: fixture_bytes("census_county_names.txt"),
            SLDL_2024: fixture_bytes("census_sldl.txt"),
        }
    )
    return Districts(web), web


def test_2020_census_lines() -> None:
    house = _districts()[0].house(2024)
    assert house.counties("MD", "01") == "BALTIMORE;CAROLINE;CECIL"
    assert house.counties("MD", "7") == "BALTIMORE;BALTIMORE CITY"
    assert house.counties("MD", "05") == "ST. MARY'S"
    assert house.counties("MD", "09") == ""  # no such district
    assert house.counties("NY", "01") == ""  # not in the file


def test_one_seat_states_are_statewide() -> None:
    house = _districts()[0].house(2024)
    assert house.counties("DE", "00") == "*"
    assert house.counties("DE", "01") == "*"


def test_water_only_overlaps_are_ignored() -> None:
    house = _districts()[0].house(2024)
    # Florida 12 touches Marion County only in water; Cook County's row is unassigned water.
    assert house.counties("FL", "12") == ""
    assert house.counties("IL", "") == ""


def test_coded_lines_get_names_from_the_county_list() -> None:
    house = _districts()[0].house(2018)
    assert house.counties("MD", "02") == "BALTIMORE;BALTIMORE CITY"
    assert house.counties("MD", "05") == "ST. MARY'S"
    # 24999 isn't a county on the list, so district 8 has none.
    assert house.counties("MD", "08") == ""


def test_district_covering_every_county_is_statewide() -> None:
    lower = _districts()[0].state_house()
    assert lower.counties("MD", "1A") == "ALLEGANY;GARRETT"
    assert lower.counties("MD", "3") == "FREDERICK"
    assert lower.counties("DC", "") == ""  # county area outside every district


def test_years_without_lines_or_with_new_maps_stay_unplaced() -> None:
    districts, web = _districts()
    assert districts.house(2002).counties("MD", "01") == ""
    assert web.requested == []
    assert districts.house(2026).counties("MD", "01") == "BALTIMORE;CAROLINE;CECIL"
    assert districts.house(2024).counties("TX", "01") == "BOWIE;CAMP"
    assert districts.house(2026).counties("TX", "01") == ""  # redrawn for 2026
    assert districts.house(2020).counties("MD", "02") == "BALTIMORE;BALTIMORE CITY"
    assert districts.house(2025).counties("MD", "01") == "BALTIMORE;CAROLINE;CECIL"


def test_files_are_fetched_once() -> None:
    districts, web = _districts()
    for year in (2024, 2025, 2024):
        districts.house(year)
    assert web.requested == [CD119]


def test_missing_file_means_unknown() -> None:
    districts = Districts(FakeWeb({}))
    assert districts.house(2024).counties("MD", "01") == ""


def test_helpers() -> None:
    assert district_key("03") == district_key("3") == district_key("003") == "3"
    assert district_key("011B") == "11B"
    assert county_name("Acadia Parish") == "ACADIA"
    assert county_name("Baltimore city") == "BALTIMORE CITY"
    assert county_name("Doña Ana County") == "DOÑA ANA"
