"""The student's data as the tools see it, backed by the database.

Same interface as the prototype's JSON store, so the tools and agent are
reused unchanged, but scoped to one user. The toolbox can only ever
reach this user's rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AcademicItem, Course, LmsConnection, Reminder, User


@dataclass
class Item:
    id: str
    institution: str
    course: str
    course_name: str | None
    title: str
    type: str
    due: datetime | None
    start: datetime | None
    all_day: bool
    source_tool: str | None = None
    completed: bool = False
    estimate_minutes: int | None = None

    @property
    def when(self) -> datetime | None:
        return self.due or self.start


class Store:
    def __init__(self, session: Session, user_id: str):
        self.session = session
        self.user_id = user_id
        self.items: list[Item] = []
        self.quiet_courses: list[tuple[str, str]] = []
        self.data_as_of: datetime | None = None
        self.last_sync_at: datetime | None = None  # last attempt, successful or not

    def load(self) -> None:
        rows = self.session.scalars(select(AcademicItem).where(AcademicItem.user_id == self.user_id))
        self.items = sorted(
            (self._item(r) for r in rows if r.actionable),
            key=lambda i: i.when or datetime.max.replace(tzinfo=timezone.utc),
        )
        courses = self.session.scalars(select(Course).where(Course.user_id == self.user_id))
        self.quiet_courses = sorted((c.institution, c.code or c.label) for c in courses if not c.has_calendar_items)
        self.data_as_of = self.session.scalar(
            select(func.max(LmsConnection.last_success_at)).where(LmsConnection.user_id == self.user_id))
        self.last_sync_at = self.session.scalar(
            select(func.max(LmsConnection.last_sync_at)).where(LmsConnection.user_id == self.user_id))

    @staticmethod
    def _item(r: AcademicItem) -> Item:
        return Item(id=r.id, institution=r.institution, course=r.course_code or r.course_label or "?",
                    course_name=r.course_name, title=r.title, type=r.type, due=r.due_at, start=r.start_at,
                    all_day=r.all_day, completed=r.completed_at is not None, estimate_minutes=r.estimate_minutes)

    def get(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def courses(self) -> list[tuple[str, str, str | None]]:
        seen: dict[tuple[str, str], str | None] = {}
        for i in self.items:
            key = (i.institution, i.course)
            seen[key] = seen.get(key) or i.course_name
        return sorted((inst, code, name) for (inst, code), name in seen.items())

    def _row(self, item_id: str) -> AcademicItem:
        row = self.session.get(AcademicItem, item_id)
        if row is None or row.user_id != self.user_id:
            raise KeyError(item_id)
        return row

    def set_completed(self, item_id: str, done: bool, now: datetime) -> Item:
        row = self._row(item_id)
        row.completed_at = now if done else None
        if done:  # nothing left to remind about
            for reminder in self.active_reminders():
                if reminder.item_id == item_id:
                    reminder.status = "cancelled"
        self.session.flush()
        item = self.get(item_id)
        if item:
            item.completed = done
        return item or self._item(row)

    def set_estimate(self, item_id: str, minutes: int) -> Item:
        row = self._row(item_id)
        row.estimate_minutes = minutes
        self.session.flush()
        item = self.get(item_id)
        if item:
            item.estimate_minutes = minutes
        return item or self._item(row)

    # ------------------------------------------------------------ reminders and settings

    @property
    def user(self) -> User:
        return self.session.get(User, self.user_id)

    def active_reminders(self) -> list[Reminder]:
        return list(self.session.scalars(select(Reminder).where(
            Reminder.user_id == self.user_id, Reminder.status == "active").order_by(Reminder.fire_at)))

    def add_reminder(self, fire_at: datetime, item_id: str | None, note: str | None, now: datetime) -> Reminder:
        if item_id is not None:
            self._row(item_id)  # must be this student's item
        reminder = Reminder(user_id=self.user_id, item_id=item_id, note=note, fire_at=fire_at, created_at=now)
        self.session.add(reminder)
        self.session.flush()
        return reminder

    def cancel_reminder(self, reminder_id: str) -> None:
        reminder = self.session.get(Reminder, reminder_id)
        if reminder is None or reminder.user_id != self.user_id:
            raise KeyError(reminder_id)
        reminder.status = "cancelled"
        self.session.flush()

    def update_settings(self, **changes) -> User:
        user = self.user
        for key, value in changes.items():
            setattr(user, key, value)
        self.session.flush()
        return user
