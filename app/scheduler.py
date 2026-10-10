"""Texts the assistant sends on its own, plus background syncs.

`tick(session, now)` runs every minute (a thread in the web process, see
`start`). Each job is plain, deterministic Python with no LLM:

- background sync of every connection every few hours, then one batched
  text about new or moved deadlines
- reminders the student asked for ("remind me at 8 to start the essay")
- due-soon reminders N hours before each unfinished deadline
- the morning briefing at the student's chosen time

Every text carries a dedupe key, so running a tick twice (or overlapping
ticks) can never send the same text twice. Quiet hours and the daily cap
live in messaging.send_text.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai.timeutil import fmt_when
from .connectors.brightspace import fetch as fetch_module
from .connectors.brightspace.institutions import get_institution
from .db import utcnow
from .messaging import in_quiet_hours, send_text
from .models import AcademicItem, LmsConnection, Reminder, User
from .sync import FAILURES_BEFORE_ERROR, SyncResult, run_sync

log = logging.getLogger(__name__)

SYNC_EVERY = timedelta(hours=3)
BROKEN_SYNC_EVERY = timedelta(hours=24)   # keep retrying a broken feed, gently
BRIEFING_WINDOW = timedelta(hours=2)      # never send a "morning" text hours late after downtime
BRIEFING_DAYS_AHEAD = 2
MAX_LINES = 6


# ------------------------------------------------------------------ helpers

def _tz(user: User) -> ZoneInfo:
    return ZoneInfo(user.timezone)


def _active_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).where(User.status == "active", User.onboarding_step == "done")))


def _open_items(session: Session, user: User) -> list[AcademicItem]:
    rows = session.scalars(select(AcademicItem).where(AcademicItem.user_id == user.id,
                                                      AcademicItem.completed_at.is_(None)))
    return sorted((r for r in rows if r.actionable), key=lambda r: r.when)


def _name(item: AcademicItem) -> str:
    course = item.course_code or item.course_label
    return f"{course} {item.title}" if course else item.title


def _time_only(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    return f"{local.hour % 12 or 12}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def _bullets(lines: list[str], more_hint: str) -> list[str]:
    if len(lines) <= MAX_LINES:
        return lines
    return lines[:MAX_LINES - 1] + [f"• +{len(lines) - MAX_LINES + 1} more ({more_hint})"]


# ------------------------------------------------------------------ morning briefing

def briefing_text(session: Session, user: User, now: datetime) -> str:
    tz = _tz(user)
    today = now.astimezone(tz).date()
    items = _open_items(session, user)
    overdue = [i for i in items if i.due_at and now - timedelta(days=3) <= i.due_at < now]
    soon = [i for i in items if i.when >= now and i.when.astimezone(tz).date() <= today + timedelta(BRIEFING_DAYS_AHEAD)]
    todays = [i for i in soon if i.when.astimezone(tz).date() == today]
    later = [i for i in soon if i not in todays]

    def verb(i: AcademicItem) -> str:
        return "due" if i.due_at else "starts"

    if not (todays or later or overdue):
        return ("Good morning! Nothing due today or in the next 2 days. "
                "Text me \"what's due this week\" to look further ahead.")
    parts = ["Good morning! Here's your day."]
    if todays:
        parts.append("Today:\n" + "\n".join(_bullets(
            [f"• {_name(i)}, {verb(i)} {_time_only(i.when, tz) if not i.all_day else 'today'}" for i in todays],
            "text \"what's due today\"")))
    if later:
        parts.append("Coming up:\n" + "\n".join(_bullets(
            [f"• {_name(i)}, {verb(i)} {fmt_when(i.when, tz, i.all_day)}" for i in later],
            "text \"what's due this week\"")))
    if overdue:
        parts.append("Still open (past due):\n" + "\n".join(_bullets(
            [f"• {_name(i)}, was due {fmt_when(i.due_at, tz, i.all_day)}" for i in overdue],
            "text \"what's overdue\"")))
    parts.append("Text \"done with ...\" when you finish something.")
    return "\n\n".join(parts)


def _briefing_due(user: User, now: datetime) -> date | None:
    """The local date to brief for, if it's briefing time and today's hasn't gone out."""
    if not user.briefing_enabled:
        return None
    tz = _tz(user)
    local = now.astimezone(tz)
    hour, minute = (int(x) for x in (user.briefing_time or "08:00").split(":"))
    target = datetime.combine(local.date(), time(hour, minute), tz)
    if not target <= now < target + BRIEFING_WINDOW or user.last_briefing_date == local.date():
        return None
    return local.date()


def send_briefings(session: Session, now: datetime) -> int:
    sent = 0
    for user in _active_users(session):
        day = _briefing_due(user, now)
        if day is None:
            continue
        msg = send_text(session, user, briefing_text(session, user, now), kind="briefing",
                        dedupe_key=f"briefing:{user.id}:{day.isoformat()}", now=now)
        if msg:
            user.last_briefing_date = day
            sent += 1
    return sent


# ------------------------------------------------------------------ due-soon reminders

