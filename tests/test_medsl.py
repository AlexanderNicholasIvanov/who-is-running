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
    parse_with_results,
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
    assert source.available_years() == [2016, 2018, 2020, 2022, 2024]
    assert source.candidacies(2019) == []
    assert "2018-nc-precinct-general.zip" in _url(2018, "NC")
    assert "2022-nc-local-precinct-general.zip" in _url(2022, "NC")
    assert _url(2016, "NC") == (
        "https://raw.githubusercontent.com/MEDSL/official-precinct-returns/HEAD/"
        "source/2016-nc-precinct.zip"
    )
    # 2020 was deleted from GitHub after the move to Dataverse, so it's read at a fixed commit.
    assert _url(2020, "NC") == (
        "https://raw.githubusercontent.com/MEDSL/2020-elections-official/"
        "ad22f37e3f1e3355f078ba5a1efce6c221333ef1/individual_states/2020-nc-precinct-general.zip"
    )


def test_resorted_copy_of_a_file_is_skipped() -> None:
    # North Carolina 2020's zip holds the same rows twice, once re-sorted.
    body = fixture_bytes("medsl_2020_nc.csv")
    web = FakeWeb(
        {
            "2020-nc-": _zip(
                ("2020-nc-precinct-general.csv", body),
                ("2020-nc-precinct-general-sorted.csv", body),
            )
        }
    )
    source = MedslSource(web)

    assert [r.candidate_name for r in source.candidacies(2020)] == ["JOSEPH R BIDEN"]
    assert [(r.county, r.votes) for r in source.results(2020)] == [("BURKE", 205)]


def test_2016_schema() -> None:
    # 2016 uses older column names, spells a candidate as each county did
    # ("Jim GRAY" in Adair, "Jim Gray" in Breckinridge) and leaves Adair's party
    # blank, though it gives Rand Paul's own party (candidate_party).
    candidacies, results = parse_with_results(
        fixture_bytes("medsl_2016.csv").decode().splitlines(keepends=True)
    )

    by_name = {c.candidate_name: c for c in candidacies}
    assert set(by_name) == {"Jim GRAY", "Rand Paul", "Craig DEAN"}
    assert by_name["Jim GRAY"].party == "DEM"
    paul = by_name["Rand Paul"]  # the spelling with the most votes
    assert (paul.state, paul.office, paul.party, paul.counties) == ("KY", "US Senate", "REP", "*")
    assert paul.election_type == "general"
    local = by_name["Craig DEAN"]
    assert (local.level, local.jurisdiction, local.counties) == (
        Level.LOCAL,
        "Adair County",
        "Adair County",
    )
    assert sorted((r.county_fips, r.candidate_name, r.party, r.votes) for r in results) == [
        ("21001", "Jim GRAY", "DEM", 144),
        ("21001", "Rand Paul", "REP", 237),
        ("21027", "Jim GRAY", "DEM", 115),
        ("21027", "Rand Paul", "REP", 269),
    ]


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


def test_races_record_their_counties() -> None:
    rows = _by_name()
    # The fixture's precincts are in KENT, NEW CASTLE and SUSSEX.
    assert rows["SARAH MCBRIDE"].counties == "NEW CASTLE;SUSSEX"
    # Counties are per race, so every candidate in it gets the same list.
    assert rows["JOHN J WHALEN III"].counties == "NEW CASTLE;SUSSEX"
    assert rows["DANIEL B SHORT"].counties == "SUSSEX"
    assert rows["PAT ONE"].counties == "KENT"


def test_race_in_every_county_is_statewide() -> None:
    header = fixture_bytes("medsl_precincts.csv").decode().splitlines()[0]
    row = (
        "1,GOVERNOR,DEMOCRAT,DEMOCRAT,TOTAL,5,{county},1,{county},1,MATT MEYER,STATEWIDE,STATE,"
        "2024,GEN,DELAWARE,FALSE,FALSE,DE,10,51,11,2024-11-05,1"
    )
    lines = [header, *(row.format(county=c) for c in ("KENT", "NEW CASTLE", "SUSSEX"))]

    (governor,) = parse(lines)

    assert governor.counties == "*"


def test_new_england_town_races_keep_their_county() -> None:
    (row,) = _parse("medsl_town.csv")
    assert (row.jurisdiction, row.counties) == ("BURLINGTON", "*")  # the file's only county


def test_2016_county_codes() -> None:
    header = fixture_bytes("medsl_2016.csv").decode().splitlines()[0]
    clinton = '"CLINTON, HILLARY RODHAM / TIMOTHY MICHAEL KAINE",,,,,president'
    # Wisconsin appends a town code to the county's; Kansas City has a code but no name.
    wi = (
        "2016,gen,FALSE,Wisconsin,WI,55,25,Adams County,5500100000,01581060,43.973763,"
        "-89.767223,ADAMS,TOWN OF ADAMS Wards 1-3,Hillary Clinton,clinton,US President,"
        f"statewide,FALSE,democratic,total,207,,,,,,,,,,P00003392,{clinton}"
    )
    mo = [
        "2016,gen,FALSE,Missouri,MO,29,34,Jackson County,29095,00758502,39.005441,-94.343326,"
        "Jackson,ABSENTEE,Hillary Clinton,clinton,US President,statewide,FALSE,democratic,"
        f"total,6920,,,,,,,,,,P00003392,{clinton}",
        "2016,gen,FALSE,Missouri,MO,29,34,,29380,,,,Kansas City,INTRASTATE,Hillary Clinton,"
        f"clinton,US President,statewide,FALSE,democratic,total,14,,,,,,,,,,P00003392,{clinton}",
    ]

    (result,) = parse_with_results([header, wi])[1]
    assert (result.county_fips, result.county, result.votes) == ("55001", "Adams County", 207)
    results = parse_with_results([header, *mo])[1]
    assert sorted((r.county_fips, r.county, r.votes) for r in results) == [
        ("29095", "Jackson County", 6920),
        ("29380", "Kansas City", 14),
    ]


def test_2016_presidential_nominees_without_a_party() -> None:
    # Vermont 2016 gives no party on any presidential line.
    header = fixture_bytes("medsl_2016.csv").decode().splitlines()[0]
    row = (
        "2016,gen,FALSE,Vermont,VT,50,6,Addison County,50001,01461757,44.031248,-73.141581,"
        "Lincoln,1,{name},{surname},US President,statewide,FALSE,,total,{votes},,,,,,,,,,"
        '{fec},"{fec_name}",,,,,president'
    )
    candidacies = parse(
        [
            header,
            row.format(
                name="Hillary Clinton",
                surname="clinton",
                votes=494,
                fec="P00003392",
                fec_name="CLINTON, HILLARY RODHAM / TIMOTHY MICHAEL KAINE",
            ),
            row.format(
                name="Gary Johnson",
                surname="johnson",
                votes=22,
                fec="P60012234",
                fec_name="JOHNSON, JOHN FITZGERALD MR.",
            ),
        ]
    )
    assert {(c.candidate_name, c.party) for c in candidacies} == {
        ("Hillary Clinton", "DEM"),
        ("Gary Johnson", ""),  # not guessed: his ballot line varied by state
    }
