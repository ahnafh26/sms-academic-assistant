"""Longitudinal analysis across every saved snapshot of every feed.

Runs automatically at the end of `run-all` and rewrites
data/reports/ANALYSIS.md, so the viability questions that need time
(Q10 UID stability, Q11 edits keep UID, Q14 past items stay, Q15
deletions disappear) answer themselves as daily snapshots accumulate.
It also lists upcoming deadlines per course for the student to check by
hand, and flags courses that publish nothing to the calendar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import diff, fetch, normalize, parse
from .config import FeedConfig, InstitutionConfig
from .redact import redact

REVIEW_DAYS = 21
ACTIONABLE = ("deliverable", "exam")


@dataclass
class Snapshot:
    taken_at: datetime
    label: str
    events: list[parse.RawEvent]
    items: list[normalize.NormalizedItem]


@dataclass
class TimedChange:
    at: datetime
    change: diff.ItemChange


@dataclass
class FeedAnalysis:
    feed: FeedConfig
    inst: InstitutionConfig
    snapshots: list[Snapshot]
    changes: list[TimedChange] = field(default_factory=list)
    pair_retention: list[float] = field(default_factory=list)
    uid_gaps: int = 0
    uids_seen: int = 0
    uids_gone: int = 0
    moved_with_sequence_bump: int = 0
    moved_events: int = 0

    @property
    def latest(self) -> Snapshot:
        return self.snapshots[-1]


def load_history(feed: FeedConfig, inst: InstitutionConfig) -> list[Snapshot]:
    snaps = []
    for path in fetch.list_snapshots(feed):
        events = parse.parse_ics(path.read_bytes()).events
        snaps.append(Snapshot(fetch.snapshot_time(path), path.stem, events, normalize.normalize_events(events, inst)))
    return snaps


def analyze_feed(feed: FeedConfig, inst: InstitutionConfig, snapshots: list[Snapshot]) -> FeedAnalysis:
    a = FeedAnalysis(feed, inst, snapshots)
    if not snapshots:
        return a

    presence: dict[str, list[bool]] = {}
    for idx, snap in enumerate(snapshots):
        uids = {e.uid for e in snap.events if e.uid}
        for uid in uids:
            presence.setdefault(uid, [False] * len(snapshots))[idx] = True
    for flags in presence.values():
        first = flags.index(True)
        seen = flags[first:]
        last = len(seen) - 1 - seen[::-1].index(True)
        if not all(seen[: last + 1]):
            a.uid_gaps += 1  # vanished then came back
    a.uids_seen = len(presence)
    a.uids_gone = sum(1 for flags in presence.values() if not flags[-1])

    for old, new in zip(snapshots, snapshots[1:]):
        ev_diff = diff.diff_events(old.events, new.events)
        if ev_diff.uid_retention is not None:
            a.pair_retention.append(ev_diff.uid_retention)
        for old_ev, new_ev, fields in ev_diff.changed:
            if {"dtstart", "dtend"} & set(fields):
                a.moved_events += 1
                if (new_ev.sequence or 0) > (old_ev.sequence or 0):
                    a.moved_with_sequence_bump += 1
        a.changes += [TimedChange(new.taken_at, c) for c in diff.diff_items(old.items, new.items)]
    return a


# ---------------------------------------------------------------- verdicts

def _when(brief: dict | None) -> datetime | None:
    if not brief:
        return None
    raw = brief.get("due_at_local") or brief.get("start_at")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def answers(a: FeedAnalysis) -> dict[str, str]:
    n = len(a.snapshots)
    if n < 2:
        pending = f"⏳ needs ≥2 snapshots (have {n})"
        return {q: pending for q in ("Q10", "Q11", "Q14", "Q15")}

    days = (a.latest.taken_at - a.snapshots[0].taken_at).total_seconds() / 86400
    span = f"{n} snapshots over {days:.1f} days"
    out: dict[str, str] = {}

    # Q10: UID stability
    id_changed = [c for c in a.changes if c.change.kind == "id_changed"]
    min_ret = min(a.pair_retention) if a.pair_retention else None
    if not id_changed and a.uid_gaps == 0:
        out["Q10"] = f"✅ stable so far: no re-issued UIDs, no UID gaps ({span}; min pairwise retention {min_ret:.0%})"
    else:
        out["Q10"] = (f"⚠️ {len(id_changed)} items re-appeared under a new UID (caught by fingerprint), "
                      f"{a.uid_gaps} UIDs vanished and returned ({span})")

    # Q11: moved deadlines keep UID
    moved = [c for c in a.changes if c.change.kind in ("due_changed", "start_changed")]
    same_uid = [c for c in moved if c.change.matched_by == "external_source_id"]
    if not moved:
        out["Q11"] = f"⏳ no deadline/time changes observed yet ({span})"
    else:
        seq = f"; SEQUENCE bumped on {a.moved_with_sequence_bump}/{a.moved_events} moved events"
        mark = "✅" if len(same_uid) == len(moved) else "⚠️"
        out["Q11"] = f"{mark} {len(same_uid)}/{len(moved)} time changes kept the same UID{seq}"

    # Q14: past items remain
    now = a.latest.taken_at
    # only this term's items: stale copied-course dates would inflate the count
    past_present = [
        i for i in a.latest.items
        if i.org_kind == "course" and i.in_term is not False and (_iso(i.due_at) or now) < now
    ]
    out["Q14"] = (f"✅ yes — {len(past_present)} past-due current-term course items still in the latest feed "
                  "(no submission/completion status is exposed)") if past_present else "❓ no past-due items to judge"

    # Q15: deletions
    removed = [c for c in a.changes if c.change.kind == "removed"]
    if not removed:
        out["Q15"] = f"⏳ no items have disappeared yet ({span})"
    else:
        before_due = [c for c in removed if (_when(c.change.old) or c.at) > c.at]
        out["Q15"] = (f"✅ deleted items disappear: {len(removed)} removed "
                      f"({len(before_due)} before their due date → likely instructor deletions)")
    return out


def _iso(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


# ---------------------------------------------------------------- rendering

def render(analyses: list[FeedAnalysis], *, generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    lines = [
        "# Spike analysis (auto-generated)",
        "",
        f"Generated {generated_at.isoformat(timespec='minutes')} by `run-all`. Rewritten on every run. Don't edit.",
        "",
        "## Viability answers so far",
        "",
        "| Feed | Q10 UIDs stable | Q11 edits keep UID | Q14 past items stay | Q15 deletions disappear |",
        "|---|---|---|---|---|",
    ]
    for a in analyses:
        ans = answers(a)
        lines.append(f"| {a.feed.label} | {ans['Q10']} | {ans['Q11']} | {ans['Q14']} | {ans['Q15']} |")

    for a in analyses:
        if not a.snapshots:
            lines += ["", f"## {a.feed.label}", "", "No snapshots yet."]
            continue
        lines += _feed_section(a)
    return redact("\n".join(lines))


def _feed_section(a: FeedAnalysis) -> list[str]:
    tz = ZoneInfo(a.inst.timezone)
    now = a.latest.taken_at
    horizon = now + timedelta(days=REVIEW_DAYS)
    items = a.latest.items
    lines = [
        "",
        f"## {a.feed.label} — {a.inst.name} ({a.inst.lms_name})",
        "",
        f"- snapshots: {len(a.snapshots)} ({a.snapshots[0].label} → {a.latest.label})",
        f"- latest: {len(a.latest.events)} events → {len(items)} items; "
        f"UIDs seen across history: {a.uids_seen}, no longer present: {a.uids_gone}",
    ]

    # Coverage per course
    courses: dict[str, list[normalize.NormalizedItem]] = {}
    for i in items:
        if i.org_kind == "course":
            courses.setdefault(i.course_code or i.course_label or "?", []).append(i)
    lines += ["", "### Course coverage (this term)", "", "| course | upcoming deadlines | in-term items | stale items | note |", "|---|---:|---:|---:|---|"]
    for code, its in sorted(courses.items()):
        upcoming = [i for i in its if _actionable(i) and (_iso(i.due_at) or _iso(i.start_at) or now) >= now]
        in_term = [i for i in its if i.in_term is not False]
        stale = [i for i in its if i.in_term is False]
        note = "⚠️ no upcoming deadlines in calendar: check the course outline / other sites" if not upcoming else ""
        lines.append(f"| {code} | {len(upcoming)} | {len(in_term)} | {len(stale)} | {note} |")

    # Review checklist
    review = sorted(
        (i for i in items if _actionable(i) and now <= (_iso(i.due_at) or _iso(i.start_at) or now - timedelta(1)) <= horizon),
        key=lambda i: i.due_at or i.start_at or "",
    )
    lines += ["", f"### Deadlines in the next {REVIEW_DAYS} days: check these against Brightspace and the course outlines", ""]
    if not review:
        lines.append("- none")
    for i in review:
        when = (_iso(i.due_at) or _iso(i.start_at)).astimezone(tz)
        lines.append(f"- [ ] {when:%a %b %d %I:%M %p} · **{i.course_code or i.course_label}** · {i.title} ({i.type})")

    # Change log
    lines += ["", "### Changes detected between snapshots", ""]
    sig = [c for c in a.changes if c.change.significant or c.change.kind == "id_changed"]
    if not sig:
        lines.append("- none yet")
    for c in sig:
        ch = c.change
        delta = diff._delta(ch)
        lines.append(f"- {c.at:%Y-%m-%d %H:%M}Z · {ch.kind} · {ch.course_code or '-'} · {ch.title} · {delta}")
    return lines


def _actionable(i: normalize.NormalizedItem) -> bool:
    return i.category in ACTIONABLE and i.in_term is not False and not i.cancelled