def send_due_soon(session: Session, now: datetime) -> int:
    """One text per student per due time, `due_reminder_minutes` before it.

    The dedupe key includes the due time, so a deadline that moves re-arms
    its reminder by itself, and finished items never get one.
    """
    sent = 0
    for user in _active_users(session):
        if not user.due_reminder_minutes:
            continue
        offset = timedelta(minutes=user.due_reminder_minutes)
        by_time: dict[datetime, list[AcademicItem]] = defaultdict(list)
        for item in _open_items(session, user):
            if item.when - offset <= now < item.when:
                by_time[item.when].append(item)
        tz = _tz(user)
        for when, items in sorted(by_time.items()):
            lines = [f"{_name(i)} {'is due' if i.due_at else 'starts'} at {_time_only(when, tz)}" for i in items]
            body = "Heads up: " + "; ".join(lines) + (". " if len(lines) == 1 else ". ")
            body += "Reply \"done\" if you've already finished." if any(i.due_at for i in items) else "Good luck!"
            key = f"due:{user.id}:{user.due_reminder_minutes}:{when.isoformat()}"
            if send_text(session, user, body, kind="due_soon", dedupe_key=key, now=now):
                sent += 1
    return sent


# ------------------------------------------------------------------ reminders the student asked for

def reminder_text(session: Session, reminder: Reminder, user: User) -> str:
    item = session.get(AcademicItem, reminder.item_id) if reminder.item_id else None
    note = (reminder.note or "").strip()
    if item is None:
        return f"Reminder: {note}" if note else "Reminder!"
    verb = "is due" if item.due_at else "starts"
    about = f"{_name(item)} {verb} {fmt_when(item.when, _tz(user), item.all_day)}."
    return f"Reminder: {note}. {about}" if note else f"Reminder: {about}"


def send_custom_reminders(session: Session, now: datetime) -> int:
    sent = 0
    due = session.scalars(select(Reminder).where(Reminder.status == "active", Reminder.fire_at <= now)
                          .order_by(Reminder.fire_at))
    for reminder in list(due):
        user = session.get(User, reminder.user_id)
        item = session.get(AcademicItem, reminder.item_id) if reminder.item_id else None
        if item is not None and item.completed_at is not None:
            reminder.status = "cancelled"  # finished already: nothing to remind about
            continue
        if send_text(session, user, reminder_text(session, reminder, user), kind="reminder",
                     dedupe_key=f"reminder:{reminder.id}", now=now):
            sent += 1
        reminder.status = "sent"
    return sent


# ------------------------------------------------------------------ background sync + change alerts

def alert_text(user: User, result: SyncResult, lms_name: str) -> str:
    tz = _tz(user)
    lines = [f"• New: {_name(i)}, {'due' if i.due_at else 'on'} {fmt_when(i.when, tz, i.all_day)}"
             for i in sorted(result.created, key=lambda i: i.when)]
    for item, old in sorted(result.moved, key=lambda pair: pair[0].when):
        was = f" (was {fmt_when(old, tz, item.all_day)})" if old else ""
        lines.append(f"• Moved: {_name(item)}, now {fmt_when(item.when, tz, item.all_day)}{was}")
    return f"Update from {lms_name}:\n" + "\n".join(_bullets(lines, "text \"what's due this week\""))


def broken_feed_text(lms_name: str) -> str:
    return (f"Heads up: I can't read your {lms_name} calendar anymore (the link may have been reset), "
            f"so I might miss new deadlines. Reply LINK and I'll send you a link to reconnect.")


def sync_due_connections(session: Session, now: datetime, *, fetcher: Callable | None = None) -> int:
    synced = 0
    conns = session.scalars(select(LmsConnection).where(LmsConnection.status.in_(("active", "error"))))
    for conn in list(conns):
        user = session.get(User, conn.user_id)
        if user.status != "active" or user.onboarding_step != "done" or in_quiet_hours(user, now):
            continue  # syncing overnight would surface alerts nobody may send until morning
        every = BROKEN_SYNC_EVERY if conn.status == "error" else SYNC_EVERY
        if conn.last_sync_at and now - conn.last_sync_at < every:
            continue
        result = run_sync(session, conn, trigger="scheduled", fetcher=fetcher or fetch_module.fetch_feed, now=now)
        synced += 1
        lms_name = get_institution(conn.institution).lms_name
        if result.ok and user.alerts_enabled and (result.created or result.moved):
            send_text(session, user, alert_text(user, result, lms_name), kind="alert",
                      dedupe_key=f"alert:{conn.id}:{now.isoformat()}", now=now)
        elif not result.ok and conn.consecutive_failures == FAILURES_BEFORE_ERROR:
            send_text(session, user, broken_feed_text(lms_name), kind="alert",
                      dedupe_key=f"broken:{conn.id}", now=now)
    return synced


# ------------------------------------------------------------------ the tick

JOBS = (("sync", sync_due_connections), ("reminders", send_custom_reminders),
        ("due_soon", send_due_soon), ("briefings", send_briefings))


def tick(session: Session, now: datetime | None = None, **kw) -> dict[str, int]:
    """Run every job once. Sync goes first so alerts and briefings see fresh data."""
    now = now or utcnow()
    counts = {}
    for name, job in JOBS:
        try:
            counts[name] = job(session, now, **kw) if name == "sync" else job(session, now)
            session.flush()
        except Exception:  # one broken job must not stop the others
            log.exception("scheduler job %s failed", name)
            counts[name] = -1
    return counts


def start(interval_seconds: float = 60) -> threading.Event:
    """Tick in a background thread until the returned event is set."""
    from .db import session_scope

    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                with session_scope() as session:
                    tick(session)
            except Exception:
                log.exception("scheduler tick failed")
            stop.wait(interval_seconds)

    threading.Thread(target=loop, name="scheduler", daemon=True).start()
    return stop
