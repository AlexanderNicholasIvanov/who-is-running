"""Statewide ballot measures: how sources word Yes and No, and what it takes to pass.

A measure is kept as results, not candidacies: its county votes are
``CountyResult`` rows whose candidate is "Yes" or "No" and whose party is
empty, so a race is a measure exactly when its choices are Yes and No.
"""

from __future__ import annotations

import re

YES, NO = "Yes", "No"

# Every way the MEDSL 2018-2024 files word the two choices.
CHOICES = {
    **dict.fromkeys(("YES", "FOR", "APPROVE", "IN FAVOR OF"), YES),
    **dict.fromkeys(("YES FOR APPROVAL", "YES TO APPROVE"), YES),
    # Not "REJECTED": Michigan uses it for rejected write-ins.
    **dict.fromkeys(("NO", "AGAINST", "REJECT", "DISAPPROVE"), NO),
    **dict.fromkeys(("NO FOR REJECTION", "NO TO REJECT"), NO),
}
# "FOR THE PROPOSAL - YES" (Oklahoma), "AGAINST THE LEVY" (West Virginia).
SPELLED_OUT = re.compile(r"^(FOR|AGAINST) THE [A-Z ]+?(?: - (YES|NO))?$")
# Judges up for retention are asked Yes/No too, but they aren't measures. A
# measure *about* courts says what kind of measure it is.
RETENTION = re.compile(r"\b(RETENTION|RETAIN|RETAINED)\b|\b(JUSTICE|JUDGE|COURT)\b")
MEASURE_WORDS = re.compile(
    r"\b(AMENDMENT|PROPOSITION|PROPOSAL|QUESTION|ISSUE|MEASURE|INITIATIVE|REFERENDUM)\b"
)

# The share of Yes votes (of Yes + No) a statewide measure needs, for states
# where one rule covers every statewide measure in the years we have. States
# not listed have rules this data can't settle, so the site shows the split
# only: Wyoming, Minnesota, Hawaii and Tennessee count against ballots cast in
# the whole election; Colorado, Illinois and New Hampshire need supermajorities
# for some measures; Nevada's initiative amendments must pass twice; Nebraska,
# Mississippi, Massachusetts and New Mexico add turnout or county conditions;
# Oregon and Washington have measure-specific exceptions.
MAJORITY = "majority"
PASS_RULES: dict[str, str | float] = {
    **dict.fromkeys(
        (
            *("AK", "AL", "AR", "AZ", "CA", "CT", "DC", "GA", "IA", "ID", "KS", "KY", "LA"),
            *("MD", "ME", "MI", "MO", "MT", "NC", "ND", "NJ", "NY", "OH", "OK", "RI", "SC"),
            *("SD", "TX", "UT", "VA", "VT", "WV"),
        ),
        MAJORITY,
    ),
    # Constitutional amendments need 60% (Art. XI, s. 5), and every Florida
    # statewide measure is one. Amendments imposing a new state tax need two
    # thirds (s. 7); none has been on a November ballot in the years covered.
    "FL": 0.6,
}


def choice_of(candidate: str, office: str) -> str:
    """ "Yes" or "No" when a row is an answer to a ballot measure, else ""."""
    name = candidate.upper()
    choice = CHOICES.get(name, "")
    if not choice and (m := SPELLED_OUT.match(name)):
        choice = YES if (m.group(2) or m.group(1)) in ("YES", "FOR") else NO
    if choice and RETENTION.search(office.upper()) and not MEASURE_WORDS.search(office.upper()):
        return ""
    return choice


def outcome(state: str, yes: int, no: int) -> tuple[str, str]:
    """("passed" or "failed", what it needed), or ("", "") when the rule isn't known."""
    rule = PASS_RULES.get(state)
    if rule is None or not yes + no:
        return "", ""
    if rule == MAJORITY:
        return ("passed" if yes > no else "failed"), "a majority"
    assert isinstance(rule, float)
    return ("passed" if yes >= rule * (yes + no) else "failed"), f"{rule:.0%}"
