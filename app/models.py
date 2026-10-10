"""Tables for the MVP (a subset of docs/architecture.md section 3).

Every student-owned row carries user_id, and every query in the app is
scoped by it. The phone number is the student's identity in the text
channel; high-risk actions will get extra confirmation later.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, utcnow


def _id() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    phone: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # E.164, e.g. +15195550187
    status: Mapped[str] = mapped_column(String(20), default="onboarding")  # onboarding | active | opted_out
    onboarding_step: Mapped[str] = mapped_column(String(30), default="new")
    # new -> awaiting_consent -> awaiting_school -> awaiting_connection -> done
    institution: Mapped[str | None] = mapped_column(String(40))
    timezone: Mapped[str] = mapped_column(String(40), default="America/Toronto")
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    terms_version: Mapped[str | None] = mapped_column(String(40))
    opted_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # texts the assistant sends on its own (the student changes these by text)
    briefing_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    briefing_time: Mapped[str] = mapped_column(String(5), default="08:00")   # local HH:MM
    last_briefing_date: Mapped[date | None] = mapped_column(Date)            # local date of the last one sent
    due_reminder_minutes: Mapped[int | None] = mapped_column(Integer, default=180)  # None = off
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)      # new / moved deadlines
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    connections: Mapped[list[LmsConnection]] = relationship(back_populates="user", cascade="all, delete-orphan")


class LmsConnection(Base):
    __tablename__ = "lms_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    institution: Mapped[str] = mapped_column(String(40))
    method: Mapped[str] = mapped_column(String(30), default="brightspace_calendar")
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | error | revoked
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)  # the calendar feed URL (contains a token)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    user: Mapped[User] = relationship(back_populates="connections")


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (UniqueConstraint("user_id", "label"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    institution: Mapped[str] = mapped_column(String(40))
    code: Mapped[str | None] = mapped_column(String(20))   # CS115
    label: Mapped[str] = mapped_column(String(200))         # "CS 115 - Fall 2026"
    name: Mapped[str | None] = mapped_column(String(200))
    has_calendar_items: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class AcademicItem(Base):
    __tablename__ = "academic_items"
    __table_args__ = (UniqueConstraint("user_id", "external_source_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("lms_connections.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(30), default="brightspace_calendar")
    external_source_id: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    lms_event_id: Mapped[str | None] = mapped_column(String(40))
    institution: Mapped[str] = mapped_column(String(40))
    course_code: Mapped[str | None] = mapped_column(String(20))
    course_label: Mapped[str | None] = mapped_column(String(200))
    course_name: Mapped[str | None] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    raw_title: Mapped[str | None] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(20))           # assignment | quiz | exam | midterm | ...
    category: Mapped[str] = mapped_column(String(20))       # deliverable | exam | content | checklist | event | non_course
    org_kind: Mapped[str] = mapped_column(String(20), default="course")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    available_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    available_until: Mapped[datetime | None] = mapped_column(UTCDateTime())
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)
    in_term: Mapped[bool | None] = mapped_column(Boolean)
    source_url: Mapped[str | None] = mapped_column(Text)
    # student-owned state: never overwritten by a sync
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    estimate_minutes: Mapped[int | None] = mapped_column(Integer)
    # sync bookkeeping
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    missing_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    missed_syncs: Mapped[int] = mapped_column(Integer, default=0)  # hidden after 2 consecutive misses
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    @property
    def when(self) -> datetime | None:
        return self.due_at or self.start_at

    @property
    def actionable(self) -> bool:
        return (self.org_kind == "course" and self.category in ("deliverable", "exam") and self.in_term is not False
                and not self.cancelled and (self.missed_syncs or 0) < 2 and self.when is not None)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    body: Mapped[str] = mapped_column(Text)
    provider_sid: Mapped[str | None] = mapped_column(String(64), unique=True)  # Twilio MessageSid (dedupe)
    status: Mapped[str] = mapped_column(String(20), default="received")
    # received | queued | sent | delivered | failed
    kind: Mapped[str] = mapped_column(String(20), default="reply")
    # reply | briefing | due_soon | alert | reminder | system
    dedupe_key: Mapped[str | None] = mapped_column(String(160), unique=True)  # one text per key, ever
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class Reminder(Base):
    """A reminder the student asked for ("remind me at 8 to start the essay")."""

    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[str | None] = mapped_column(ForeignKey("academic_items.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(String(300))
    fire_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | sent | cancelled
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ConversationTurn(Base):
    """One student message plus the tool traffic and reply it produced (the agent's memory)."""

    __tablename__ = "conversation_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    messages: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)


class SetupToken(Base):
    """One-time link (connect page). Only the SHA-256 of the token is stored."""

    __tablename__ = "setup_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(30))  # connect_calendar | delete_account
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("lms_connections.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(20))  # initial | manual | scheduled
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    ok: Mapped[bool | None] = mapped_column(Boolean)
    stats: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
