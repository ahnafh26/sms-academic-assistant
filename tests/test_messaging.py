from datetime import timedelta

from app.messaging import DAILY_PROACTIVE_CAP, in_quiet_hours, send_text
from app.models import Message, User
from conftest import NOW


def _student(session, **kw):
    u = User(phone="+15195550187", status="active", onboarding_step="done", **kw)
    session.add(u)
    session.flush()
    return u


def test_dedupe_key_sends_once(session):
    u = _student(session)
    assert send_text(session, u, "hi", kind="reminder", dedupe_key="reminder:1", now=NOW)
    assert send_text(session, u, "hi again", kind="reminder", dedupe_key="reminder:1", now=NOW) is None
    assert session.query(Message).count() == 1


def test_quiet_hours_hold_alerts_but_not_replies_briefings_or_asked_for_reminders(session):
    u = _student(session)
    night = NOW.replace(hour=23)  # 11 PM Toronto
    assert in_quiet_hours(u, night) and in_quiet_hours(u, NOW.replace(hour=7, minute=59))
    assert not in_quiet_hours(u, NOW.replace(hour=8))
    assert send_text(session, u, "alert", kind="alert", now=night) is None
    assert send_text(session, u, "due soon", kind="due_soon", now=night) is None
    assert send_text(session, u, "briefing at the time they picked", kind="briefing", now=night)
    assert send_text(session, u, "reply", now=night)
    assert send_text(session, u, "you asked", kind="reminder", now=night)


def test_daily_cap_on_proactive_texts(session):
    u = _student(session)
    for n in range(DAILY_PROACTIVE_CAP):
        assert send_text(session, u, f"alert {n}", kind="alert", now=NOW + timedelta(minutes=n))
    assert send_text(session, u, "one too many", kind="due_soon", now=NOW + timedelta(hours=1)) is None
    assert send_text(session, u, "next day", kind="due_soon", now=NOW + timedelta(days=1))


def test_onboarding_students_get_no_proactive_texts(session):
    u = _student(session)
    u.status = "onboarding"
    assert send_text(session, u, "briefing", kind="briefing", now=NOW) is None
    u.status = "opted_out"
    assert send_text(session, u, "reply", now=NOW) is None
