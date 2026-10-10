"""Connect a student's Brightspace calendar (used by the connect page and by pasting the link in a text)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai.timeutil import fmt_when
from .connectors.brightspace import fetch as fetch_module
from .connectors.brightspace.fetch import validate_feed_url
from .connectors.brightspace.institutions import get_institution
from .db import utcnow
from .messaging import send_text
from .models import AcademicItem, Course, LmsConnection, User
from .security import encrypt
from .sync import SyncResult, run_sync


@dataclass
class ConnectOutcome:
    ok: bool
    problem: str | None = None   # student-facing explanation when not ok
    summary: str | None = None   # the "Connected!" text that was sent


def connect_calendar(session: Session, user: User, url: str, *, fetcher=None,
                     now: datetime | None = None) -> ConnectOutcome:
    now = now or utcnow()
    if not user.institution:
        return ConnectOutcome(False, "Tell me your school first, then I'll send the connect link.")
    inst = get_institution(user.institution)
    problem = validate_feed_url(url, inst)
    if problem:
        return ConnectOutcome(False, problem)

    for old in user.connections:
        if old.method == "brightspace_calendar" and old.status != "revoked":
            old.status = "revoked"
    conn = LmsConnection(user_id=user.id, institution=inst.slug, encrypted_secret=encrypt(url.strip()), created_at=now)
    session.add(conn)
    session.flush()

    result = run_sync(session, conn, trigger="initial", fetcher=fetcher or fetch_module.fetch_feed, now=now)
    if not result.ok:
        conn.status = "error"
        return ConnectOutcome(False, f"I couldn't read that calendar. The link may have been reset, or calendar "
                                     f"feeds may be off. Copy a fresh Subscribe link from {inst.lms_name} and try again.")

    user.onboarding_step = "done"
    if user.status != "opted_out":
        user.status = "active"
    summary = connected_message(session, user, result, now)
    send_text(session, user, summary)
    return ConnectOutcome(True, summary=summary)


def connected_message(session: Session, user: User, result: SyncResult, now: datetime) -> str:
    inst = get_institution(user.institution)
    tz = ZoneInfo(user.timezone or inst.timezone)
    items = [i for i in session.scalars(select(AcademicItem).where(AcademicItem.user_id == user.id)) if i.actionable]
    upcoming = sorted((i for i in items if i.when >= now and i.completed_at is None), key=lambda i: i.when)
    courses = list(session.scalars(select(Course).where(Course.user_id == user.id)))
    quiet = sorted(c.code or c.label for c in courses if not c.has_calendar_items)

    lines = [f"Connected! I found {len(courses)} course{'s' if len(courses) != 1 else ''} and "
             f"{len(upcoming)} upcoming deadline{'s' if len(upcoming) != 1 else ''}."]
    if upcoming:
        nxt = upcoming[0]
        lines.append(f"Next up: {nxt.course_code or nxt.course_label} {nxt.title}, {fmt_when(nxt.when, tz, nxt.all_day)}.")
    if quiet:
        names = ", ".join(quiet[:-1]) + (" and " if len(quiet) > 1 else "") + quiet[-1]
        verb = "don't" if len(quiet) > 1 else "doesn't"
        lines.append(f"Heads up: {names} {verb} post deadlines to {inst.lms_name}, so I can't see "
                     f"{'their' if len(quiet) > 1 else 'its'} due dates yet.")
    lines.append("Each morning at 8 I'll text you what's due, plus a heads-up 3 hours before each deadline. "
                 "Reply anytime to change that.")
    lines.append('Text me anything, like "what\'s due this week" or "remind me at 8 to start the essay".')
    return "\n\n".join(lines)
