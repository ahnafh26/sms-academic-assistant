from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import scheduler
from app.connectors.brightspace.fetch import FetchError
from app.models import AcademicItem, LmsConnection, Message, Reminder, User
from app.security import encrypt
from conftest import NOW, TZ

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=T"


@pytest.fixture
def student(session):
    u = User(phone="+15195550187", institution="waterloo", status="active", onboarding_step="done")
    session.add(u)
    session.flush()
    return u


def add_item(session, user, title, when, *, course="BU111", type="assignment", **kw):
    item = AcademicItem(user_id=user.id, external_source_id=title, institution="waterloo", course_code=course,
                        title=title, type=type, category="exam" if type in ("exam", "midterm") else "deliverable",
                        org_kind="course", **({"start_at": when} if type in ("exam", "midterm") else {"due_at": when}),
                        **kw)
    session.add(item)
    session.flush()
    return item


def texts(session, kind=None):
    q = session.query(Message).filter_by(direction="out")
    if kind:
        q = q.filter_by(kind=kind)
    return [m.body for m in q.order_by(Message.created_at)]


def at(hour, minute=0, day=5):
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


# ------------------------------------------------------------------ briefing

def test_briefing_lists_today_coming_up_and_overdue(session, student):
    add_item(session, student, "Quiz 5", at(16))
    add_item(session, student, "Midterm", at(19, day=7), course="EC120", type="midterm")
    add_item(session, student, "Essay", at(23, 59, day=3))
    add_item(session, student, "Far away", at(12, day=20))
    add_item(session, student, "Already done", at(17), completed_at=NOW)

    assert scheduler.send_briefings(session, at(8, 1)) == 1
    body = texts(session, "briefing")[0]
    assert "Today:\n• BU111 Quiz 5, due 4:00 PM" in body
    assert "Coming up:\n• EC120 Midterm, starts Wed Oct 7, 7:00 PM" in body
    assert "Still open (past due):\n• BU111 Essay, was due Sat Oct 3, 11:59 PM" in body
    assert "Far away" not in body and "Already done" not in body
    assert student.last_briefing_date == at(8).date()

    assert scheduler.send_briefings(session, at(8, 2)) == 0  # once a day


def test_briefing_time_window_and_setting(session, student):
    student.briefing_time = "07:30"
    assert scheduler.send_briefings(session, at(7, 29)) == 0
    assert scheduler.send_briefings(session, at(9, 31)) == 0  # more than 2h late: skip the day
    assert scheduler.send_briefings(session, at(7, 30)) == 1
    assert texts(session)[0].startswith("Good morning! Nothing due")

    student.briefing_enabled = False
    assert scheduler.send_briefings(session, at(7, 30, day=6)) == 0


# ------------------------------------------------------------------ due soon

def test_due_soon_fires_once_per_due_time_and_skips_finished(session, student):
    add_item(session, student, "Quiz 5", at(16))
    add_item(session, student, "Lab 2", at(16), course="CH101")
    add_item(session, student, "Done one", at(16, 30), completed_at=NOW)

    assert scheduler.send_due_soon(session, at(12, 59)) == 0   # default: 3 hours before
    assert scheduler.send_due_soon(session, at(13, 0)) == 1
    assert scheduler.send_due_soon(session, at(13, 1)) == 0
    assert texts(session, "due_soon") == [
        "Heads up: BU111 Quiz 5 is due at 4:00 PM; CH101 Lab 2 is due at 4:00 PM. "
        "Reply \"done\" if you've already finished."]


def test_due_soon_rearms_when_a_deadline_moves_and_can_be_turned_off(session, student):
    quiz = add_item(session, student, "Quiz 5", at(16))
    scheduler.send_due_soon(session, at(13))
    quiz.due_at = at(18)
    assert scheduler.send_due_soon(session, at(15)) == 1

    student.due_reminder_minutes = None
    quiz.due_at = at(20)
    assert scheduler.send_due_soon(session, at(17)) == 0


def test_due_soon_waits_out_quiet_hours(session, student):
    add_item(session, student, "Early quiz", at(9))
    assert scheduler.send_due_soon(session, at(6)) == 0      # 6 AM is quiet
    assert scheduler.send_due_soon(session, at(8)) == 1      # still before it's due


