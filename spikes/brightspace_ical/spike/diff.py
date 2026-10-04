"""Compare two snapshots of the same feed.

Two levels:
- raw events keyed by UID: are UIDs stable? when a deadline moves, does
  the UID survive? (Q10, Q11)
- normalized items keyed by external_source_id, falling back to the
  fingerprint: what would the product alert on? (Q15, change detection)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import NormalizedItem
from .parse import RawEvent
from .redact import redact

EVENT_FIELDS = ("summary", "dtstart", "dtend", "location", "description", "status", "sequence", "url")
SIGNIFICANT = {"created", "removed", "due_changed", "start_changed", "title_changed", "cancelled", "restored"}


@dataclass
class EventDiff:
    added: list[RawEvent] = field(default_factory=list)
    removed: list[RawEvent] = field(default_factory=list)
    changed: list[tuple[RawEvent, RawEvent, list[str]]] = field(default_factory=list)
    unchanged: int = 0
    without_uid: int = 0

    @property
    def uid_retention(self) -> float | None:
        """Share of old UIDs still present in the new snapshot."""
        old_total = len(self.removed) + len(self.changed) + self.unchanged
        return None if old_total == 0 else (len(self.changed) + self.unchanged) / old_total


@dataclass
class ItemChange:
    kind: str
    course_code: str | None
    title: str
    old: dict | None = None
    new: dict | None = None
    matched_by: str | None = None  # external_source_id | fingerprint

    @property
    def significant(self) -> bool:
        return self.kind in SIGNIFICANT


def diff_events(old: list[RawEvent], new: list[RawEvent]) -> EventDiff:
    result = EventDiff()
    old_by_uid = {e.uid: e for e in old if e.uid}
    new_by_uid = {e.uid: e for e in new if e.uid}
    result.without_uid = sum(1 for e in [*old, *new] if not e.uid)
    for uid, old_ev in old_by_uid.items():
        new_ev = new_by_uid.get(uid)
        if new_ev is None:
            result.removed.append(old_ev)
            continue
        fields = [f for f in EVENT_FIELDS if _event_value(old_ev, f) != _event_value(new_ev, f)]
        if fields:
            result.changed.append((old_ev, new_ev, fields))
        else:
            result.unchanged += 1
    result.added = [e for uid, e in new_by_uid.items() if uid not in old_by_uid]
    return result


def _event_value(ev: RawEvent, name: str):
    value = getattr(ev, name)
    if name in ("dtstart", "dtend") and value is not None:
        return (value.value, value.kind, value.tzid)
    return value


def diff_items(old: list[NormalizedItem], new: list[NormalizedItem]) -> list[ItemChange]:
    changes: list[ItemChange] = []
    new_by_id = {i.external_source_id: i for i in new}
    unmatched_old: list[NormalizedItem] = []
    matched_new: set[str] = set()

    for o in old:
        n = new_by_id.get(o.external_source_id)
        if n is None:
            unmatched_old.append(o)
            continue
        matched_new.add(n.external_source_id)
        changes += _field_changes(o, n, "external_source_id")

    remaining_new = {i.fingerprint: i for i in new if i.external_source_id not in matched_new}
    for o in unmatched_old:
        n = remaining_new.pop(o.fingerprint, None)
        if n is None:
            changes.append(ItemChange("removed", o.course_code, o.title, old=_brief(o)))
            continue
        matched_new.add(n.external_source_id)
        changes.append(ItemChange("id_changed", o.course_code, o.title, _brief(o), _brief(n), "fingerprint"))
        changes += _field_changes(o, n, "fingerprint")

    changes += [
        ItemChange("created", n.course_code, n.title, new=_brief(n))
        for n in new
        if n.external_source_id not in matched_new
    ]
    return changes


def _field_changes(o: NormalizedItem, n: NormalizedItem, matched_by: str) -> list[ItemChange]:
    out: list[ItemChange] = []

    def add(kind: str) -> None:
        out.append(ItemChange(kind, n.course_code, n.title, _brief(o), _brief(n), matched_by))

    if o.due_at != n.due_at:
        add("due_changed")
    if (o.start_at, o.end_at) != (n.start_at, n.end_at):
        add("start_changed")
    if o.title.casefold().split() != n.title.casefold().split():
        add("title_changed")
    if not o.cancelled and n.cancelled:
        add("cancelled")
    if o.cancelled and not n.cancelled:
        add("restored")
    if o.type != n.type:
        add("type_changed")
    if (o.available_at, o.available_until) != (n.available_at, n.available_until):
        add("availability_changed")
    if o.course_code != n.course_code:
        add("course_changed")
    return out


def _brief(i: NormalizedItem) -> dict:
    return {
        "course": i.course_code, "title": i.title, "type": i.type, "due_at_local": i.due_at_local,
        "start_at": i.start_at, "cancelled": i.cancelled, "uids": i.raw_event_uids,
    }


def render_markdown(
    events: EventDiff, items: list[ItemChange], *, old_label: str, new_label: str
) -> str:
    retention = events.uid_retention
    id_changed = [c for c in items if c.kind == "id_changed"]
    moved_same_uid = [
        (o, n) for o, n, fields in events.changed if {"dtstart", "dtend"} & set(fields)
    ]
    lines = [
        f"# Snapshot diff: `{old_label}` → `{new_label}`",
        "",
        "## Raw events by UID (Q10, Q11, Q15)",
        "",
        f"- UID retention: **{'n/a' if retention is None else f'{retention:.0%}'}** "
        f"(unchanged {events.unchanged}, changed {len(events.changed)}, removed {len(events.removed)}, "
        f"added {len(events.added)}, without UID {events.without_uid})",
        f"- events whose time changed but UID stayed the same: **{len(moved_same_uid)}**",
        f"- items matched only by fingerprint (UID changed): **{len(id_changed)}**",
        "",
    ]
    for old_ev, new_ev, fields in events.changed:
        lines.append(f"- changed `{old_ev.summary}`: {', '.join(fields)}")
        for f in fields:
            if f in ("summary", "dtstart", "dtend", "status", "sequence"):
                lines.append(f"  - {f}: {_event_value(old_ev, f)} → {_event_value(new_ev, f)}")
    lines += [f"- removed `{e.summary}`" for e in events.removed]
    lines += [f"- added `{e.summary}`" for e in events.added]

    lines += ["", "## Normalized item changes", "", "| significant | kind | course | title | old → new |", "|---|---|---|---|---|"]
    for c in sorted(items, key=lambda c: (not c.significant, c.kind)):
        delta = _delta(c)
        lines.append(f"| {'yes' if c.significant else 'no'} | {c.kind} | {c.course_code or '-'} | {c.title} | {delta} |")
    if not items:
        lines.append("| | (no changes) | | | |")
    return redact("\n".join(lines))


def _delta(c: ItemChange) -> str:
    key = {
        "due_changed": "due_at_local", "start_changed": "start_at", "title_changed": "title",
        "type_changed": "type", "course_changed": "course", "id_changed": "uids",
    }.get(c.kind)
    if key and c.old and c.new:
        return f"{c.old.get(key)} → {c.new.get(key)}"
    if c.kind == "created" and c.new:
        return f"due {c.new.get('due_at_local') or c.new.get('start_at')}"
    if c.kind == "removed" and c.old:
        return f"was due {c.old.get('due_at_local') or c.old.get('start_at')}"
    return ""
