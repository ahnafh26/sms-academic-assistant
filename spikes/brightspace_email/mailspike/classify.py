"""RawEmail -> NotificationEvent: what happened, in which course, with which facts.

Rules are evidence-driven and will be rewritten once real Brightspace
notification emails are collected; the strongest signal is expected to
be the D2L link path (/grades, /dropbox, /news, /quizzing, /content),
which is stable across schools, rather than subject wording, which isn't.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .parse import RawEmail

COURSE_CODE = re.compile(r"\b([A-Z]{2,5})\s?-?\s?(\d{2,4}[A-Z]?)\b")
STOPWORDS = {"LAB", "QUIZ", "TEST", "WEEK", "UNIT", "PART", "EXAM", "TERM", "FALL", "WINTER", "SPRING", "DUE",
             "HW", "CH", "ROOM", "RM", "PAGE", "OCT", "NOV", "DEC", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
             "JUL", "AUG", "SEP", "AM", "PM"}
ORG_UNIT = [re.compile(p) for p in (
    r"[?&]ou=(\d+)", r"/d2l/le/(?:news|content|calendar|discussions)/(\d+)", r"/d2l/home/(\d+)", r"/d2l/le/(\d+)/",
)]

# (kind, link-path pattern, text pattern) — first match wins; digests first because they mention everything
KIND_RULES: list[tuple[str, str | None, str | None]] = [
    ("digest", None, r"\b(summary of activity|activity summary|digest|daily summary|weekly summary)\b"),
    ("feedback", r"/dropbox/", r"\bfeedback\b"),
    ("grade", r"/grades/", r"\bgrades?\b.{0,40}\b(released|posted|updated|available)\b|\b(released|posted|updated)\b.{0,40}\bgrades?\b"),
    ("due_soon", None, r"\bdue\b.{0,60}\b(soon|approaching|tomorrow|today|upcoming)\b|\b(reminder|upcoming)\b.{0,60}\bdue\b"),
    ("announcement", r"/news/", r"\bannouncements?\b"),
    ("quiz", r"/quizzing/", None),
    ("content", r"/content/", r"\b(new|updated) content\b"),
    ("discussion", r"/discussions/", r"\bdiscussion\b"),
    ("assignment", r"/dropbox/", r"\bassignment\b.{0,30}\b(created|available|opened|posted)\b"),
]
DEADLINE_CHANGE = re.compile(
    r"\b(extended|extension|postponed|pushed back|moved to|rescheduled|new (due )?date|new deadline|"
    r"due date (has )?(been )?changed|is now due|now due)\b", re.I)
TITLE_PREFIX = re.compile(
    r"^(re:\s*|fw:\s*|fwd:\s*|reminder:\s*|grade released\s*[-:]\s*|feedback available:\s*|new announcement( in)?:?\s*|"
    r"new content:\s*|upcoming:\s*)", re.I)
TITLE_SUFFIX = re.compile(r"\s+(is due( soon| tomorrow| today)?|due soon|has been released)\.?$", re.I)

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
DATE_WORDY = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})"
    r"(?:\s*(?:at|@|,)?\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?)?", re.I)
DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?\b")
GRADE_FRACTION = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)")
GRADE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
GRADE_CONTEXT = re.compile(r"\b(grade|score|mark|points|result)\b", re.I)


@dataclass
class NotificationEvent:
    id: str
    institution: str | None
    kind: str
    course_code: str | None
    org_unit_id: str | None
    title: str | None
    received_at: str | None
    dates_mentioned: list[str]
    due_at: str | None
    grade: dict | None
    mentions_deadline_change: bool
    deadline_change_to: str | None
    url: str | None
    subject: str
    sender: str
    source: str = "email"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def is_brightspace(e: RawEmail, hosts: dict[str, str]) -> bool:
    return institution_for(e, hosts) is not None or any("/d2l/" in link for link in e.links)


def institution_for(e: RawEmail, hosts: dict[str, str]) -> str | None:
    candidates = [urlsplit(link).hostname or "" for link in e.links] + [e.sender.split("@")[-1]]
    for host in candidates:
        for known, slug in hosts.items():
            if host == known or host.endswith("." + known):
                return slug
    return None


def classify(e: RawEmail, hosts: dict[str, str], tz: ZoneInfo) -> NotificationEvent | None:
    """None for emails that aren't Brightspace notifications."""
    if not is_brightspace(e, hosts):
        return None
    d2l_links = [link for link in e.links if "/d2l/" in link]
    paths = " ".join(urlsplit(link).path.lower() + "/" for link in d2l_links)
    haystack = f"{e.subject}\n{e.text}"
    warnings: list[str] = []

    kind = "other"
    for name, link_pat, text_pat in KIND_RULES:
        if (link_pat and re.search(link_pat, paths)) or (text_pat and re.search(text_pat, haystack, re.I)):
            kind = name
            break
    if kind == "other":
        warnings.append("unclassified")

    dates = extract_dates(haystack, tz)
    change = bool(DEADLINE_CHANGE.search(haystack))
    due_at = dates[0] if kind == "due_soon" and dates else None
    if kind == "due_soon" and not dates:
        warnings.append("due_soon_without_date")

    return NotificationEvent(
        id=hashlib.sha256((e.message_id or f"{e.subject}|{e.sent_at}").encode()).hexdigest()[:24],
        institution=institution_for(e, hosts),
        kind=kind,
        course_code=course_code(haystack),
        org_unit_id=org_unit(d2l_links),
        title=title_for(e, kind),
        received_at=e.sent_at.isoformat() if e.sent_at else None,
        dates_mentioned=dates,
        due_at=due_at,
        grade=extract_grade(e.text) if kind in ("grade", "feedback", "digest", "other") else None,
        mentions_deadline_change=change,
        deadline_change_to=dates[-1] if change and dates else None,
        url=d2l_links[0] if d2l_links else None,
        subject=e.subject,
        sender=e.sender,
        warnings=warnings,
    )


