"""Twilio webhooks: only signed requests count, replies go out through the transport, receipts update status."""

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app import messaging
from app.config import get_settings
from app.main import create_app
from app.models import Message, User

TOKEN = "test-auth-token"
BASE = "https://assistant.example.app"


@pytest.fixture
def client(session_factory, monkeypatch):
    for key, value in {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": TOKEN,
                       "TWILIO_FROM_NUMBER": "+18885550123", "PUBLIC_BASE_URL": BASE}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    sent = []
    app = create_app()  # registers the real Twilio transport; swap it for a recorder
    messaging.set_transport(lambda phone, body: sent.append((phone, body)) or f"SMout{len(sent)}")
    yield TestClient(app), sent
    messaging.set_transport(None)
    get_settings.cache_clear()


def post(client, path, params, *, sign=True):
    signature = RequestValidator(TOKEN).compute_signature(BASE + path, params) if sign else "bogus"
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature})


def test_signed_inbound_text_is_answered_through_twilio(client, session_factory):
    http, sent = client
    r = post(http, "/webhooks/twilio/sms", {"From": "+15195550187", "Body": "hi", "MessageSid": "SMin1"})
    assert r.status_code == 200 and r.text.endswith("<Response></Response>")
    assert len(sent) == 2 and sent[0][0] == "+15195550187" and "school assistant" in sent[0][1]

    s = session_factory()
    assert s.query(User).one().onboarding_step == "awaiting_consent"
    outs = s.query(Message).filter_by(direction="out").all()
    assert {m.status for m in outs} == {"sent"} and {m.provider_sid for m in outs} == {"SMout1", "SMout2"}

    # Twilio retries the same webhook: handled once
    post(http, "/webhooks/twilio/sms", {"From": "+15195550187", "Body": "hi", "MessageSid": "SMin1"})
    assert len(sent) == 2


def test_unsigned_requests_are_rejected(client, session_factory):
    http, sent = client
    r = post(http, "/webhooks/twilio/sms", {"From": "+15195550187", "Body": "hi", "MessageSid": "SMx"}, sign=False)
    assert r.status_code == 403 and sent == []
    assert session_factory().query(Message).count() == 0


def test_delivery_receipts_update_status(client, session_factory):
    http, _ = client
    post(http, "/webhooks/twilio/sms", {"From": "+15195550187", "Body": "hi", "MessageSid": "SMin1"})
    post(http, "/webhooks/twilio/status", {"MessageSid": "SMout1", "MessageStatus": "delivered"})
    post(http, "/webhooks/twilio/status", {"MessageSid": "SMout2", "MessageStatus": "undelivered",
                                           "ErrorCode": "30006"})
    statuses = {m.provider_sid: m.status for m in session_factory().query(Message).filter_by(direction="out")}
    assert statuses == {"SMout1": "delivered", "SMout2": "failed"}


def test_a_failed_send_is_recorded_without_undoing_the_conversation(client, session_factory):
    http, _ = client

    def broken(phone, body):
        raise ConnectionError("twilio down")
    messaging.set_transport(broken)
    post(http, "/webhooks/twilio/sms", {"From": "+15195550187", "Body": "hi", "MessageSid": "SMin1"})
    s = session_factory()
    assert s.query(User).one().onboarding_step == "awaiting_consent"
    assert {m.status for m in s.query(Message).filter_by(direction="out")} == {"failed"}
