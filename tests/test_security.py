from datetime import timedelta

import pytest

from app import security
from app.db import utcnow
from app.models import SetupToken, User


def test_encrypt_round_trip_and_no_plaintext():
    secret = "https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=SECRET123"
    blob = security.encrypt(secret)
    assert b"SECRET123" not in blob
    assert security.decrypt(blob) == secret


def test_setup_token_lifecycle(session):
    user = User(phone="+15195550187")
    session.add(user)
    session.flush()
    raw = security.create_setup_token(session, user, "connect_calendar")
    stored = session.query(SetupToken).one()
    assert raw not in stored.token_hash and len(raw) >= 40

    ok = security.check_setup_token(session, raw, "connect_calendar")
    assert ok.problem is None and ok.token.user_id == user.id
    assert security.check_setup_token(session, raw, "other_purpose").problem
    assert "isn't valid" in security.check_setup_token(session, "nope", "connect_calendar").problem

    stored.used_at = utcnow()
    assert "already used" in security.check_setup_token(session, raw, "connect_calendar").problem
    stored.used_at = None
    stored.expires_at = utcnow() - timedelta(minutes=1)
    assert "expired" in security.check_setup_token(session, raw, "connect_calendar").problem


def test_setup_link():
    assert security.setup_link("abc").endswith("/s/abc")


@pytest.mark.parametrize("raw,expected", [
    ("519-555-0187", "+15195550187"), ("(519) 555 0187", "+15195550187"),
    ("15195550187", "+15195550187"), ("+1 519 555 0187", "+15195550187"), ("+447700900123", "+447700900123"),
])
def test_normalize_phone(raw, expected):
    assert security.normalize_phone(raw) == expected


def test_normalize_phone_rejects_junk():
    with pytest.raises(ValueError):
        security.normalize_phone("hello")


def test_mask_phone():
    assert security.mask_phone("+15195550187") == "(•••) •••-0187"