def course_code(text: str) -> str | None:
    for m in COURSE_CODE.finditer(text):
        if m.group(1) not in STOPWORDS:
            return f"{m.group(1)}{m.group(2)}"
    return None


def org_unit(links: list[str]) -> str | None:
    for link in links:
        for pattern in ORG_UNIT:
            m = pattern.search(link)
            if m:
                return m.group(1)
    return None


def title_for(e: RawEmail, kind: str) -> str | None:
    quoted = re.search(r"[“\"']([^”\"']{3,80})[”\"']", e.subject)
    if quoted:
        return quoted.group(1).strip()
    subject = TITLE_PREFIX.sub("", e.subject).strip()
    if kind == "announcement" and (not subject or subject.lower().startswith(("in ", "new "))
                                   or COURSE_CODE.search(subject)):
        first = next((ln for ln in e.text.splitlines() if ln.strip() and "http" not in ln), None)
        return first.strip()[:120] if first else None
    if ":" in subject:  # "CS 135 - Fall 2026: Assignment 4" -> "Assignment 4"
        subject = subject.rsplit(":", 1)[1].strip()
    subject = TITLE_SUFFIX.sub("", subject).strip()
    return subject or None


def extract_dates(text: str, tz: ZoneInfo) -> list[str]:
    found: list[tuple[int, str]] = []
    for m in DATE_WORDY.finditer(text):
        mon, day, year, hh, mm, ap = m.groups()
        try:
            if hh:
                hour = int(hh) % 12 + (12 if ap.lower() == "p" else 0)
                found.append((m.start(), datetime(int(year), MONTHS[mon.lower()[:3]], int(day), hour,
                                                  int(mm or 0), tzinfo=tz).isoformat()))
            else:
                found.append((m.start(), f"{int(year):04d}-{MONTHS[mon.lower()[:3]]:02d}-{int(day):02d}"))
        except ValueError:
            continue
    for m in DATE_ISO.finditer(text):
        y, mo, d, hh, mm = m.groups()
        try:
            value = (datetime(int(y), int(mo), int(d), int(hh), int(mm), tzinfo=tz).isoformat() if hh
                     else f"{y}-{mo}-{d}")
            found.append((m.start(), value))
        except ValueError:
            continue
    seen: list[str] = []
    for _, value in sorted(found):
        if value not in seen:
            seen.append(value)
    return seen


def extract_grade(text: str) -> dict | None:
    for line in text.splitlines():
        if not GRADE_CONTEXT.search(line):
            continue
        frac = GRADE_FRACTION.search(line)
        if frac and float(frac.group(2)) > 0:
            score, out_of = float(frac.group(1)), float(frac.group(2))
            return {"score": score, "out_of": out_of, "percent": round(100 * score / out_of, 1)}
        pct = GRADE_PERCENT.search(line)
        if pct:
            return {"score": None, "out_of": None, "percent": float(pct.group(1))}
    return None
