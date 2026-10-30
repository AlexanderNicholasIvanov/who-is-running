from __future__ import annotations

import io
import zipfile

from conftest import FakeWeb, fixture_bytes

from who_is_running.models import Candidacy, Level
from who_is_running.sources.medsl import (
    NOT_OFFICES,
    STATES,
    MedslSource,
    district_of,
    iso_date,
    parse,
    party_of,
)


def _parse(name: str) -> list[Candidacy]:
    return sorted(parse(fixture_bytes(name).decode().splitlines(keepends=True)))


def _by_name(name: str = "medsl_precincts.csv") -> dict[str, Candidacy]:
    return {r.candidate_name: r for r in _parse(name)}


def _zip(*members: tuple[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in members:
            zf.writestr(name, body)
    return buf.getvalue()


def test_precinct_rows_collapse_to_one_candidacy_per_race() -> None:
    rows = _parse("medsl_precincts.csv")
    assert [r.candidate_name for r in rows].count("SARAH MCBRIDE") == 1
    # "039" and "39" are the same district.
    assert [r.candidate_name for r in rows].count("DANIEL B SHORT") == 1


def test_non_candidates_are_dropped() -> None:
    names = set(_by_name())
    assert "WRITEIN" not in names  # write-in line
    assert "REPUBLICAN" not in names  # straight-party ticket
    assert "YES" not in names  # ballot question
    assert "" not in names  # turnout line
    assert "TURNOUT" not in names
    assert "PETER SWANN - OVERVOTES" not in names  # per-candidate overvote line
    assert "BONDS APPROVED" not in names  # filed under a ballot question
    assert "JOHN PELANDER - TIMES BLANK VOTED" not in names
    assert "DEM" not in names  # straight-ticket line named after the party


def test_html_entities_and_no_party() -> None:
    oneil = _by_name()["MARY O'NEIL"]
    assert oneil.party == ""
    assert oneil.jurisdiction == "SUSSEX"


def test_fusion_party_lines_merge_into_one_candidacy() -> None:
    rows = [r for r in _parse("medsl_precincts.csv") if r.candidate_name == "ANN FUSION"]
    assert [r.party for r in rows] == ["DEM/WORKING FAMILIES"]


def test_federal_offices_and_districts() -> None:
    rows = _by_name()
    house = rows["SARAH MCBRIDE"]
    assert (house.level, house.office, house.district) == (Level.FEDERAL, "US House", "AT-LARGE")
    assert house.jurisdiction == ""  # only local races carry a county
    assert (house.election_year, house.election_date) == (2024, "2024-11-05")
    assert (house.election_type, house.party, house.source) == ("general", "DEM", "medsl")

    senate = rows['MICHAEL "DR MIKE" KATZ']
    assert senate.office == "US Senate (special)"
    assert senate.district == ""
    assert senate.party == "IND"

    assert rows["DANIEL B SHORT"].level is Level.STATE
    assert rows["DANIEL B SHORT"].district == "39"


def test_local_races_are_per_county() -> None:
    rows = _by_name()
    # Two counties' "SHERIFF" races share a name; the county tells them apart.
    assert (rows["PAT ONE"].jurisdiction, rows["PAT TWO"].jurisdiction) == ("KENT", "SUSSEX")
    assert rows["PAT ONE"].party == ""  # nonpartisan
    assert rows["PAT TWO"].party == "CONSTITUTION"
    assert rows["JOANNE MASTEN"].office == "KENT COUNTY LEVY COURT"
    assert rows["JOANNE MASTEN"].district == "01"


def test_blank_dataverse_is_classified_from_the_office() -> None:
    rows = _by_name()
    assert rows["HEGAR"].level is Level.STATE
    assert rows["JONES"].level is Level.LOCAL
    assert rows["JONES"].jurisdiction == "SUSSEX"


def test_new_england_local_races_use_the_town() -> None:
    (row,) = _parse("medsl_town.csv")
    assert row.jurisdiction == "BURLINGTON"
    assert row.district == ""  # "000"
    assert row.election_year == 2018


def test_helpers() -> None:
    assert district_of("001") == "01"
    assert district_of("115") == "115"
    assert district_of("STATEWIDE") == ""
    assert district_of("1A") == "1A"
    assert party_of("OTHER", "GREEN") == "GRE"
    assert party_of("", '""') == ""
    assert party_of("OTHER", "LIBERTARIAN PARTY") == "LIB"
    assert party_of("OTHER", "CONSTITUTION PARTY") == "CONSTITUTION"
    assert district_of("AT LARGE") == "AT-LARGE"
    assert iso_date("11/8/22") == "2022-11-08"
    assert iso_date("2024-11-05") == "2024-11-05"


def test_candidacies_fetches_every_state_and_skips_missing_files() -> None:
    web = FakeWeb(
        {
            "/de24.zip": _zip(
                ("de24.csv", fixture_bytes("medsl_precincts.csv")),
                ("__MACOSX/._de24.csv", b"\x00junk"),
            ),
            "/vt24.zip": _zip(("vt24.csv", b"\xef\xbb\xbf" + fixture_bytes("medsl_town.csv"))),
        }
    )

    rows = MedslSource(web).candidacies(2024)

    assert len(web.requested) == len(STATES)
    assert web.requested[0] == (
        "https://raw.githubusercontent.com/MEDSL/2024-elections-official/HEAD/"
        "individual_states/ak24.zip"
    )
    assert {r.state for r in rows} == {"DE", "VT"}
    assert len(rows) == len(_parse("medsl_precincts.csv")) + 1


def test_years() -> None:
    source = MedslSource(FakeWeb({}))
    assert source.available_years() == [2018, 2022, 2024]
    assert source.candidacies(2020) == []
    assert "2018-nc-precinct-general.zip" in _url(2018, "NC")
    assert "2022-nc-local-precinct-general.zip" in _url(2022, "NC")


def _url(year: int, state: str) -> str:
    web = FakeWeb({})
    MedslSource(web).candidacies(year)
    return next(u for u in web.requested if f"-{state.lower()}-" in u)


def test_ballot_question_offices() -> None:
    for question in (
        "CITY OF MENAN OVERRIDE LEVY",
        "KUNA RURAL FIRE DISTRICT BOND",
        "LOCAL ISSUE NO 1",
    ):
        assert NOT_OFFICES.search(question), question
    for office in (
        "KENT COUNTY LEVY COURT",
        "BOND COUNTY SHERIFF",
        "LEVY COUNTY COMMISSIONER",
        "ADVISORY NEIGHBORHOOD COMMISSION",
        "FRANKLIN TOWNSHIP ADVISORY BOARD",
    ):
        assert not NOT_OFFICES.search(office), office
