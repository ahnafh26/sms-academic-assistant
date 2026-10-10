import re
from pathlib import Path

import pytest

from app.connectors.brightspace import fetch as fetch_module
from app.models import LmsConnection, Message, SetupToken, User
from app.router import handle_inbound
from app.security import decrypt
from conftest import NOW

FIXTURES = Path(__file__).parent / "fixtures"
FEED = "https://mylearningspace.wlu.ca/d2l/le/calendar/feed/user/feed.ics?token=SECRET999"


@pytest.fixture(autouse=True)
def fake_feed(monkeypatch):
    calls = []

    def fetch(url, inst, client=None):
        calls.append(url)
        return (FIXTURES / "real_laurier_anon.ics").read_bytes()
    monkeypatch.setattr(fetch_module, "fetch_feed", fetch)
    return calls


def text(session, body):
    return handle_inbound(session, "+15195550187", body, now=NOW, brain=lambda s, u, t, now=None: f"brain: {t}")


def user(session):
    return session.query(User).one()


def test_full_signup_by_text(session, fake_feed):
    hello = text(session, "hi")
    assert "school assistant" in hello[0] and "Reply YES" in hello[1]
    assert "Reply YES" in text(session, "what is this")[0]           # no consent yet

    assert text(session, "yes") == ["What school do you go to?"]
    assert user(session).consent_at == NOW and user(session).terms_version

    replies = text(session, "laurier")
    assert replies[0] == "Got it, Wilfrid Laurier University."
    link = re.search(r"https?://\S+/s/(\S+)", replies[1])
    assert link and "expires in 30 min" in replies[1]
    assert session.query(SetupToken).count() == 1
    assert user(session).onboarding_step == "awaiting_connection"

    assert "Almost there" in text(session, "ok")[0]
    assert "Tap this link" in text(session, "send the link again")[0]
    assert session.query(SetupToken).count() == 2

    # paste the calendar link straight into the thread
    assert text(session, f"here {FEED}") == []          # success text is queued by the connect step
    u = user(session)
    assert u.onboarding_step == "done" and u.status == "active"
    conn = session.query(LmsConnection).one()
    assert decrypt(conn.encrypted_secret) == FEED and conn.last_success_at == NOW
    last = session.query(Message).filter_by(direction="out").order_by(Message.created_at.desc()).first()
    assert last.body.startswith("Connected! I found 2 courses and 37 upcoming deadlines.")
    assert "Next up:" in last.body

    assert text(session, "what's due today") == ["brain: what's due today"]


def test_school_answers(session):
    text(session, "hi"); text(session, "yes")
    assert text(session, "university")[0].startswith("Did you mean")   # every school is a university
    assert "don't support that school" in text(session, "harvard")[0]
    assert text(session, "uw")[0] == "Got it, University of Waterloo."
    assert text(session, "wrong school") == ["What school do you go to?"]
    assert user(session).institution is None


def test_bad_pasted_link_explains(session):
    text(session, "hi"); text(session, "yes"); text(session, "wlu")
    reply = text(session, "https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=x")
    assert "MyLearningSpace links start with" in reply[0]
    assert user(session).onboarding_step == "awaiting_connection"


def test_unreadable_feed_explains(session, monkeypatch):
    def broken(url, inst, client=None):
        raise fetch_module.FetchError("response is not a calendar")
    monkeypatch.setattr(fetch_module, "fetch_feed", broken)
    text(session, "hi"); text(session, "yes"); text(session, "laurier")
    reply = text(session, FEED)
    assert "couldn't read that calendar" in reply[0]
    assert session.query(LmsConnection).one().status == "error"
