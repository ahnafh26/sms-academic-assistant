"""Academic data the assistant is allowed to talk about.

Source of truth: the spike's normalized items (all_items.json, refreshed
daily by the scheduled task). Only real course deliverables and exams in
the current term are exposed: noise (maintenance windows, residence
events, content pages) and stale copied-course dates never reach the LLM.

Student-owned state (completion marks, time estimates) lives in a local
JSON file keyed by the item's stable external_source_id, so it survives
re-syncs and is never overwritten by the feed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ACTIONABLE_CATEGORIES = {"deliverable", "exam"}


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
    source_tool: str | None
    completed: bool = False
    estimate_minutes: int | None = None

    @property
    def when(self) -> datetime | None:
        return self.due or self.start


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def is_actionable(raw: dict) -> bool:
    return (
        raw.get("org_kind") == "course"
        and raw.get("category") in ACTIONABLE_CATEGORIES
        and raw.get("in_term") is not False
        and not raw.get("cancelled")
        and bool(raw.get("due_at") or raw.get("start_at"))
    )


class Store:
    def __init__(self, items_path: Path, state_path: Path):
        self.items_path = items_path
        self.state_path = state_path
        self.items: list[Item] = []
        self.data_as_of: datetime | None = None
        self._state: dict = {"completed": {}, "estimates": {}}

    def load(self) -> None:
        if not self.items_path.exists():
            raise FileNotFoundError(
                f"{self.items_path} not found. Run the spike first: "
                "cd spikes/brightspace_ical && .venv\\Scripts\\python -m spike run-all"
            )
        raw_items = json.loads(self.items_path.read_text(encoding="utf-8"))
        self.data_as_of = datetime.fromtimestamp(self.items_path.stat().st_mtime, tz=timezone.utc)
        if self.state_path.exists():
            self._state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._state.setdefault("completed", {})
            self._state.setdefault("estimates", {})

        self.items = []
        for raw in raw_items:
            if not is_actionable(raw):
                continue
            item_id = raw["external_source_id"]
            self.items.append(
                Item(
                    id=item_id,
                    institution=raw["institution"],
                    course=raw.get("course_code") or raw.get("course_label") or "?",
                    course_name=raw.get("course_name"),
                    title=raw["title"],
                    type=raw["type"],
                    due=_parse(raw.get("due_at")),
                    start=_parse(raw.get("start_at")),
                    all_day=bool(raw.get("all_day")),
                    source_tool=raw.get("source_tool"),
                    completed=item_id in self._state["completed"],
                    estimate_minutes=self._state["estimates"].get(item_id),
                )
            )
        self.items.sort(key=lambda i: i.when or datetime.max.replace(tzinfo=timezone.utc))

    def get(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def courses(self) -> list[tuple[str, str, str | None]]:
        """(institution, course, course_name) for every course with at least one item."""
        seen: dict[tuple[str, str], str | None] = {}
        for i in self.items:
            key = (i.institution, i.course)
            seen[key] = seen.get(key) or i.course_name
        return sorted((inst, code, name) for (inst, code), name in seen.items())

    def set_completed(self, item_id: str, done: bool, now: datetime) -> Item:
        item = self._require(item_id)
        item.completed = done
        if done:
            self._state["completed"][item_id] = now.isoformat()
        else:
            self._state["completed"].pop(item_id, None)
        self._save()
        return item

    def set_estimate(self, item_id: str, minutes: int) -> Item:
        item = self._require(item_id)
        item.estimate_minutes = minutes
        self._state["estimates"][item_id] = minutes
        self._save()
        return item

    def _require(self, item_id: str) -> Item:
        item = self.get(item_id)
        if item is None:
            raise KeyError(item_id)
        return item

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
