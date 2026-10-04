"""Markdown report describing what a real feed contains.

Each section maps to one or more of the spike's viability questions
(Q-numbers refer to findings/FINDINGS.md).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import InstitutionConfig
from .normalize import NormalizedItem, normalize_events, strip_role
from .parse import ParsedFeed, RawEvent
from .redact import redact
from .rules import build_rules

SAMPLE_COUNT = 8
TRUNCATE = 240


def build_report(
    feed: ParsedFeed,
    inst: InstitutionConfig,
    *,
    label: str,
    fetched_at: datetime | None = None,
    items: list[NormalizedItem] | None = None,
) -> str:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    tz = ZoneInfo(inst.timezone)
    rules = build_rules(inst.rules)
    events = feed.events
    items = items if items is not None else normalize_events(events, inst)
    n = len(events) or 1

    out: list[str] = [
        f"# Feed report: {inst.name} ({inst.lms_name}) — `{label}`",
        "",
        f"- fetched: {fetched_at.isoformat()}",
        f"- VEVENTs: **{len(events)}** → normalized items: **{len(items)}**",
        "",
        "## Calendar-level properties (Q12)",
        "",
    ]
    out += [f"- `{k}`: {_clip(v)}" for k, v in sorted(feed.calendar_props.items())] or ["- (none)"]
    out += [f"- VTIMEZONE blocks: {', '.join(feed.timezones) or 'none'}", ""]

    # Property coverage
    coverage = Counter(name for ev in events for name in ev.properties)
    out += ["## Property coverage (Q3)", "", "| property | events | % |", "|---|---:|---:|"]
    out += [f"| `{name}` | {count} | {100 * count // n}% |" for name, count in coverage.most_common()]
    out.append("")

    # Time representation
    start_kinds = Counter(_kind(ev.dtstart) for ev in events)
    end_kinds = Counter(_kind(ev.dtend) for ev in events)
    tzids = Counter(t.tzid for ev in events for t in (ev.dtstart, ev.dtend) if t and t.tzid)
    durations = Counter(_duration_bucket(ev, tz) for ev in events)
    out += [
        "## Time representation (Q8, Q12)",
        "",
        f"- DTSTART kinds: {_fmt_counter(start_kinds)}",
        f"- DTEND kinds: {_fmt_counter(end_kinds)}",
        f"- TZIDs used: {_fmt_counter(tzids) or 'none'}",
        f"- DTEND − DTSTART: {_fmt_counter(durations)}",
        f"- local times of point events (top): {_fmt_counter(_local_times(events, tz), 8)}",
        "",
    ]

    # Window
    anchors = sorted(t for t in (_utc(ev, tz) for ev in events) if t)
    out += ["## Feed window (Q13)", ""]
    if anchors:
        first, last = anchors[0], anchors[-1]
        out += [
            f"- earliest event: {first.date()} ({(first - fetched_at).days:+d} days from fetch)",
            f"- latest event: {last.date()} ({(last - fetched_at).days:+d} days from fetch)",
            f"- past events: {sum(a < fetched_at for a in anchors)}, future events: {sum(a >= fetched_at for a in anchors)}",
            f"- by month: {_fmt_counter(Counter(a.astimezone(tz).strftime('%Y-%m') for a in anchors), 24, sort_keys=True)}",
        ]
    out.append("")

    # Title patterns
    roles = Counter(strip_role(ev.summary, rules)[1] for ev in events)
    suffixes = Counter(_suffix(ev.summary) for ev in events)
    out += [
        "## Title patterns (Q5–Q7, Q9)",
        "",
        f"- detected roles: {_fmt_counter(roles)}",
        f"- raw trailing segments (after last ' - '): {_fmt_counter(suffixes, 15)}",
        f"- first words: {_fmt_counter(Counter(ev.summary.split(' ')[0] for ev in events if ev.summary), 15)}",
        f"- events with role=plain (unrecognized suffix): {roles.get('plain', 0)} — check these for new suffix rules",
        "",
    ]

    # UIDs
    uids = [ev.uid for ev in events]
    dup = [u for u, c in Counter(uids).items() if u and c > 1]
    out += [
        "## UIDs (Q10)",
        "",
        f"- missing UID: {sum(1 for u in uids if not u)}; duplicate UIDs: {len(dup)}",
        f"- UID shapes: {_fmt_counter(Counter(_shape(u) for u in uids if u), 6)}",
        f"- SEQUENCE present: {sum(ev.sequence is not None for ev in events)}; "
        f"LAST-MODIFIED present: {sum(ev.last_modified is not None for ev in events)}",
        f"- STATUS values: {_fmt_counter(Counter(ev.status or '(none)' for ev in events))}",
        f"- RRULE (recurring) events: {sum(ev.rrule is not None for ev in events)}",
        "",
    ]

    # Courses
    courses = Counter((i.course_code or "(none)", i.course_name or "") for i in items)
    out += [
        "## Courses (Q4, Q16)",
        "",
        f"- detection method: {_fmt_counter(Counter(i.course_detection for i in items))}",
        f"- distinct LOCATION values: {len({ev.location for ev in events if ev.location})}",
        "",
        "| course_code | course_name | items |",
        "|---|---|---:|",
    ]
    out += [f"| {code} | {_clip(name, 70)} | {count} |" for (code, name), count in courses.most_common()]
    out += ["", "Top LOCATION values:", ""]
    out += [f"- {_clip(loc, 120)} ({c})" for loc, c in Counter(ev.location for ev in events if ev.location).most_common(15)]
    out.append("")

    # Types + warnings
    warnings = Counter(w for i in items for w in i.warnings)
    out += [
        "## Normalized types and warnings (Q5–Q7, Q17)",
        "",
        f"- types: {_fmt_counter(Counter(i.type for i in items))}",
        f"- type detection: {_fmt_counter(Counter(i.type_detection for i in items), 12)}",
        f"- warnings: {_fmt_counter(warnings) or 'none'}",
        f"- cancelled items: {sum(i.cancelled for i in items)}",
        "",
        "Items typed `other` (candidates for new rules):",
        "",
    ]
    out += [f"- {i.course_code or '-'} · {_clip(i.raw_event_title, 100)}" for i in items if i.type == "other"][:25] or ["- none"]
    out.append("")

    # Samples
    out += ["## Sample raw events (Q3)", ""]
    for ev in events[:SAMPLE_COUNT]:
        out.append("```")
        for name, values in ev.properties.items():
            for v in values:
                out.append(f"{name}: {_clip(v)}")
        out += ["```", ""]

    return redact("\n".join(out))


# ---------------------------------------------------------------- helpers

def _kind(t) -> str:
    return "missing" if t is None else t.kind


def _utc(ev: RawEvent, tz) -> datetime | None:
    t = ev.dtstart or ev.dtend
    if t is None:
        return None
    if t.kind == "date":
        return datetime(t.value.year, t.value.month, t.value.day, 12, tzinfo=tz).astimezone(timezone.utc)
    return t.to_utc(tz)


def _duration_bucket(ev: RawEvent, tz) -> str:
    if not ev.dtstart or not ev.dtend:
        return "no DTEND" if ev.dtstart else "no DTSTART"
    if "date" in (ev.dtstart.kind, ev.dtend.kind):
        return "all-day"
    minutes = (ev.dtend.to_utc(tz) - ev.dtstart.to_utc(tz)).total_seconds() / 60
    if minutes == 0:
        return "0 (point)"
    if minutes <= 60:
        return "≤1h"
    if minutes <= 240:
        return "1–4h"
    return ">4h"


def _local_times(events: list[RawEvent], tz) -> Counter:
    c: Counter = Counter()
    for ev in events:
        if ev.dtstart and ev.dtstart.kind != "date":
            c[ev.dtstart.to_utc(tz).astimezone(tz).strftime("%H:%M")] += 1
    return c


def _suffix(summary: str) -> str:
    parts = re.split(r"\s[-–—]\s", summary)
    return parts[-1].strip() if len(parts) > 1 else "(no ' - ')"


def _shape(uid: str) -> str:
    return re.sub(r"[a-z]", "a", re.sub(r"[A-Z]", "A", re.sub(r"\d+", "#", uid)))


def _fmt_counter(c: Counter, limit: int = 10, sort_keys: bool = False) -> str:
    entries = sorted(c.items()) if sort_keys else c.most_common(limit)
    return ", ".join(f"`{k}`×{v}" for k, v in entries[:limit] if k is not None)


def _clip(text: object, limit: int = TRUNCATE) -> str:
    s = str(text).replace("\r", " ").replace("\n", " ⏎ ").replace("|", "\\|")
    return s if len(s) <= limit else s[: limit - 1] + "…"
