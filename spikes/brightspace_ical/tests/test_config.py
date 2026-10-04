import pytest

from spike.config import load_institutions
from spike.normalize import detect_course
from spike.parse import RawEvent
from spike.rules import build_rules


def _event(location: str) -> RawEvent:
    return RawEvent(
        uid="u", summary="Assignment 1 - Due", description=None, location=location, categories=[],
        url=None, dtstart=None, dtend=None, status=None, sequence=None, rrule=None, last_modified=None,
    )


def test_registry_loads_all_schools():
    institutions = load_institutions()
    assert {"waterloo", "laurier", "mcmaster", "guelph", "tmu", "western", "queens"} <= set(institutions)
    assert all(i.timezone == "America/Toronto" for i in institutions.values())
    assert institutions["waterloo"].allows_host("learn.uwaterloo.ca")
    assert not institutions["waterloo"].allows_host("learn.uwaterloo.ca.evil.com")


@pytest.mark.parametrize(
    "school,location,code",
    [
        ("waterloo", "CS 135 - Fall 2026", "CS135"),
        ("waterloo", "SPCOM 223 - Fall 2026", "SPCOM223"),
        ("laurier", "BU111 - Fall 2026", "BU111"),
        ("mcmaster", "COMPSCI 1MD3 - Fall 2026", "COMPSCI1MD3"),
        ("guelph", "CIS*1300 - Fall 2026", "CIS1300"),
        ("western", "CS 1026A - Fall 2026", "CS1026A"),
    ],
)
def test_course_code_formats_per_school(school, location, code):
    rules = build_rules(load_institutions()[school].rules)
    assert detect_course(_event(location), rules)[0] == code
