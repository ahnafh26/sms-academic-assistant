from pathlib import Path

import pytest

from spike.config import InstitutionConfig
from spike.normalize import classify_type, normalize_events, strip_role
from spike.parse import parse_ics
from spike.rules import build_rules

FIXTURES = Path(__file__).parent / "fixtures"
INST = InstitutionConfig(
    slug="waterloo", name="UW", lms_name="LEARN", hosts=("learn.example.ca",), timezone="America/Toronto"
)


@pytest.fixture(scope="module")
def items():
    events = parse_ics((FIXTURES / "synthetic_before.ics").read_bytes()).events
    return normalize_events(events, INST)


def _find(items, course, title):
    matches = [i for i in items if i.course_code == course and i.title == title]
    assert matches, f"{course} {title} not found in {[(i.course_code, i.title) for i in items]}"
    return matches


def test_due_and_start_events_merge_into_one_item(items):
    (a4,) = _find(items, "CS135", "Assignment 4")
    assert a4.type == "assignment"
    assert a4.due_at == "2026-10-08T02:00:00Z"
    assert a4.due_at_local == "2026-10-07T22:00:00-04:00"
    assert a4.available_at == "2026-09-30T13:00:00Z"
    assert len(a4.raw_event_uids) == 2
    assert a4.course_name == "Designing Functional Programs"
    assert a4.course_detection == "location"


def test_due_plus_availability_end(items):
    (quiz,) = _find(items, "BU111", "Quiz 5")
    assert quiz.type == "quiz"
    assert quiz.due_at == "2026-10-07T20:00:00Z"
    assert quiz.available_until == "2026-10-07T21:00:00Z"
    assert "due_from_availability_end" not in quiz.warnings


def test_exam_with_duration_is_scheduled_block(items):
    (mid,) = _find(items, "EC120", "Midterm Exam")
    assert mid.type == "midterm"
    assert mid.start_at == "2026-10-15T23:00:00Z"
    assert mid.end_at == "2026-10-16T01:00:00Z"
    assert mid.due_at is None


def test_all_day_final(items):
    (final,) = _find(items, "EC120", "Final Exam")
    assert final.type == "exam" and final.all_day


def test_same_title_weekly_items_stay_separate_across_dst(items):
    reflections = _find(items, "MA129", "Weekly Reflection")
    assert [r.due_at for r in reflections] == ["2026-11-01T03:59:00Z", "2026-11-03T04:59:00Z"]
    assert len({r.external_source_id for r in reflections}) == 2
    assert len({r.fingerprint for r in reflections}) == 2


def test_course_missing_falls_back_to_org_unit(items):
    lab = next(i for i in items if i.title == "Lab Report 2")
    assert lab.course_code is None
    assert lab.org_unit_id == "422222"
    assert "no_course_code" in lab.warnings
    assert lab.type == "lab"


def test_cancelled_flag(items):
    (disc,) = _find(items, "CS135", "Discussion Post 3")
    assert disc.cancelled and disc.type == "discussion"


def test_every_item_has_stable_ids(items):
    assert len(items) == 8
    assert len({i.external_source_id for i in items}) == len(items)
    assert all(i.source == "brightspace_calendar" for i in items)


@pytest.mark.parametrize(
    "summary,base,role",
    [
        ("Assignment 4 - Due", "Assignment 4", "due"),
        ("Quiz 5 - Availability Ends", "Quiz 5", "end"),
        ("Quiz 5 - Availability Starts", "Quiz 5", "start"),
        ("Project Proposal – Due", "Project Proposal", "due"),
        ("Essay 1 is due", "Essay 1", "due"),
        ("Due: Problem Set 3", "Problem Set 3", "due"),
        ("Midterm Exam", "Midterm Exam", "plain"),
    ],
)
def test_strip_role(summary, base, role):
    assert strip_role(summary, build_rules()) == (base, role)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Midterm Exam", "midterm"),
        ("Final Exam", "exam"),
        ("Quiz 3", "quiz"),
        ("Term Test 1", "quiz"),
        ("WebWork 4", "assignment"),
        ("A4", "assignment"),
        ("Lab 7", "lab"),
        ("Week 3 Discussion", "discussion"),
        ("Group Presentation", "presentation"),
        ("Office Hours", "other"),
    ],
)
def test_classify_type(title, expected):
    assert classify_type(title, build_rules())[0] == expected


def test_institution_override_course_regex():
    inst = InstitutionConfig(
        slug="x", name="X", lms_name="D2L", hosts=("x",), timezone="America/Toronto",
        rules={"course_code_regex": r"\b([A-Z]{4})\*(\d{4})\b"},  # Guelph-style ACCT*1220
    )
    raw = (FIXTURES / "synthetic_before.ics").read_bytes().replace(
        b"LOCATION:BU111 - Fall 2026", b"LOCATION:ACCT*1220 - Fall 2026"
    )
    items = normalize_events(parse_ics(raw).events, inst)
    assert any(i.course_code == "ACCT1220" for i in items)


@pytest.mark.parametrize(
    "location,label,venue",
    [
        ("CS 115 - Fall 2026", "CS 115 - Fall 2026", None),
        ("Zoom link (BU-111-All-Sections - Understanding the Business Environment)",
         "BU-111-All-Sections - Understanding the Business Environment", "Zoom link"),
        ("LH1001 (BBA Program Information)", "BBA Program Information", "LH1001"),
        ("Math Ready Modules (2026)", "Math Ready Modules (2026)", None),
        ("Ron Eydt Village (Residence Experience)", "Residence Experience", "Ron Eydt Village"),
    ],
)
def test_split_location_real_patterns(location, label, venue):
    from spike.normalize import split_location
    assert split_location(location) == (label, venue)


def test_room_in_venue_is_not_a_course_code():
    from spike.normalize import detect_course
    from spike.parse import RawEvent
    ev = RawEvent(uid="u", summary="Info session", description=None, location="LH1001 (BBA Program Information)",
                  categories=[], url=None, dtstart=None, dtend=None, status=None, sequence=None, rrule=None,
                  last_modified=None)
    cm = detect_course(ev, build_rules())
    assert cm.code is None and cm.label == "BBA Program Information" and cm.how == "location:no_code"


def test_laurier_section_style_codes():
    from spike.normalize import detect_course
    from spike.parse import RawEvent
    ev = RawEvent(uid="u", summary="Quiz", description=None, location="EC-120-A-B-C - Intro To Microeconomics",
                  categories=[], url=None, dtstart=None, dtend=None, status=None, sequence=None, rrule=None,
                  last_modified=None)
    cm = detect_course(ev, build_rules())
    assert (cm.code, cm.name) == ("EC120", "Intro To Microeconomics")
