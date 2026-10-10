"""Reminder and settings tools: times are checked and formatted server-side, never by the model."""

from datetime import datetime, timedelta, timezone

import pytest

from app import brain
from app.ai.llm import AssistantTurn
from app.ai.store import Store
from app.ai.tools import Toolbox
from app.messaging import send_text
from app.models import AcademicItem, LmsConnection, Reminder, User
from conftest import NOW, TZ


@pytest.fixture
def student(session):
    u = User(phone="+15195550187", institution="laurier", status="active", onboarding_step="done")
    session.add(u)
    session.flush()
    session.add(AcademicItem(user_id=u.id, external_source_id="essay", institution="laurier", course_code="EN101",
                             title="Essay", type="assignment", category="deliverable", org_kind="course",
                             due_at=datetime(2026, 10, 7, 3, 59, tzinfo=timezone.utc)))  # Tue Oct 6, 11:59 PM
    session.flush()
    return u


def toolbox(session, user, now=NOW, refresh_fn=None):
    store = Store(session, user.id)
    store.load()
    return Toolbox(store, TZ, now_fn=lambda: now, refresh_fn=refresh_fn)


def test_create_reminder_at_a_local_time_about_an_item(session, student):
    r = toolbox(session, student).call("create_reminder", {"at": "2026-10-05T20:00", "item": "essay",
                                                           "note": "start the essay"})
    view = r["reminder_set"]
    assert view["at"] == "Mon Oct 5, 8:00 PM" and view["when"] == "in 11 hours"
    assert view["about"] == "EN101 Essay" and view["note"] == "start the essay"
    row = session.query(Reminder).one()
    assert row.fire_at == datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc) and row.status == "active"


def test_reminder_time_checks(session, student):
    tb = toolbox(session, student)
    assert "already passed" in tb.call("create_reminder", {"at": "2026-10-05T08:00", "note": "x"})["error"]
    assert "YYYY-MM-DD" in tb.call("create_reminder", {"at": "8pm", "note": "x"})["error"]
    assert "ask the student when" in tb.call("create_reminder", {"note": "x"})["error"]
    assert "needs a note or an item" in tb.call("create_reminder", {"in_minutes": 20})["error"]
    assert tb.call("create_reminder", {"in_minutes": 20, "note": "laundry"})["reminder_set"]["at"] == \
        "Mon Oct 5, 9:20 AM"


def test_list_and_cancel_reminders(session, student):
    tb = toolbox(session, student)
    ref = tb.call("create_reminder", {"in_minutes": 60, "note": "one"})["reminder_set"]["ref"]
    tb.call("create_reminder", {"in_minutes": 120, "note": "two"})
    assert [r["note"] for r in tb.call("list_reminders", {})["reminders"]] == ["one", "two"]
    assert tb.call("cancel_reminder", {"reminder": ref})["cancelled"][0]["note"] == "one"
    assert "no active reminder" in tb.call("cancel_reminder", {"reminder": ref})["error"]
    assert len(tb.call("cancel_reminder", {"reminder": "all"})["cancelled"]) == 1
    assert tb.call("list_reminders", {})["reminders"] == []


def test_finishing_an_item_cancels_its_reminders(session, student):
    tb = toolbox(session, student)
    tb.call("create_reminder", {"in_minutes": 60, "item": "essay"})
    tb.call("create_reminder", {"in_minutes": 60, "note": "unrelated"})
    tb.call("mark_complete", {"item": "essay"})
    assert sorted(r.status for r in session.query(Reminder)) == ["active", "cancelled"]


def test_settings(session, student):
    tb = toolbox(session, student)
    assert tb.call("get_settings", {})["settings"]["morning_text"] == "on, at 8:00 AM"
    r = tb.call("update_settings", {"morning_text_time": "07:30", "due_soon_hours_before": 24, "alerts_on": False})
    assert r["settings"]["morning_text"] == "on, at 7:30 AM"
    assert r["settings"]["due_soon_reminders"] == "1 day before each deadline"
    assert r["settings"]["new_and_moved_deadline_alerts"] == "off"
    assert student.briefing_time == "07:30" and student.due_reminder_minutes == 1440 and not student.alerts_enabled

    tb.call("update_settings", {"morning_text_on": False, "due_soon_hours_before": 0})
    assert student.briefing_enabled is False and student.due_reminder_minutes is None
    assert "HH:MM" in tb.call("update_settings", {"morning_text_time": "7.30pm"})["error"]


def test_refresh_limit_counts_background_syncs(session, student):
    session.add(LmsConnection(user_id=student.id, institution="laurier", encrypted_secret=b"x",
                              last_sync_at=NOW - timedelta(minutes=4)))
    session.flush()
    calls = []
    r = toolbox(session, student, refresh_fn=lambda: calls.append(1) or "synced").call("refresh_data", {})
    assert r["refreshed"] is False and "try again in 6 min" in r["reason"] and calls == []


class Script:
    def __init__(self, steps):
        self.steps = list(steps)
        self.seen = []

    def complete(self, messages, tools):
        self.seen.append(messages)
        return self.steps.pop(0)


def test_brain_sees_texts_it_sent_on_its_own(session, student):
    send_text(session, student, "Heads up: EN101 Essay is due at 11:59 PM.", kind="due_soon",
              now=NOW - timedelta(hours=1))
    model = Script([AssistantTurn("Nice work!")])
    brain.set_model(model)
    try:
        brain.answer(session, student, "done", now=NOW)
    finally:
        brain.set_model(None)
    system = model.seen[0][0]["content"]
    assert "Texts you sent the student on your own" in system and "EN101 Essay is due at 11:59 PM" in system
