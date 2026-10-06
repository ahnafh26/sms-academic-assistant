"""Outbound texts go through one outbox.

Every message to a student is first recorded as a `messages` row with
status "queued"; a transport then delivers it and marks it "sent". Locally,
the fake phone is the transport (it reads queued rows). With Twilio, the
webhook/worker will deliver through the Twilio API. Opted-out numbers
are never texted.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import utcnow
from .models import Message, User

Transport = Callable[[str, str], str | None]  # (phone, body) -> provider message id
_transport: Transport | None = None


def set_transport(transport: Transport | None) -> None:
    """Deliver immediately through this function (e.g. Twilio). None = leave queued for the fake phone."""
    global _transport
    _transport = transport


def send_text(session: Session, user: User, body: str) -> Message | None:
    if user.status == "opted_out":
        return None
    msg = Message(user_id=user.id, phone=user.phone, direction="out", body=body, status="queued", created_at=utcnow())
    session.add(msg)
    session.flush()
    if _transport is not None:
        try:
            msg.provider_sid = _transport(user.phone, body)
            msg.status = "sent"
        except Exception as exc:  # delivery failure must not lose the conversation state
            msg.status = "failed"
            msg.body = body
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
