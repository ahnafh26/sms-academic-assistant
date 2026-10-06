"""Shared fixtures: a small, hand-built items file in the spike's output format."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from chat.data import Store

TZ = ZoneInfo("America/Toronto")
# Monday Oct 5 2026, 9:00 AM Toronto
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)


def raw_item(id_, course, title, type_, due=None, start=None, **extra):
    base = {
        "institution": extra.pop("institution", "laurier"),
        "course_code": course,
        "course_name": extra.pop("course_name", None),
        "course_label": None,
        "title": title,
        "type": type_,
        "due_at": due,
        "start_at": start,
        "all_day": False,
        "cancelled": False,
        "external_source_id": id_,
        "org_kind": "course",
        "category": "exam" if type_ in ("exam", "midterm") else "deliverable",
        "in_term": None,
        "source_tool": None,
    }
    base.update(extra)
    return base


ITEMS = [
    raw_item("bu-quiz5", "BU111", "Quiz 5", "quiz", due="2026-10-05T20:00:00Z",
             course_name="Understanding the Business Environment"),       # today 4 PM
    raw_item("cs-a4", "CS135", "Assignment 4", "assignment", due="2026-10-06T02:00:00Z",
             institution="waterloo"),                                    # today 10 PM
    raw_item("ec-hw", "EC120", "Ch. 5 EOC Problems: Elasticity", "assignment", due="2026-10-06T03:59:00Z",
             course_name="Intro To Microeconomics"),                     # today 11:59 PM
    raw_item("ma-ww", "MA129", "WebWork 3", "assignment", due="2026-10-07T03:59:00Z"),     # tomorrow 11:59 PM
    raw_item("ec-mid", "EC120", "Midterm", "midterm", start="2026-10-15T23:00:00Z",
             course_name="Intro To Microeconomics"),                     # Thu Oct 15 7 PM
    raw_item("bu-case", "BU111", "Live Case Individual Report", "assignment", due="2026-11-10T18:00:00Z"),
    raw_item("old-done", "EC120", "Ch. 1 Decision Point", "assignment", due="2026-09-29T14:00:00Z"),  # past
    # filtered out: noise / stale / cancelled / content
    raw_item("maint", None, "Monthly Maintenance Window", "other", due="2026-10-06T05:00:00Z",
             org_kind="institution", category="non_course"),
    raw_item("stale", "BU111", "Midterm", "midterm", start="2024-10-04T22:30:00Z", in_term=False),
    raw_item("cancel", "CS135", "Discussion 3", "discussion", due="2026-10-08T03:59:00Z", cancelled=True),
    raw_item("rubric", "BU111", "Presentation rubric", "other", due="2026-10-31T17:00:00Z", category="content"),
]


@pytest.fixture
def items_file(tmp_path):
    path = tmp_path / "all_items.json"
    path.write_text(json.dumps(ITEMS), encoding="utf-8")
    return path


@pytest.fixture
def store(items_file, tmp_path):
    s = Store(items_file, tmp_path / "state.json")
    s.load()
    return s
