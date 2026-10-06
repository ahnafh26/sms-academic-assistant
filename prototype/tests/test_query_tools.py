import pytest

from chat.tools import Toolbox, course_matches
from conftest import NOW, TZ


@pytest.fixture
def tb(store):
    return Toolbox(store, TZ, now_fn=lambda: NOW)


def _titles(result):
    return [f"{i['course']} {i['title']}" for i in result["items"]]


def test_due_today(tb):
    r = tb.call("list_deadlines", {"range": "today"})
    assert r["period"] == "today (Mon Oct 5)"
    assert _titles(r) == ["BU111 Quiz 5", "CS135 Assignment 4", "EC120 Ch. 5 EOC Problems: Elasticity"]
    first = r["items"][0]
    assert first["due"] == "Mon Oct 5, 4:00 PM" and first["when"] == "in 7 hours" and first["status"] == "upcoming"


def test_tomorrow_and_this_week(tb):
    assert _titles(tb.call("list_deadlines", {"range": "tomorrow"})) == ["MA129 WebWork 3"]
    assert tb.call("list_deadlines", {"range": "this_week"})["count"] == 4


def test_exams_include_midterms_and_use_starts(tb):
    r = tb.call("list_deadlines", {"range": "this_month", "types": ["exam"]})
    assert _titles(r) == ["EC120 Midterm"]
    assert r["items"][0]["starts"] == "Thu Oct 15, 7:00 PM"


@pytest.mark.parametrize("course,expected", [("econ", 2), ("BU111", 1), ("cs", 1), ("business", 1), ("micro", 2)])
def test_course_filter_aliases(tb, course, expected):
    assert tb.call("list_deadlines", {"range": "next_14_days", "course": course})["count"] == expected


def test_quiet_course_explained(tb):
    r = tb.call("list_deadlines", {"range": "this_week", "course": "MATH137"})
    assert "no deadlines published" in r["error"]


def test_unknown_course_lists_known(tb):
    r = tb.call("list_deadlines", {"range": "this_week", "course": "PHIL"})
    assert "Known courses" in r["error"] and "EC120" in r["error"]


def test_institution_filter(tb):
    assert _titles(tb.call("list_deadlines", {"range": "this_week", "institution": "waterloo"})) == ["CS135 Assignment 4"]


def test_next_deadlines(tb):
    r = tb.call("next_deadlines", {"count": 2})
    assert _titles(r) == ["BU111 Quiz 5", "CS135 Assignment 4"]
    nm = tb.call("next_deadlines", {"count": 1, "types": ["midterm"]})
    assert _titles(nm) == ["EC120 Midterm"]


def test_find_items_and_stable_refs(tb):
    r = tb.call("find_items", {"query": "cs a4"})
    assert r["candidates"][0]["title"] == "Assignment 4"
    ref = r["candidates"][0]["ref"]
    again = tb.call("list_deadlines", {"range": "today"})
    assert next(i["ref"] for i in again["items"] if i["title"] == "Assignment 4") == ref


def test_get_courses(tb):
    r = tb.call("get_courses", {})
    assert {"institution": "laurier", "course": "EC120", "name": "Intro To Microeconomics", "upcoming": 2} in r["courses"]
    assert r["courses_without_calendar_deadlines"][0]["course"] == "MATH137"


def test_workload(tb):
    r = tb.call("get_workload", {"range": "this_week"})
    assert r["busiest_day"] == "Mon Oct 5" and r["total"] == 4


def test_bad_args_and_unknown_tool(tb):
    assert "error" in tb.call("list_deadlines", {"range": "custom", "start_date": "friday"})
    assert "error" in tb.call("nope", {})
    assert "bad arguments" in tb.call("list_deadlines", {"range": "today", "colour": "red"})["error"]


def test_course_matches():
    assert course_matches("EC120", "Intro To Microeconomics", "microeconomics")
    assert not course_matches("EC120", None, "bu")
