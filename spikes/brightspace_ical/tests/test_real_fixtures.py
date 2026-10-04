"""Regression tests against anonymized real feeds (first fetch, 2026-10-04).

Course codes are pseudonymized (e.g. Waterloo MATH 135 -> XCIP 667) but
structure, titles, suffixes and times are real. Expected numbers were
verified by hand against the un-anonymized feeds; see findings/FINDINGS.md.
"""

from collections import Counter
from pathlib import Path

import pytest

from spike.config import load_institutions
from spike.normalize import normalize_events
from spike.parse import parse_ics

FIXTURES = Path(__file__).parent / "fixtures"
TERM_START = "2026-08-15"


def _items(inst: str):
    events = parse_ics((FIXTURES / f"real_{inst}_anon.ics").read_bytes()).events
    return events, normalize_events(events, load_institutions()[inst])


def _actionable(items):
    """What the product would surface as this term's deadlines."""
    return [
        i for i in items
        if i.category in ("deliverable", "exam") and i.in_term is not False
        and (i.due_at or i.start_at or "") >= TERM_START
    ]


@pytest.fixture(scope="module")
def waterloo():
    return _items("waterloo")


@pytest.fixture(scope="module")
def laurier():
    return _items("laurier")


def test_waterloo_shape(waterloo):
    events, items = waterloo
    assert len(events) == 124 and len(items) == 124  # LEARN emits only "- Due" markers here: no merges
    assert Counter(i.org_kind for i in items) == {"course": 80, "community": 43, "institution": 1}
    assert len({i.course_code for i in items if i.course_code}) == 4


def test_waterloo_only_one_course_has_current_deadlines(waterloo):
    _, items = waterloo
    actionable = _actionable(items)
    assert len(actionable) == 10
    assert {i.course_code for i in actionable} == {"XCIP667"}  # MATH 135; CS 115 / MATH 137 / MTHEL 99 have none


def test_waterloo_stale_copied_dates_excluded(waterloo):
    _, items = waterloo
    (midterm,) = [i for i in items if i.type == "midterm"]
    assert midterm.title == "Write Midterm" and midterm.in_term is False  # dated Oct 2025 in a Fall 2026 course
    stale = [i for i in items if "outside_course_term" in i.warnings]
    assert len(stale) == 69


def test_laurier_shape(laurier):
    events, items = laurier
    assert len(events) == 153 and len(items) == 151  # two Available/Availability Ends pairs merged
    assert Counter(i.org_kind for i in items) == {"institution": 77, "course": 67, "community": 7}
    assert all(i.category == "non_course" for i in items if i.org_kind != "course")


def test_laurier_actionable_deadlines(laurier):
    _, items = laurier
    actionable = _actionable(items)
    assert len(actionable) == 52
    assert Counter(i.course_code for i in actionable) == {"AO746": 40, "LW305": 12}  # EC120, BU111
    assert Counter(i.type for i in actionable)["assignment"] == 43
    assert sum(i.type == "other" for i in actionable) == 1  # finals conflict form


def test_laurier_stale_midterm_excluded(laurier):
    _, items = laurier
    (midterm,) = [i for i in items if i.type == "midterm"]
    assert midterm.in_term is False and "date_far_from_course_median" in midterm.warnings


def test_ids_unique_in_both_feeds(waterloo, laurier):
    for _, items in (waterloo, laurier):
        assert len({i.external_source_id for i in items}) == len(items)
        assert all(i.lms_event_id for i in items)
