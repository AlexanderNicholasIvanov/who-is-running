from __future__ import annotations

from conftest import FakeWeb, fixture_bytes

from who_is_running.models import Candidacy, Level
from who_is_running.sources.nc import NcSource, classify, district_of, parse


def _by_name() -> dict[str, Candidacy]:
    return {r.candidate_name: r for r in parse(fixture_bytes("nc_listing.csv").decode())}


def test_statewide_race_is_collapsed_across_counties() -> None:
    rows = parse(fixture_bytes("nc_listing.csv").decode())
    cooper = [r for r in rows if r.candidate_name == "Roy Cooper"]

    assert len(cooper) == 1
    assert cooper[0].jurisdiction == ""  # appears in more than one county
    assert cooper[0].level is Level.FEDERAL
    assert cooper[0].election_type == "general"
    assert cooper[0].election_date == "2026-11-03"


def test_single_county_race_records_the_county() -> None:
    hills = _by_name()["Ted Hills"]
    assert hills.jurisdiction == "WAKE"
    assert hills.level is Level.LOCAL
    assert hills.district == "05"


def test_multi_county_city_race_has_no_single_jurisdiction() -> None:
    doe = _by_name()["Jane Doe"]
    assert doe.jurisdiction == ""
    assert doe.level is Level.LOCAL
    assert doe.district == "E"


def test_primary_and_nonpartisan_election_types() -> None:
    rows = _by_name()
    assert rows["Asa Buck"].election_type == "primary"
    assert rows["Asa Buck"].party == "REP"
    # March, no party contest: the file doesn't say what kind of election it is.
    assert rows["John Roe"].election_type == ""


def test_old_style_dates_and_contest_names() -> None:
    judge = _by_name()["Old Judge"]
    assert judge.election_date == "2010-05-04"
    assert judge.election_year == 2010
    assert judge.level is Level.STATE


def test_classify() -> None:
    assert classify("US HOUSE DISTRICT 1") is Level.FEDERAL
    assert classify("PRESIDENTIAL PREFERENCE") is Level.FEDERAL
    assert classify("NC GOVERNOR") is Level.STATE
    assert classify("DISTRICT ATTORNEY DISTRICT 15A") is Level.STATE
    assert classify("COURT OF APPEALS JUDGE (WYNN)") is Level.STATE
    assert classify("DISTRICT COURT JUDGE DISTRICT 11 (RAWLS)") is Level.STATE
    assert classify("ALAMANCE COUNTY CLERK OF SUPERIOR COURT") is Level.LOCAL
    assert classify("TOWN OF ELKIN MAYOR") is Level.LOCAL


def test_district_of() -> None:
    assert district_of("NC DISTRICT COURT JUDGE DISTRICT 01 SEAT 01") == "01"
    assert district_of("DISTRICT ATTORNEY DISTRICT 15A") == "15A"
    assert district_of("HANDY SANITARY DISTRICT SUPERVISOR") == ""
    assert district_of("NC GOVERNOR") == ""


def test_candidacies_finds_the_csv_by_listing_the_folder() -> None:
    web = FakeWeb(
        {
            "list-type=2&prefix=Elections/2014/": fixture_bytes("nc_s3_listing.xml"),
            "Candidate_Listing_2014_rev1.csv": fixture_bytes("nc_listing.csv"),
        }
    )

    rows = NcSource(web).candidacies(2014)

    assert rows
    assert web.requested[-1] == (
        "https://s3.amazonaws.com/dl.ncsbe.gov/Elections/2014/Candidate%20Filing/"
        "Candidate_Listing_2014_rev1.csv"
    )


def test_missing_year_yields_nothing() -> None:
    empty = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"></ListBucketResult>'
    )
    assert NcSource(FakeWeb({"list-type=2": empty})).candidacies(2005) == []
