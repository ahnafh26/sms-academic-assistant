"""Outbound texts go through one outbox.

Every message to a student is first recorded as a `messages` row with
status "queued"; a transport then delivers it and marks it "sent". Locally,
the fake phone is the transport (it reads queued rows). With Twilio, the
webhook/worker will deliver through the Twilio API. Opted-out numbers
are never texted.

Texts the assistant sends on its own (briefings, due-soon reminders,
change alerts) are "proactive" and capped per day; due-soon reminders and
change alerts also wait out quiet hours (the briefing goes out at the time
the student picked). Reminders the student asked for are exempt from both. A
`dedupe_key` makes any text at-most-once, so a retried or overlapping
scheduler tick can never send the same reminder twice.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import Message, User

Transport = Callable[[str, str], str | None]  # (phone, body) -> provider message id
_transport: Transport | None = None

PROACTIVE_KINDS = {"briefing", "due_soon", "alert"}
QUIET_KINDS = {"due_soon", "alert"}
QUIET_START, QUIET_END = time(22, 0), time(8, 0)
DAILY_PROACTIVE_CAP = 6


def set_transport(transport: Transport | None) -> None:
    """Deliver immediately through this function (e.g. Twilio). None = leave queued for the fake phone."""
    global _transport
    _transport = transport


def in_quiet_hours(user: User, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(user.timezone)).time()
    return local >= QUIET_START or local < QUIET_END


def proactive_sent_today(session: Session, user: User, now: datetime) -> int:
    tz = ZoneInfo(user.timezone)
    midnight = datetime.combine(now.astimezone(tz).date(), time(0), tz)
    return session.scalar(select(func.count(Message.id)).where(
        Message.user_id == user.id, Message.direction == "out", Message.kind.in_(PROACTIVE_KINDS),
        Message.created_at >= midnight, Message.created_at < midnight + timedelta(days=1))) or 0


def proactive_allowed(session: Session, user: User, now: datetime, kind: str = "alert") -> bool:
    """Whether the assistant may text this student unprompted right now."""
    return (user.status == "active" and not (kind in QUIET_KINDS and in_quiet_hours(user, now))
            and proactive_sent_today(session, user, now) < DAILY_PROACTIVE_CAP)


def send_text(session: Session, user: User, body: str, *, kind: str = "reply", dedupe_key: str | None = None,
              now: datetime | None = None) -> Message | None:
    """Queue (and, with a transport, deliver) one text. None when it must not be sent."""
    now = now or utcnow()
    if user.status == "opted_out":
        return None
    if dedupe_key and session.scalar(select(Message.id).where(Message.dedupe_key == dedupe_key)):
        return None
    if kind in PROACTIVE_KINDS and not proactive_allowed(session, user, now, kind):
        return None
    msg = Message(user_id=user.id, phone=user.phone, direction="out", body=body, status="queued", kind=kind,
                  dedupe_key=dedupe_key, created_at=now)
    session.add(msg)
    session.flush()
    if _transport is not None:
        try:
            msg.provider_sid = _transport(user.phone, body)
            msg.status = "sent"
        except Exception as exc:  # delivery failure must not lose the conversation state
            msg.status = "failed"
            session.flush()
            raise RuntimeError(f"text delivery failed: {exc}") from None
    session.flush()
    return msg


def record_inbound(session: Session, user: User | None, phone: str, body: str, provider_sid: str | None) -> Message:
    msg = Message(user_id=user.id if user else None, phone=phone, direction="in", body=body,
                  provider_sid=provider_sid, status="received", created_at=utcnow())
    session.add(msg)
    session.flush()
    return msg


def already_received(session: Session, provider_sid: str | None) -> bool:
    """Twilio retries webhooks; the same MessageSid must only be handled once."""
    return bool(provider_sid) and session.scalar(select(Message.id).where(Message.provider_sid == provider_sid)) is not None


def take_queued(session: Session, phone: str) -> list[Message]:
    """For the local fake phone: queued texts to this number, oldest first, marked as sent."""
    rows = list(session.scalars(select(Message).where(
        Message.phone == phone, Message.direction == "out", Message.status == "queued").order_by(Message.created_at)))
    for row in rows:
        row.status = "sent"
    session.flush()
    return rows
