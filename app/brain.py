"""Answer a connected student's text with the LLM + tools, remembering recent turns."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai.agent import Agent
from .ai.llm import ChatModel, LLMError, model_from_settings
from .ai.store import Store
from .ai.tools import Toolbox
from .config import get_settings
from .db import utcnow
from .models import ConversationTurn, LmsConnection, Message, User
from .sync import run_sync

log = logging.getLogger(__name__)
HISTORY_TURNS = 6
RECENT_PROACTIVE = timedelta(hours=24)
NOT_CONFIGURED = "I'm connected to your courses, but my brain isn't switched on yet (no AI key set up). Try again soon!"
LLM_DOWN = "Sorry, I'm having trouble thinking right now. Try again in a minute."

_model_override: ChatModel | None = None


def set_model(model: ChatModel | None) -> None:
    """Tests and the fake phone can inject a model."""
    global _model_override
    _model_override = model


@lru_cache
def _configured_model() -> ChatModel | None:
    return model_from_settings(get_settings())


def current_model() -> ChatModel | None:
    return _model_override or _configured_model()


def _refresh(session: Session, user: User):
    def refresh() -> str:
        conn = session.scalar(select(LmsConnection).where(
            LmsConnection.user_id == user.id, LmsConnection.status != "revoked").order_by(LmsConnection.created_at.desc()))
        if conn is None:
            return "no calendar connected"
        result = run_sync(session, conn, trigger="manual")
        if not result.ok:
            return f"sync failed: {result.error}"
        return f"synced: {len(result.created)} new, {len(result.moved)} moved, {result.hidden} removed"
    return refresh


def _recent_proactive(session: Session, user: User, now: datetime, since: datetime | None) -> str | None:
    """Texts the assistant sent on its own since the last conversation turn (a reply like "done"
    or "remind me later about that" usually refers to one of them)."""
    start = max(filter(None, (since, now - RECENT_PROACTIVE)))
    rows = list(session.scalars(select(Message).where(
        Message.user_id == user.id, Message.direction == "out", Message.created_at > start,
        Message.kind.in_(("briefing", "due_soon", "alert", "reminder"))).order_by(Message.created_at)))[-4:]
    if not rows:
        return None
    sent = "\n---\n".join(m.body for m in rows)
    return ("Texts you sent the student on your own since their last message (they may be replying to "
            "these; use tools to find the items before acting):\n" + sent)


def answer(session: Session, user: User, text: str, *, now: datetime | None = None) -> str:
    model = current_model()
    if model is None:
        return NOT_CONFIGURED
    fixed_now = now
    now_fn = (lambda: fixed_now) if fixed_now else utcnow
    tz = ZoneInfo(user.timezone or get_settings().default_timezone)

    store = Store(session, user.id)
    store.load()
    toolbox = Toolbox(store, tz, now_fn=now_fn, refresh_fn=_refresh(session, user))
    past = list(session.scalars(select(ConversationTurn).where(ConversationTurn.user_id == user.id)
                                .order_by(ConversationTurn.created_at.desc()).limit(HISTORY_TURNS)))
    context = _recent_proactive(session, user, now_fn(), past[0].created_at if past else None)
    agent = Agent(model, toolbox, tz, now_fn=now_fn, history=[t.messages for t in reversed(past)], context=context)
    try:
        reply = agent.reply(text)
    except LLMError as exc:
        log.warning("LLM error for user %s: %s", user.id, exc)
        return LLM_DOWN
    session.add(ConversationTurn(user_id=user.id, messages=agent.turns[-1], created_at=now_fn()))
    session.flush()
    return reply
