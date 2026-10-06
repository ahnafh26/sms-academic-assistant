"""Markdown report: what real notification emails look like and what we extracted."""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

from .classify import NotificationEvent
from .parse import RawEmail


def subject_shape(subject: str) -> str:
    s = re.sub(r"[“\"'][^”\"']+[”\"']", '"…"', subject)
    s = re.sub(r"\b[A-Z]{2,5}\s?-?\s?\d{2,4}[A-Z]?\b", "CODE", s)
    return re.sub(r"\d+", "#", s)


def link_shape(link: str) -> str:
    parts = urlsplit(link)
    path = re.sub(r"/\d+", "/#", parts.path)
    return f"{parts.hostname}{path}"


def build_report(pairs: list[tuple[RawEmail, NotificationEvent | None]]) -> str:
    events = [ev for _, ev in pairs if ev]
    ignored = [e for e, ev in pairs if ev is None]
    out = [
        "# Brightspace email notifications: report (auto-generated)",
        "",
        f"- emails read: **{len(pairs)}**. Brightspace notifications: **{len(events)}**. Ignored: {len(ignored)}",
        f"- by institution: {dict(Counter(ev.institution or '?' for ev in events))}",
        f"- by kind: {dict(Counter(ev.kind for ev in events))}",
        f"- with a course code: {sum(bool(ev.course_code) for ev in events)}; with an org unit: "
        f"{sum(bool(ev.org_unit_id) for ev in events)}; with a grade: {sum(bool(ev.grade) for ev in events)}; "
        f"mentioning a deadline change: {sum(ev.mentions_deadline_change for ev in events)}",
        "",
        "## Senders (E2)",
        "",
    ]
    senders = Counter((e.sender, e.sender_name) for e, ev in pairs if ev)
    out += [f"- `{addr}` ({name}) ×{n}" for (addr, name), n in senders.most_common()] or ["- none"]

    out += ["", "## Subject patterns (E3)", ""]
    shapes = Counter((ev.kind, subject_shape(e.subject)) for e, ev in pairs if ev)
    out += [f"- [{kind}] `{shape}` ×{n}" for (kind, shape), n in shapes.most_common()] or ["- none"]

    out += ["", "## Link patterns (E4)", ""]
    links = Counter(link_shape(link) for e, ev in pairs if ev for link in e.links if "/d2l/" in link)
    out += [f"- `{shape}` ×{n}" for shape, n in links.most_common(25)] or ["- none"]

    out += ["", "## Extracted events", "", "| received | inst | kind | course | title | facts |", "|---|---|---|---|---|---|"]
    for ev in events:
        facts = []
        if ev.grade:
            facts.append(f"grade {ev.grade['percent']}%")
        if ev.due_at:
            facts.append(f"due {ev.due_at}")
        if ev.mentions_deadline_change:
            facts.append(f"⚠️ deadline change → {ev.deadline_change_to or '?'}")
        if ev.warnings:
            facts.append(f"warnings: {', '.join(ev.warnings)}")
        title = (ev.title or "").replace("|", "\\|")[:70]
        out.append(f"| {(ev.received_at or '')[:16]} | {ev.institution} | {ev.kind} | {ev.course_code or '-'} | "
                   f"{title} | {'; '.join(facts)} |")

    unclassified = [(e, ev) for e, ev in pairs if ev and ev.kind == "other"]
    out += ["", "## Unclassified notifications (need new rules)", ""]
    for e, _ in unclassified[:15]:
        body = re.sub(r"\s+", " ", e.text)[:300]
        out += [f"- **{e.subject}**", f"  > {body}"]
    if not unclassified:
        out.append("- none")

    out += ["", "## Ignored (not Brightspace)", ""]
    out += [f"- {e.sender}: {e.subject}" for e in ignored[:20]] or ["- none"]
    return "\n".join(out)
