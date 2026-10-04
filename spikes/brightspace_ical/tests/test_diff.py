from pathlib import Path

import pytest

from spike.config import InstitutionConfig
from spike.diff import diff_events, diff_items, render_markdown
from spike.normalize import normalize_events
from spike.parse import parse_ics

FIXTURES = Path(__file__).parent / "fixtures"
INST = InstitutionConfig(
    slug="waterloo", name="UW", lms_name="LEARN", hosts=("learn.example.ca",), timezone="America/Toronto"
)


def _events(name):
    return parse_ics((FIXTURES / name).read_bytes()).events


@pytest.fixture(scope="module")
def event_diff():
    return diff_events(_events("synthetic_before.ics"), _events("synthetic_after.ics"))


@pytest.fixture(scope="module")
def item_changes():
    before = normalize_events(_events("synthetic_before.ics"), INST)
    after = normalize_events(_events("synthetic_after.ics"), INST)
    return diff_items(before, after)


def _kinds(changes, kind):
    return [c for c in changes if c.kind == kind]


def test_event_level_uid_accounting(event_diff):
    assert {e.uid for e in event_diff.removed} == {
        "6606-2001-due@synthetic.d2l", "6606-2001-end@synthetic.d2l", "6606-4001-due@synthetic.d2l"
    }
    assert {e.uid for e in event_diff.added} == {"6606-1002-due@synthetic.d2l", "6606-9001-due@synthetic.d2l"}
    assert event_diff.uid_retention == pytest.approx(7 / 10)


def test_moved_deadline_keeps_uid(event_diff):
    changed = {old.uid: fields for old, _, fields in event_diff.changed}
    assert {"dtstart", "dtend", "sequence"} <= set(changed["6606-1001-due@synthetic.d2l"])


def test_due_date_change_detected(item_changes):
    (moved,) = _kinds(item_changes, "due_changed")
    assert moved.title == "Assignment 4"
    assert moved.old["due_at_local"] == "2026-10-07T22:00:00-04:00"
    assert moved.new["due_at_local"] == "2026-10-09T22:00:00-04:00"
    assert moved.matched_by == "external_source_id" and moved.significant


def test_removed_and_created(item_changes):
    assert [c.title for c in _kinds(item_changes, "removed")] == ["Quiz 5"]
    assert [c.title for c in _kinds(item_changes, "created")] == ["Assignment 5"]


def test_title_change_same_uid(item_changes):
    (renamed,) = _kinds(item_changes, "title_changed")
    assert renamed.old["title"] == "Lab Report 2" and renamed.new["title"] == "Lab Report 2 Revised"


def test_uid_instability_caught_by_fingerprint(item_changes):
    (reissued,) = _kinds(item_changes, "id_changed")
    assert reissued.title == "Weekly Reflection" and reissued.matched_by == "fingerprint"
    assert not reissued.significant
    # the re-issued item must NOT also appear as removed + created
    assert "Weekly Reflection" not in [c.title for c in item_changes if c.kind in ("removed", "created")]


def test_cosmetic_description_change_is_not_an_item_change(item_changes):
    a4_kinds = {c.kind for c in item_changes if c.title == "Assignment 4"}
    assert a4_kinds == {"due_changed"}


def test_markdown_report(event_diff, item_changes):
    md = render_markdown(event_diff, item_changes, old_label="before", new_label="after")
    assert "UID retention: **70%**" in md
    assert "events whose time changed but UID stayed the same: **1**" in md
    assert "| yes | due_changed | CS135 | Assignment 4 |" in md