# ------------------------------------------------------------------ custom reminders

def test_custom_reminders_fire_and_skip_finished_items(session, student):
    essay = add_item(session, student, "Essay", at(23, 59, day=6), course="EN101")
    quiz = add_item(session, student, "Quiz 5", at(16))
    session.add_all([
        Reminder(user_id=student.id, note="start the essay", item_id=essay.id, fire_at=at(20)),
        Reminder(user_id=student.id, note="call mom", fire_at=at(23)),   # asked for, so quiet hours don't apply
        Reminder(user_id=student.id, item_id=quiz.id, fire_at=at(14)),
        Reminder(user_id=student.id, note="later", fire_at=at(9, day=6)),
    ])
    quiz.completed_at = NOW
    session.flush()

    assert scheduler.send_custom_reminders(session, at(23, 1)) == 2
    assert texts(session, "reminder") == [
        "Reminder: start the essay. EN101 Essay is due Tue Oct 6, 11:59 PM.", "Reminder: call mom"]
    statuses = sorted(r.status for r in session.query(Reminder))
    assert statuses == ["active", "cancelled", "sent", "sent"]
    assert scheduler.send_custom_reminders(session, at(23, 2)) == 0


# ------------------------------------------------------------------ background sync

def _connect(session, student, *bodies):
    conn = LmsConnection(user_id=student.id, institution="waterloo", encrypted_secret=encrypt(URL))
    session.add(conn)
    session.flush()
    queue = [(FIXTURES / b).read_bytes() if b.endswith(".ics") else b for b in bodies]

    def fetch(url, inst):
        body = queue.pop(0)
        if body == "FAIL":
            raise FetchError("HTTP 403")
        return body
    return conn, fetch


def test_background_sync_texts_new_and_moved_deadlines(session, student):
    conn, fetch = _connect(session, student, "synthetic_before.ics", "synthetic_after.ics")
    assert scheduler.sync_due_connections(session, NOW, fetcher=fetch) == 1   # first sync: no alert
    assert texts(session) == []
    assert scheduler.sync_due_connections(session, NOW + timedelta(hours=1), fetcher=fetch) == 0  # not due yet

    assert scheduler.sync_due_connections(session, NOW + timedelta(hours=3), fetcher=fetch) == 1
    (alert,) = texts(session, "alert")
    assert alert.startswith("Update from LEARN:")
    assert "• New: CS135 Assignment 5, due Wed Oct 21, 10:00 PM" in alert
    assert "• Moved: CS135 Assignment 4, now Fri Oct 9, 10:00 PM (was Wed Oct 7, 10:00 PM)" in alert


def test_alerts_can_be_turned_off_and_skip_quiet_hours(session, student):
    student.alerts_enabled = False
    conn, fetch = _connect(session, student, "synthetic_before.ics", "synthetic_after.ics")
    assert scheduler.sync_due_connections(session, at(23), fetcher=fetch) == 0   # quiet hours: wait
    scheduler.sync_due_connections(session, NOW, fetcher=fetch)
    scheduler.sync_due_connections(session, NOW + timedelta(hours=3), fetcher=fetch)
    assert texts(session) == []


def test_broken_feed_texts_once(session, student):
    conn, fetch = _connect(session, student, "FAIL", "FAIL", "FAIL", "FAIL")
    for hours in (0, 3, 6):
        scheduler.sync_due_connections(session, NOW + timedelta(hours=hours), fetcher=fetch)
    assert conn.status == "error"
    assert scheduler.sync_due_connections(session, NOW + timedelta(hours=9), fetcher=fetch) == 0  # retried daily
    scheduler.sync_due_connections(session, NOW + timedelta(hours=30), fetcher=fetch)
    (notice,) = texts(session)
    assert "can't read your LEARN calendar" in notice and "Reply LINK" in notice


def test_tick_runs_every_job_and_survives_a_failing_one(session, student, monkeypatch):
    def boom(session, now):
        raise RuntimeError("boom")
    monkeypatch.setattr(scheduler, "JOBS", (("due_soon", boom), ("briefings", scheduler.send_briefings)))
    assert scheduler.tick(session, at(8)) == {"due_soon": -1, "briefings": 1}
