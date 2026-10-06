import html
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.connectors.brightspace import fetch as fetch_module
from app.db import utcnow
from app.main import create_app
from app.models import LmsConnection, Message, SetupToken, User
from app.security import create_setup_token

FIXTURES = Path(__file__).parent / "fixtures"
FEED = "https://mylearningspace.wlu.ca/d2l/le/calendar/feed/user/feed.ics?token=SECRET999"


@pytest.fixture
def setup(session_factory, monkeypatch):
    monkeypatch.setattr(fetch_module, "fetch_feed",
                        lambda url, inst, client=None: (FIXTURES / "real_laurier_anon.ics").read_bytes())
    s = session_factory()
    user = User(phone="+15195550187", institution="laurier", onboarding_step="awaiting_connection",
                status="onboarding", consent_at=utcnow())
    s.add(user)
    s.flush()
    token = create_setup_token(s, user, "connect_calendar")
    s.commit()
    return TestClient(create_app()), token, s


def test_connect_page_renders_for_the_students_school(setup):
    client, token, _ = setup
    r = client.get(f"/s/{token}")
    assert r.status_code == 200
    assert "Connect MyLearningSpace" in r.text and "Wilfrid Laurier University" in r.text
    assert "https://mylearningspace.wlu.ca/d2l/le/calendar/6605" in r.text  # deep link to all-courses calendar
    assert "/static/logos/laurier.png" in r.text
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"


def test_bad_link_shows_the_reason(setup):
    client, token, s = setup
    r = client.post(f"/s/{token}", data={"feed_url": "https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=x"})
    assert r.status_code == 422 and "MyLearningSpace links start with" in r.text
    assert s.query(LmsConnection).count() == 0


def test_successful_connect_texts_the_student_and_burns_the_link(setup):
    client, token, s = setup
    r = client.post(f"/s/{token}", data={"feed_url": FEED})
    assert r.status_code == 200 and "You're set" in r.text and "Connected ✅" in r.text
    s.expire_all()
    user = s.query(User).one()
    assert user.onboarding_step == "done" and user.status == "active"
    assert s.query(Message).filter_by(direction="out").one().body.startswith("Connected ✅")
    assert s.query(SetupToken).one().used_at is not None
    again = client.get(f"/s/{token}")
    assert again.status_code == 410 and "already used" in again.text


def test_expired_and_unknown_links(setup):
    client, token, s = setup
    s.query(SetupToken).one().expires_at = utcnow() - timedelta(minutes=1)
    s.commit()
    assert "expired" in client.get(f"/s/{token}").text
    assert "isn't valid" in html.unescape(client.get("/s/not-a-token").text)


def test_terms_page(setup):
    client, _, _ = setup
    assert "Text STOP anytime" in client.get("/terms").text
