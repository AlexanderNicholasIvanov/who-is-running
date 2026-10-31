"""Tests for the Virginia Department of Elections source.

Fixtures:
- va_list_page.html: trimmed from the 2026 general's all-offices list page (2026-10-31).
- va_candidates.xlsx: real rows of that page's workbook (rev. 10-9-2026), repacked as a
  minimal .xlsx with the campaign email, phone, address and website columns blanked.
"""

from __future__ import annotations

import pytest
from conftest import FakeWeb, fixture_bytes

from who_is_running.http import NotFound
from who_is_running.models import Candidacy, Level
from who_is_running.sources.va import SITE, VaSource, workbook_url

PAGE = f"{SITE}/november-3-2026-gen-elect-all-offices/"
WORKBOOK = (
    "https://www.elections.virginia.gov/media/castyourballot/candidatelist/2026/"
    "2026-November-All-Offices-Candidate-List-(rev-10-9-2026).xlsx"
)


def _web() -> FakeWeb:
    return FakeWeb(
        {
            WORKBOOK: fixture_bytes("va_candidates.xlsx"),
            PAGE: fixture_bytes("va_list_page.html"),
        }
    )


def _rows() -> list[Candidacy]:
    return VaSource(_web()).candidacies(2026)


def _named(name: str, rows: list[Candidacy] | None = None) -> list[Candidacy]:
    return [r for r in (rows or _rows()) if r.candidate_name == name]


def test_workbook_is_found_from_the_list_page() -> None:
    web = _web()
    VaSource(web).candidacies(2026)
    assert web.requested == [PAGE, WORKBOOK]


def test_page_without_a_workbook_is_not_found() -> None:
    with pytest.raises(NotFound):
        workbook_url("<html><a href='/x.pdf'>list</a></html>", PAGE)


def test_federal_races() -> None:
    (warner,) = _named("Mark R. Warner")
    assert warner == Candidacy(
        state="VA",
        election_year=2026,
        election_date="2026-11-03",
        level=Level.FEDERAL,
        office="US Senate",
        district="",
        jurisdiction="",
        counties="*",
        candidate_name="Mark R. Warner",
        party="DEM",
        election_type="general",
        incumbent_status="incumbent",
        candidate_status="",
        source="va",
        source_candidate_id="",
    )
    (mizusawa,) = _named("Bert Mizusawa")
    assert (mizusawa.party, mizusawa.incumbent_status) == ("REP", "challenger")

    (blais,) = _named("Dianne L. Blais")
    assert (blais.office, blais.district, blais.party) == ("US House", "11", "GRE")
    assert blais.counties == "FAIRFAX;FAIRFAX CITY"  # one race, listed in both localities


def test_general_assembly_special() -> None:
    (luna,) = _named("Sonia Ruth Vasquez Luna")
    assert luna.level is Level.STATE
    assert (luna.office, luna.district) == ("House of Delegates", "20")
    assert luna.counties == "MANASSAS CITY;MANASSAS PARK CITY;PRINCE WILLIAM"
    assert luna.incumbent_status == "open_seat"  # no incumbent on the ballot


def test_same_title_in_two_cities_is_two_races() -> None:
    rows = _rows()
    (green,) = _named("Michael L. Green", rows)
    (henley,) = _named("Barbara M. Henley", rows)
    for row, city in ((green, "COVINGTON CITY"), (henley, "VIRGINIA BEACH CITY")):
        assert (row.office, row.district) == ("Member City Council", "District 2")
        assert row.level is Level.LOCAL
        assert (row.jurisdiction, row.counties) == (city, city)
    assert green.incumbent_status == "open_seat"
    assert henley.incumbent_status == "incumbent"
    (bruzzesi,) = _named("Jeff L. Bruzzesi", rows)
    assert bruzzesi.incumbent_status == "challenger"


def test_races_across_locality_lines_are_joined() -> None:
    rows = _rows()
    (mayor,) = _named("David A. McCaleb", rows)
    assert (mayor.office, mayor.district) == ("Mayor - Belle Haven", "")
    assert (mayor.jurisdiction, mayor.counties) == ("", "ACCOMACK;NORTHAMPTON")

    (sheriff,) = _named("Gary C. Hash", rows)
    assert (sheriff.office, sheriff.district) == ("Sheriff", "")
    assert sheriff.counties == "GALAX CITY;GRAYSON"


def test_town_named_only_in_the_district_column_goes_in_the_office() -> None:
    (fox,) = _named("W. Christian Fox")
    assert (fox.office, fox.district) == ("Mayor - Farmville", "")
    assert fox.counties == "CUMBERLAND;PRINCE EDWARD"


def test_several_seats_leave_non_incumbents_unlabeled() -> None:
    council = [r for r in _rows() if r.office == "Member Town Council - Belle Haven"]
    assert len(council) == 5
    assert {r.incumbent_status for r in council} == {"incumbent"}
    assert {r.party for r in council} == {"IND"}


def test_contact_details_are_never_published() -> None:
    raw = "\n".join(",".join(r.as_row()) for r in _rows())
    assert "@" not in raw and "PO Box" not in raw
