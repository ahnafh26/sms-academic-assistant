"""Normalization rules: shared defaults + per-institution overrides.

Everything school-specific should be expressible as an override in
institutions.yaml -> rules, never as a code branch on the school slug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Brightspace emits one event per date on an activity ("Assignment 4 - Due",
# "Quiz 5 - Availability Ends", ...). Role tells us which field the date feeds.
DEFAULT_SUFFIXES: list[tuple[str, str]] = [
    (r"\s*[-–—:]\s*due$", "due"),
    (r"\s+is\s+due$", "due"),
    (r"\s*[-–—:]\s*(?:availability\s+)?(?:ends?|end\s+date|closes?)$", "end"),
    (r"\s*[-–—:]\s*(?:availability\s+)?(?:starts?|start\s+date|begins?|opens?)$", "start"),
    (r"\s*[-–—:]\s*available$", "start"),
]
DEFAULT_PREFIXES: list[tuple[str, str]] = [
    (r"^due\s*[-–—:]\s*", "due"),
]

# Ordered: first match wins ("Midterm Exam" must be midterm, not exam).
DEFAULT_TYPE_RULES: list[tuple[str, str]] = [
    ("midterm", r"\bmid-?\s?terms?\b"),
    ("exam", r"\bfinal\s+exam\b|\bexam(?:ination)?s?\b|\bfinal\b"),
    ("quiz", r"\bquiz(?:zes)?\b|\btest\b"),
    ("lab", r"\blab(?:oratory)?s?\b"),
    ("discussion", r"\bdiscussion\b|\bforum\b"),
    ("presentation", r"\bpresentations?\b"),
    ("reading", r"\breadings?\b"),
    ("lecture", r"\blectures?\b"),
    (
        "assignment",
        r"\bassignments?\b|\bhomework\b|\bhw\s?\d+\b|\ba\d{1,2}\b|\bproblem\s+sets?\b|\bwebwork\b"
        r"|\bprojects?\b|\breports?\b|\bessays?\b|\bdropbox\b|\bsubmission\b|\breflections?\b",
    ),
]

# e.g. "CS 135", "CS135", "MATH 135", "BU111", "SPCOM 223", "CP104A"
DEFAULT_COURSE_CODE = r"\b([A-Z]{2,5})\s?-?\s?(\d{2,4}[A-Z]?)\b"
# Uppercase words that look like subject codes but aren't.
COURSE_CODE_STOPWORDS = {
    "LAB", "QUIZ", "TEST", "WEEK", "UNIT", "PART", "EXAM", "TERM", "FALL", "WINTER",
    "SPRING", "DUE", "HW", "MOD", "CH", "SEC", "LEC", "TUT", "ROOM", "RM", "PAGE",
}
DEFAULT_COURSE_FIELDS = ["location", "categories", "description", "summary"]
TERM_PATTERN = r"\b(?:fall|winter|spring|summer|autumn)\s*(?:term\s*)?(?:20\d{2})?\b|\b20\d{2}\s*(?:fall|winter|spring|summer)\b|\b[FWS]\d{2}\b"
ORG_UNIT_PATTERNS = [
    r"/d2l/le/calendar/(\d+)",
    r"/d2l/home/(\d+)",
    r"[?&]ou=(\d+)",
    r"/d2l/(?:le|lms)/[a-z_]+/(\d{4,})/",
]


@dataclass(frozen=True)
class Rules:
    suffixes: list[tuple[re.Pattern, str]]
    prefixes: list[tuple[re.Pattern, str]]
    type_rules: list[tuple[str, re.Pattern]]
    course_code: re.Pattern
    course_fields: list[str]
    term: re.Pattern
    org_unit: list[re.Pattern]
    all_day_due_time: str  # local time applied when a due event is an all-day DATE


def build_rules(overrides: dict[str, Any] | None = None) -> Rules:
    o = overrides or {}
    suffixes = [*o.get("extra_suffixes", []), *DEFAULT_SUFFIXES]
    prefixes = [*o.get("extra_prefixes", []), *DEFAULT_PREFIXES]
    type_rules = [*o.get("extra_type_rules", []), *DEFAULT_TYPE_RULES]
    return Rules(
        suffixes=[(re.compile(p, re.I), role) for p, role in suffixes],
        prefixes=[(re.compile(p, re.I), role) for p, role in prefixes],
        type_rules=[(t, re.compile(p, re.I)) for t, p in type_rules],
        course_code=re.compile(o.get("course_code_regex", DEFAULT_COURSE_CODE)),
        course_fields=list(o.get("course_fields", DEFAULT_COURSE_FIELDS)),
        term=re.compile(TERM_PATTERN, re.I),
        org_unit=[re.compile(p) for p in ORG_UNIT_PATTERNS],
        all_day_due_time=o.get("all_day_due_time", "23:59"),
    )
