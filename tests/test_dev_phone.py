"""The fake phone drives the real router/outbox end to end (no server needed for these steps)."""

from app import dev
from app.models import User

PHONE = "+15195550187"


def test_fake_phone_signup_conversation(session_factory):
    lines = []
    dev.send_from_phone(PHONE, "hi")
    assert dev.deliver_pending(PHONE, out=lines.append) == 2
    assert "school assistant" in lines[0] and "Reply YES" in lines[1]

    dev.send_from_phone(PHONE, "yes")
    dev.send_from_phone(PHONE, "waterloo")
    lines.clear()
    dev.deliver_pending(PHONE, out=lines.append)
    assert "What school" in lines[0] and "University of Waterloo" in lines[1] and "/s/" in lines[2]
    assert dev.deliver_pending(PHONE, out=lines.append) == 0  # each text shows once

    dev.forget(PHONE)
    s = session_factory()
    assert s.query(User).count() == 0


def test_briefing_command_needs_a_finished_signup(session_factory):
    dev.send_from_phone(PHONE, "hi")
    dev.deliver_pending(PHONE, out=lambda line: None)
    assert dev.briefing_now(PHONE) is False

    s = session_factory()
    s.query(User).one().onboarding_step = "done"
    s.commit()
    lines = []
    assert dev.briefing_now(PHONE) is True
    dev.deliver_pending(PHONE, out=lines.append)
    assert "Good morning!" in lines[0]
