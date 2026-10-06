"""Every inbound text goes through here.

Order matters:
1. dedupe (Twilio retries) and identify the student by phone number
2. carrier-compliance keywords (STOP / START / HELP), handled without the LLM
3. opted-out numbers get no reply at all
4. sign-up steps (deterministic, no LLM)
5. everything else goes to the assistant brain
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import utcnow
from .messaging import already_received, record_inbound, send_text
from .models import User
from .security import normalize_phone

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
START_WORDS = {"START", "UNSTOP", "OPTIN"}
HELP_WORDS = {"HELP", "INFO"}

HELP_TEXT = ("{name}: I track your Brightspace deadlines by text. Ask things like \"what's due this week\". "
             "Text MANAGE for settings, STOP to unsubscribe. Msg & data rates may apply.")
STOP_TEXT = "You're unsubscribed and won't get more texts. Text START anytime to come back."
START_TEXT = "Welcome back 👋 You're subscribed again."


def handle_inbound(
    session: Session,
    phone_raw: str,
    body: str,
    *,
    provider_sid: str | None = None,
    now: datetime | None = None,
    onboarding: Callable[..., list[str]] | None = None,
    brain: Callable[..., str] | None = None,
) -> list[str]:
    """Process one inbound text; returns the replies that were queued (also useful for tests)."""
    if already_received(session, provider_sid):
        return []
    now = now or utcnow()
    phone = normalize_phone(phone_raw)
    user = session.scalar(select(User).where(User.phone == phone))
    if user is None:
        user = User(phone=phone, timezone=get_settings().default_timezone, created_at=now)
        session.add(user)
        session.flush()
    record_inbound(session, user, phone, body, provider_sid)

    text = (body or "").strip()
    keyword = text.upper().strip(" .!")
    replies: list[str]

    if keyword in STOP_WORDS:
        user.status, user.opted_out_at = "opted_out", now
        session.flush()
        return []  # carriers/Twilio send the STOP confirmation themselves; never text an opted-out number
    if keyword in START_WORDS:
        if user.status == "opted_out":
            user.status = "active" if user.onboarding_step == "done" else "onboarding"
            user.opted_out_at = None
        replies = [START_TEXT]
    elif user.status == "opted_out":
        return []
    elif keyword in HELP_WORDS:
        replies = [HELP_TEXT.format(name=get_settings().assistant_name)]
    elif user.onboarding_step != "done":
        if onboarding is None:
            from .onboarding import handle_onboarding as onboarding
        replies = onboarding(session, user, text, now=now)
    else:
        if brain is None:
            from .brain import answer as brain
        replies = [brain(session, user, text, now=now)]

    for reply in replies:
        send_text(session, user, reply)
    return replies
