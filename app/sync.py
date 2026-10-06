"""Sync one Brightspace connection into a student's academic_items.

fetch -> parse -> normalize -> upsert by external_source_id (fingerprint as
a fallback when a UID is re-issued) -> record what changed. Student-owned
state (done marks, estimates) is never touched. Items missing from the
feed are hidden only after two consecutive successful syncs, and a feed
that suddenly comes back empty is treated as an error, not a mass delete.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .connectors.brightspace.fetch import FetchError, fetch_feed
from .connectors.brightspace.institutions import InstitutionConfig, get_institution
from .connectors.brightspace.normalize import NormalizedItem, normalize_events
from .connectors.brightspace.parse import parse_ics
from .db import utcnow
from .models import AcademicItem, Course, LmsConnection, SyncRun
from .security import decrypt

Fetcher = Callable[[str, InstitutionConfig], bytes]
FAILURES_BEFORE_ERROR = 3
EMPTY_FEED_GUARD = 5  # previously had more than this many items, now zero -> treat as a broken feed

SYNCED_FIELDS = ("fingerprint", "lms_event_id", "institution", "course_code", "course_label", "course_name", "title",
                 "raw_title", "type", "category", "org_kind", "due_at", "start_at", "end_at", "available_at",
                 "available_until", "all_day", "cancelled", "in_term", "source_url")


@dataclass
class SyncResult:
    ok: bool
    error: str | None = None
    events: int = 0
    items: int = 0
    actionable: int = 0
    created: list[AcademicItem] = field(default_factory=list)   # new actionable items (not on the first sync)
    moved: list[tuple[AcademicItem, datetime | None]] = field(default_factory=list)  # (item, old time)
    hidden: int = 0
    courses: list[Course] = field(default_factory=list)


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _fields(n: NormalizedItem) -> dict:
    return {
        "fingerprint": n.fingerprint, "lms_event_id": n.lms_event_id, "institution": n.institution,
        "course_code": n.course_code, "course_label": n.course_label, "course_name": n.course_name,
        "title": n.title[:300], "raw_title": n.raw_event_title[:300], "type": n.type, "category": n.category,
        "org_kind": n.org_kind, "due_at": _ts(n.due_at), "start_at": _ts(n.start_at), "end_at": _ts(n.end_at),
        "available_at": _ts(n.available_at), "available_until": _ts(n.available_until), "all_day": n.all_day,
        "cancelled": n.cancelled, "in_term": n.in_term, "source_url": n.source_url,
    }


def run_sync(session: Session, connection: LmsConnection, *, trigger: str = "scheduled",
             fetcher: Fetcher = fetch_feed, now: datetime | None = None) -> SyncResult:
    now = now or utcnow()
    inst = get_institution(connection.institution)
    run = SyncRun(user_id=connection.user_id, connection_id=connection.id, trigger=trigger, started_at=now)
    session.add(run)
    connection.last_sync_at = now

    existing = list(session.scalars(select(AcademicItem).where(
        AcademicItem.user_id == connection.user_id, AcademicItem.connection_id == connection.id)))
    try:
        body = fetcher(decrypt(connection.encrypted_secret), inst)
        events = parse_ics(body).events
        if not events and len(existing) > EMPTY_FEED_GUARD:
            raise FetchError("feed came back empty although it had items before; treating it as a broken feed")
    except (FetchError, ValueError) as exc:
        return _fail(connection, run, str(exc), now)

    normalized = normalize_events(events, inst)
    result = SyncResult(ok=True, events=len(events), items=len(normalized))
    first_sync = not existing
    by_id = {row.external_source_id: row for row in existing}
    unmatched: list[NormalizedItem] = []

    def apply(row: AcademicItem | None, n: NormalizedItem) -> None:
        values = _fields(n)
        if row is None:
            row = AcademicItem(user_id=connection.user_id, connection_id=connection.id, source="brightspace_calendar",
                               external_source_id=n.external_source_id, first_seen_at=now, last_seen_at=now, **values)
            session.add(row)
            if not first_sync and row.actionable:
                result.created.append(row)
            return
        old_when, was_actionable = row.when, row.actionable
        for key, value in values.items():
            setattr(row, key, value)
        row.external_source_id = n.external_source_id
        row.last_seen_at, row.missing_since, row.missed_syncs = now, None, 0
        if was_actionable and row.actionable and row.when != old_when:
            result.moved.append((row, old_when))

    for n in normalized:
        row = by_id.pop(n.external_source_id, None)
        if row is None:
            unmatched.append(n)
        else:
            apply(row, n)
    leftovers = {row.fingerprint: row for row in by_id.values() if row.fingerprint}
    for n in unmatched:  # same activity re-issued under a new UID
        row = leftovers.pop(n.fingerprint, None)
        if row is not None:
            by_id.pop(row.external_source_id, None)
        apply(row, n)
    for row in by_id.values():
        row.missing_since = row.missing_since or now
        row.missed_syncs = (row.missed_syncs or 0) + 1
        if row.missed_syncs == 2:
            result.hidden += 1

    session.flush()
    result.courses = _upsert_courses(session, connection, normalized)
    result.actionable = sum(1 for row in session.scalars(select(AcademicItem).where(
        AcademicItem.user_id == connection.user_id)) if row.actionable)

    connection.status, connection.last_success_at = "active", now
    connection.consecutive_failures, connection.last_error = 0, None
    run.ok, run.finished_at = True, now
    run.stats = {"events": result.events, "items": result.items, "actionable": result.actionable,
                 "created": len(result.created), "moved": len(result.moved), "hidden": result.hidden}
    session.flush()
    return result


def _fail(connection: LmsConnection, run: SyncRun, message: str, now: datetime) -> SyncResult:
    connection.consecutive_failures = (connection.consecutive_failures or 0) + 1
    connection.last_error = message[:500]
    if connection.consecutive_failures >= FAILURES_BEFORE_ERROR:
        connection.status = "error"
    run.ok, run.finished_at, run.error = False, now, message[:500]
    return SyncResult(ok=False, error=message)


def _upsert_courses(session: Session, connection: LmsConnection, normalized: list[NormalizedItem]) -> list[Course]:
    """Every course in the feed this term, including ones that publish no deadlines."""
    by_label: dict[str, dict] = {}
    for n in normalized:
        if n.org_kind != "course" or not n.course_label:
            continue
        info = by_label.setdefault(n.course_label, {"code": n.course_code, "name": n.course_name, "current": False})
        info["name"] = info["name"] or n.course_name
        if n.in_term is not False:
            info["current"] = True
    actionable_labels = {row.course_label for row in session.scalars(select(AcademicItem).where(
        AcademicItem.user_id == connection.user_id)) if row.actionable}
    rows = {c.label: c for c in session.scalars(select(Course).where(Course.user_id == connection.user_id))}
    out = []
    for label, info in by_label.items():
        course = rows.get(label) or Course(user_id=connection.user_id, institution=connection.institution, label=label)
        course.code, course.name = info["code"], info["name"]
        course.has_calendar_items = label in actionable_labels
        session.add(course)
        out.append(course)
    session.flush()
    return out
