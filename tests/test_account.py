import re
from datetime import timedelta
from pathlib import Path

import pytest

from app import account
from app.connectors.brightspace import fetch as fetch_module
from app.messaging import take_queued
from app.models import (AcademicItem, ConversationTurn, Course, LmsConnection, Message, Reminder, SetupToken,
                        SyncRun, User)
from app.router import handle_inbound
from conftest import NOW

FIXTURES = Path(__file__).parent / "fixtures"
PHONE = "+15195550187"
FEED = "https://mylearningspace.wlu.ca/d2l/le/calendar/feed/user/feed.ics?token=SECRET999"


@pytest.fixture(autouse=True)
def fake_feed(monkeypatch):
    monkeypatch.setattr(fetch_module, "fetch_feed",
                        lambda url, inst, client=None: (FIXTURES / "real_laurier_anon.ics").read_bytes())


def text(session, body, now=NOW):
    return handle_inbound(session, PHONE, body, now=now, brain=lambda s, u, t, now=None: f"brain: {t}")


def connected_student(session):
    for body in ("hi", "yes", "laurier", FEED):
        text(session, body)
    user = session.query(User).one()
    assert user.onboarding_step == "done"
    session.add(Reminder(user_id=user.id, note="x", fire_at=NOW + timedelta(hours=1)))
    session.add(ConversationTurn(user_id=user.id, messages=[]))
    session.flush()
    return user


def test_delete_needs_the_code_then_erases_everything(session):
    connected_student(session)
    ask = text(session, "Delete my account")[0]
    code = re.search(r"DELETE (\d{4})", ask).group(1)
    wrong = f"{(int(code) + 1) % 10_000:04d}"
    assert text(session, f"delete {wrong}") == [account.WRONG_CODE_TEXT]
    assert session.query(User).count() == 1

    assert text(session, f"DELETE {code}") == []
    for table in (User, LmsConnection, AcademicItem, Course, Reminder, ConversationTurn, SetupToken, SyncRun):
        assert session.query(table).count() == 0, table.__name__
    # only the goodbye is left, queued for the fake phone, with no account attached
    (goodbye,) = session.query(Message).all()
    assert goodbye.user_id is None and goodbye.body == account.GOODBYE_TEXT
    assert [m.body for m in take_queued(session, PHONE)] == [account.GOODBYE_TEXT]


def test_delete_code_expires(session):
    connected_student(session)
    code = re.search(r"DELETE (\d{4})", text(session, "delete my data")[0]).group(1)
    assert text(session, f"DELETE {code}", now=NOW + timedelta(minutes=11)) == [account.WRONG_CODE_TEXT]
    assert session.query(User).count() == 1


def test_connected_student_can_get_a_new_link_or_paste_one(session):
    connected_student(session)
    assert "Tap this link to connect MyLearningSpace" in text(session, "link")[0]
    assert text(session, f"new one: {FEED}") == []          # reconnects; "Connected ✅" is sent by the connect step
    conns = session.query(LmsConnection).order_by(LmsConnection.created_at).all()
    assert [c.status for c in conns] == ["revoked", "active"]
    assert text(session, "what's due") == ["brain: what's due"]


def test_help_mentions_the_real_commands(session):
    reply = text(session, "help")[0]
    assert "LINK" in reply and "DELETE MY ACCOUNT" in reply and "STOP" in reply and "MANAGE" not in reply
