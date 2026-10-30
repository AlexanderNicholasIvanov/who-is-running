"""Federal candidates from the FEC candidate master bulk files.

Every candidate for President, US Senate or US House who registers with the
FEC (files a Statement of Candidacy) appears here, so this is registrations,
not ballot qualification: many entries never make a ballot.

https://www.fec.gov/campaign-finance-data/candidate-master-file-description/
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable
from dataclasses import replace
from datetime import date

from who_is_running.districts import Districts
from who_is_running.models import STATEWIDE, Candidacy, Level
from who_is_running.sources.base import Source
from who_is_running.text import decode

FIRST_CYCLE = 2000
URL = "https://www.fec.gov/files/bulk-downloads/{cycle}/cn{yy:02d}.zip"

OFFICES = {"P": "US President", "S": "US Senate", "H": "US House"}
INCUMBENT = {"I": "incumbent", "C": "challenger", "O": "open_seat"}
STATUS = {
    "C": "statutory_candidate",
    "F": "statutory_candidate_future_election",
    "N": "not_yet_statutory_candidate",
    "P": "statutory_candidate_prior_cycle",
}


def current_cycle(today: date) -> int:
    return today.year + today.year % 2


class FecSource(Source):
    name = "fec"
    description = "FEC candidate master file: all registered federal candidates (2000-present)"

    def available_years(self) -> list[int]:
        return list(range(FIRST_CYCLE, current_cycle(date.today()) + 1, 2))

    def refresh_years(self, today: date) -> list[int]:
        cycle = current_cycle(today)
        return [cycle - 2, cycle]

    def candidacies(self, year: int) -> list[Candidacy]:
        if year % 2:
            return []
        archive = self.fetch(URL.format(cycle=year, yy=year % 100))
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            raw = zf.read("cn.txt")
        return place(parse(decode(raw), cycle=year), Districts(self.fetch))


def parse(text: str, *, cycle: int) -> list[Candidacy]:
    """Parse a pipe-delimited cn.txt.

    A cycle's file also lists candidates whose election year is earlier or
    later than the cycle (e.g. senators already registered for their next
    race). We keep only elections inside the two-year cycle, so each person
    lands in the file for the cycle they actually run in. Odd years are
    special elections.
    """
    out: set[Candidacy] = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        f = line.split("|")
        cand_id, name, party, year, office_state, office, district, ici, status = f[:9]
        if not year.isdigit() or int(year) not in (cycle - 1, cycle):
            continue
        out.add(
            Candidacy(
                state=office_state,
                election_year=int(year),
                election_date="",
                level=Level.FEDERAL,
                office=OFFICES.get(office, office),
                district=district if office == "H" else "",
                jurisdiction="",
                # Senate, president and at-large House seats are statewide by
                # definition. The FEC doesn't say which counties a district covers;
                # place() fills that in from the Census district lines.
                counties=STATEWIDE if office != "H" or district in ("00", "") else "",
                candidate_name=name.strip(),
                party=party,
                election_type="",
                incumbent_status=INCUMBENT.get(ici, ""),
                candidate_status=STATUS.get(status, status),
                source="fec",
                source_candidate_id=cand_id,
            )
        )
    return sorted(out)


def place(rows: Iterable[Candidacy], districts: Districts) -> list[Candidacy]:
    """Fill in the counties of House districts from the Census district lines."""
    return [
        replace(r, counties=districts.house(r.election_year).counties(r.state, r.district))
        if r.office == "US House" and not r.counties
        else r
        for r in rows
    ]
