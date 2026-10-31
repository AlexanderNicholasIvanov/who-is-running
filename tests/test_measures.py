from __future__ import annotations

import pytest

from who_is_running.measures import choice_of, outcome


@pytest.mark.parametrize(
    ("candidate", "choice"),
    [
        # How the MEDSL 2018-2024 files word the two answers.
        ("YES", "Yes"),
        ("NO", "No"),
        ("FOR", "Yes"),  # Arkansas, Colorado, West Virginia
        ("AGAINST", "No"),
        ("APPROVE", "Yes"),  # Rhode Island
        ("REJECT", "No"),
        ("DISAPPROVE", "No"),  # Idaho's 2022 advisory question
        ("YES FOR APPROVAL", "Yes"),  # Florida
        ("NO FOR REJECTION", "No"),
        ("FOR THE PROPOSAL - YES", "Yes"),  # Oklahoma
        ("AGAINST THE PROPOSAL - NO", "No"),
        ("AGAINST THE LEVY", "No"),  # West Virginia
        ("OVERVOTES", ""),
        ("MAINTAINED", ""),  # Washington's advisory votes aren't Yes/No
        ("AGAINST STUART R BERGER", ""),  # a Maryland judge's continuance line
    ],
)
def test_choices(candidate: str, choice: str) -> None:
    assert choice_of(candidate, "STATE ISSUE 1") == choice


def test_judicial_retention_is_not_a_measure() -> None:
    assert choice_of("YES", "SUPREME COURT - RETENTION - RENATHA FRANCIS") == ""
    assert choice_of("YES", "RETENTION OF CHARLES T. CANADY") == ""
    # Wyoming 2022: a measure about judges is still a measure.
    office = "CONSTITUTIONAL AMENDMENT B SUPREME COURT JUSTICES AND DISTRICT JUDGES - RETIREMENT"
    assert choice_of("FOR", office) == "Yes"


def test_outcome_by_state_rule() -> None:
    # Florida 2024 Amendment 4: 57.2% Yes, short of the 60% it needed.
    assert outcome("FL", 6070758, 4548379) == ("failed", "60%")
    assert outcome("FL", 60, 40) == ("passed", "60%")
    # Missouri 2024 Amendment 2 passed with 50.05%.
    assert outcome("MO", 1478652, 1475691) == ("passed", "a majority")
    assert outcome("MO", 5, 5) == ("failed", "a majority")
    # Wyoming 2022 Amendment A had more Yes than No votes and still failed: it
    # needed a majority of everyone who voted in the election.
    assert outcome("WY", 103389, 78714) == ("", "")
    assert outcome("CA", 0, 0) == ("", "")
