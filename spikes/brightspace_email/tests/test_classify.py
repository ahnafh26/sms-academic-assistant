from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mailspike.classify import classify, extract_dates, extract_grade
from mailspike.parse import parse_eml

FIXTURES = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("America/Toronto")
HOSTS = {"learn.example.ca": "waterloo", "mylearningspace.example.ca": "laurier"}


def _event(name):
    return classify(parse_eml((FIXTURES / name).read_bytes()), HOSTS, TZ)


def test_grade_released():
    ev = _event("synthetic_grade.eml")
    assert (ev.kind, ev.institution, ev.course_code, ev.org_unit_id) == ("grade", "waterloo", "CS135", "411111")
    assert ev.title == "Assignment 4"
    assert ev.grade == {"score": 42.0, "out_of": 50.0, "percent": 84.0}


def test_announcement_with_extension_is_flagged():
    ev = _event("synthetic_announcement.eml")
    assert (ev.kind, ev.institution, ev.course_code, ev.org_unit_id) == ("announcement", "laurier", "BU111", "658593")
    assert ev.title == "Assignment 3 extension"
    assert ev.mentions_deadline_change
    assert ev.deadline_change_to == "2026-10-18T23:59:00-04:00"


def test_due_soon():
    ev = _event("synthetic_due_soon.eml")
    assert (ev.kind, ev.title, ev.course_code) == ("due_soon", "Quiz 5", "EC120")
    assert ev.due_at == "2026-10-13T10:00:00-04:00"


def test_feedback_with_percent():
    ev = _event("synthetic_feedback.eml")
    assert (ev.kind, ev.title, ev.course_code) == ("feedback", "Lab Report 2", "MATH135")
    assert ev.grade == {"score": None, "out_of": None, "percent": 88.0}


def test_non_brightspace_email_ignored():
    assert _event("synthetic_not_brightspace.eml") is None


@pytest.mark.parametrize("text,expected", [
    ("due Oct 13, 2026 10:00 AM", ["2026-10-13T10:00:00-04:00"]),
    ("by November 2nd, 2026 at 11:59 pm", ["2026-11-02T23:59:00-05:00"]),   # after DST ends
    ("on December 4, 2026", ["2026-12-04"]),
    ("2026-10-20 17:00", ["2026-10-20T17:00:00-04:00"]),
    ("no dates here 10/13", []),
])
def test_extract_dates(text, expected):
    assert extract_dates(text, TZ) == expected


def test_grade_needs_context():
    assert extract_grade("Posted 10/13 at noon") is None
    assert extract_grade("Your score: 17.5/20")["percent"] == 87.5
