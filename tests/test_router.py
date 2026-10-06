from app.messaging import take_queued
from app.models import Message, User
from app.router import handle_inbound
from conftest import NOW

PHONE = "+15195550187"


def stub_onboarding(session, user, text, now=None):
    return [f"onboarding got: {text}"]


def stub_brain(session, user, text, now=None):
    return f"brain got: {text}"


def inbound(session, body, **kw):
    return handle_inbound(session, "519-555-0187", body, now=NOW, onboarding=stub_onboarding, brain=stub_brain, **kw)


def test_new_number_creates_student_and_logs_both_directions(session):
    assert inbound(session, "hi") == ["onboarding got: hi"]
    user = session.query(User).one()
    assert user.phone == PHONE and user.status == "onboarding"
    assert [(m.direction, m.body) for m in session.query(Message).order_by(Message.created_at)] == [
        ("in", "hi"), ("out", "onboarding got: hi")]


def test_stop_then_silence_then_start(session):
    inbound(session, "hi")
    assert inbound(session, "stop") == []
    user = session.query(User).one()
    assert user.status == "opted_out" and user.opted_out_at == NOW
    assert inbound(session, "what's due") == []  # no texts to opted-out numbers
    assert inbound(session, "START") == ["Welcome back 👋 You're subscribed again."]
    assert user.status == "onboarding"


def test_help_never_reaches_the_llm(session):
    reply = inbound(session, "help")[0]
    assert "STOP to unsubscribe" in reply


def test_done_students_go_to_the_brain(session):
    inbound(session, "hi")
    user = session.query(User).one()
    user.onboarding_step, user.status = "done", "active"
    assert inbound(session, "what's due today") == ["brain got: what's due today"]


def test_duplicate_webhook_ignored(session):
    assert inbound(session, "hi", provider_sid="SM123")
    assert inbound(session, "hi", provider_sid="SM123") == []
    assert session.query(Message).filter_by(direction="in").count() == 1


def test_fake_phone_reads_queued_texts_once(session):
    inbound(session, "hi")
    first = take_queued(session, PHONE)
    assert [m.body for m in first] == ["onboarding got: hi"] and take_queued(session, PHONE) == []
