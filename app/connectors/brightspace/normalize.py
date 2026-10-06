"""RawEvent[] -> NormalizedItem[]: the shape the product's database will store.

Pipeline (pure, no I/O):
1. classify each event's role from its title suffix (due/start/end/plain)
2. detect its course (code + name) and an org-unit fallback key
3. group events of the same activity: (course key, base title)
4. merge each group into one item, resolving times to UTC
5. classify item type, assign external_source_id + fingerprint
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .institutions import InstitutionConfig
from .parse import RawEvent
from .rules import COURSE_CODE_STOPWORDS, Rules, build_rules

SOURCE = "brightspace_calendar"


@dataclass
class NormalizedItem:
    institution: str
    course_code: str | None
    course_name: str | None
    title: str
    type: str
    due_at: str | None
    due_at_local: str | None
    start_at: str | None
    end_at: str | None
    available_at: str | None
    available_until: str | None
    all_day: bool
    cancelled: bool
    source: str
    external_source_id: str
    fingerprint: str
    raw_event_title: str
    raw_event_uids: list[str]
    org_unit_id: str | None
    course_label: str | None
    venue: str | None
    org_kind: str  # course | community | institution
    source_tool: str | None  # Brightspace tool that created the event (quizzes, dropbox, ...)
    category: str  # deliverable | exam | content | checklist | event | non_course
    lms_event_id: str | None
    in_term: bool | None  # None when the course label carries no term
    course_detection: str
    type_detection: str
    source_url: str | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _Classified:
    event: RawEvent
    role: str  # due | start | end | plain
    base_title: str
    course_code: str | None
    course_name: str | None
    course_how: str
    org_unit: str | None
    course_label: str | None = None
    venue: str | None = None

    @property
    def course_key(self) -> str:
        return self.course_code or self.course_label or (f"ou:{self.org_unit}" if self.org_unit else "")


@dataclass
class CourseMatch:
    code: str | None
    name: str | None
    how: str
    label: str | None = None  # the org unit's display label, e.g. "CS 115 - Fall 2026"
    venue: str | None = None  # instructor-entered location, e.g. "Zoom link"


def normalize_events(events: list[RawEvent], inst: InstitutionConfig) -> list[NormalizedItem]:
    rules = build_rules(inst.rules)
    tz = ZoneInfo(inst.timezone)
    classified = [_classify(ev, rules) for ev in events]
    _backfill_course_codes(classified)

    groups: dict[tuple[str, str], list[_Classified]] = {}
    for c in classified:
        groups.setdefault((c.course_key, _norm(c.base_title)), []).append(c)

    items: list[NormalizedItem] = []
    for (course_key, base_norm), members in groups.items():
        for ordinal, bundle in enumerate(_split_bundles(members, tz)):
            items.append(_merge(bundle, inst, rules, tz, course_key, base_norm, ordinal))
    _flag_course_date_outliers(items)
    items.sort(key=lambda i: (i.due_at or i.start_at or i.available_at or "9999", i.course_code or "", i.title))
    return items


COURSE_OUTLIER_DAYS = 200


def _flag_course_date_outliers(items: list[NormalizedItem]) -> None:
    """Fallback stale-date check for labels without a term (e.g. Laurier's
    "BU-111-All-Sections - ..."): a real feed had a 2024 "Midterm" inside a
    course whose other items were all in fall 2026. Items far from the
    course's median date are marked out of term."""
    by_course: dict[str, list[tuple[NormalizedItem, datetime]]] = {}
    for item in items:
        anchor = item.due_at or item.start_at or item.available_at or item.available_until
        if item.org_kind == "course" and item.in_term is None and anchor:
            key = item.course_code or item.course_label or ""
            by_course.setdefault(key, []).append((item, datetime.fromisoformat(anchor.replace("Z", "+00:00"))))
    for members in by_course.values():
        if len(members) < 5:
            continue
        dates = sorted(d for _, d in members)
        median = dates[len(dates) // 2]
        for item, when in members:
            if abs(when - median) > timedelta(days=COURSE_OUTLIER_DAYS):
                item.in_term = False
                item.warnings.append("date_far_from_course_median")


# ---------------------------------------------------------------- classify

def strip_role(summary: str, rules: Rules) -> tuple[str, str]:
    title = summary.strip()
    for pattern, role in rules.prefixes:
        if pattern.search(title):
            return pattern.sub("", title).strip(), role
    for pattern, role in rules.suffixes:
        if pattern.search(title):
            return pattern.sub("", title).strip(), role
    return title, "plain"


def split_location(location: str) -> tuple[str, str | None]:
    """D2L LOCATION is the org unit label, or "<venue> (<label>)" when the
    instructor typed a location. Returns (label, venue)."""
    loc = location.strip()
    m = re.match(r"^(?P<venue>.+?)\s*\((?P<label>[^()]+)\)\s*$", loc)
    if m:
        inner = m.group("label").strip()
        # "(2026)" or "(Fall 2026)" is part of a label, not a wrapped label
        if len(inner) >= 4 and not re.fullmatch(r"(?:(?:fall|winter|spring|summer)\s*)?[\d\s\-/]+", inner, re.I):
            return inner, m.group("venue").strip()
    return loc, None


def _find_code(text: str, rules: Rules) -> re.Match | None:
    for m in rules.course_code.finditer(text):
        if m.group(1) not in COURSE_CODE_STOPWORDS:
            return m
    return None


def detect_course(ev: RawEvent, rules: Rules) -> CourseMatch:
    """LOCATION is authoritative when present (100% of events in real LEARN and
    MyLearningSpace feeds); other fields are a fallback for feeds without it."""
    if ev.location and "location" in rules.course_fields:
        label, venue = split_location(ev.location)
        m = _find_code(label, rules)
        if m:
            return CourseMatch(f"{m.group(1)}{m.group(2)}", _course_name(label, m, rules), "location", label, venue)
        return CourseMatch(None, label, "location:no_code", label, venue)
    fields = {
        "categories": " | ".join(ev.categories) if ev.categories else None,
        "description": ev.description,
        "summary": ev.summary,
    }
    for name in rules.course_fields:
        text = fields.get(name)
        m = _find_code(text, rules) if text else None
        if m:
            name_guess = _course_name(text, m, rules) if name == "categories" else None
            return CourseMatch(f"{m.group(1)}{m.group(2)}", name_guess, f"fallback:{name}")
    return CourseMatch(None, None, "none")


def _course_name(text: str, match: re.Match, rules: Rules) -> str | None:
    rest = text[: match.start()] + " " + text[match.end():]
    rest = rules.term.sub(" ", rest)
    rest = re.sub(r"\(\s*\)|\[\s*\]", " ", rest)
    parts = [p.strip(" -–—:|,") for p in re.split(r"\s[-–—|:]\s|\|", rest)]
    parts = [p for p in parts if len(p) > 2 and not re.fullmatch(r"[\d\s\-_/]+|(?:sec(?:tion)?|lec|tut|lab)\s*\w+", p, re.I)]
    return max(parts, key=len) if parts else None


def _org_unit(ev: RawEvent, rules: Rules) -> str | None:
    for text in (ev.url, ev.description):
        if not text:
            continue
        for pattern in rules.org_unit:
            m = pattern.search(text)
            if m:
                return m.group(1)
    return None


def _classify(ev: RawEvent, rules: Rules) -> _Classified:
    base, role = strip_role(ev.summary, rules)
    cm = detect_course(ev, rules)
    return _Classified(ev, role, base or ev.summary, cm.code, cm.name, cm.how, _org_unit(ev, rules), cm.label, cm.venue)


def _backfill_course_codes(items: list[_Classified]) -> None:
    """If events of an org unit carry exactly one course code, lend it to the others.

    Only unambiguous org units qualify: a global-calendar URL may carry the
    root org unit for every course, which must not collapse courses together.
    """
    codes_by_ou: dict[str, dict[str, str | None]] = {}
    for c in items:
        if c.org_unit and c.course_code:
            codes_by_ou.setdefault(c.org_unit, {}).setdefault(c.course_code, c.course_name)
    for c in items:
        codes = codes_by_ou.get(c.org_unit or "")
        if c.org_unit and not c.course_code and codes and len(codes) == 1:
            (code, name), = codes.items()
            c.course_code, c.course_name = code, c.course_name or name
            c.course_how = "org_unit_backfill"


# ---------------------------------------------------------------- group/merge

def _anchor(c: _Classified, tz) -> datetime:
    t = c.event.dtstart or c.event.dtend
    if t is None:
        return datetime.max.replace(tzinfo=timezone.utc)
    utc = t.to_utc(tz)
    if utc is None:  # all-day
        return datetime.combine(t.value, time(12), tz).astimezone(timezone.utc)
    return utc


def _split_bundles(members: list[_Classified], tz) -> list[list[_Classified]]:
    """Same course+title can hide several activities (e.g. weekly 'Reflection - Due').

    One bundle per due (or plain) event; start/end events attach to the earliest
    due at/after them, else the latest one.
    """
    anchors = sorted((c for c in members if c.role in ("due", "plain")), key=lambda c: _anchor(c, tz))
    if len(anchors) <= 1:
        return [members]
    bundles = [[a] for a in anchors]
    for c in members:
        if c.role in ("due", "plain"):
            continue
        t = _anchor(c, tz)
        idx = next((i for i, a in enumerate(anchors) if _anchor(a, tz) >= t), len(anchors) - 1)
        bundles[idx].append(c)
    return bundles


def _merge(
    bundle: list[_Classified],
    inst: InstitutionConfig,
    rules: Rules,
    tz: ZoneInfo,
    course_key: str,
    base_norm: str,
    ordinal: int,
) -> NormalizedItem:
    warnings: list[str] = []
    by_role: dict[str, list[_Classified]] = {}
    for c in bundle:
        by_role.setdefault(c.role, []).append(c)
    for role, cs in by_role.items():
        if len(cs) > 1:
            warnings.append(f"multiple_{role}_events")

    due_at = start_at = end_at = available_at = available_until = None
    all_day = False

    if "due" in by_role:
        due_at, all_day = _point(by_role["due"][0], rules, tz, warnings)
    if "start" in by_role:
        available_at, _ = _point(by_role["start"][0], rules, tz, warnings)
    if "end" in by_role:
        available_until, _ = _point(by_role["end"][0], rules, tz, warnings)
    if "plain" in by_role:
        ev = by_role["plain"][0].event
        s = ev.dtstart.to_utc(tz) if ev.dtstart else None
        e = ev.dtend.to_utc(tz) if ev.dtend else None
        if ev.dtstart and ev.dtstart.kind == "date":
            all_day = True
            start_at = datetime.combine(ev.dtstart.value, time(0), tz).astimezone(timezone.utc)
        elif s and e and e - s >= timedelta(minutes=1):
            start_at, end_at = s, e  # scheduled block (exam sitting, lecture, ...)
        elif s:
            due_at = due_at or s
    if due_at is None and available_until is not None and start_at is None:
        due_at = available_until
        warnings.append("due_from_availability_end")

    primary = (by_role.get("due") or by_role.get("plain") or by_role.get("end") or by_role.get("start"))[0]
    tool = source_tool(primary.event.description)
    item_type, type_how = classify_type(primary.base_title, rules)
    hint = TOOL_TYPE_HINTS.get(tool or "")
    if hint and item_type not in ("midterm", "exam") and (item_type == "other" or tool == "quizzes"):
        item_type, type_how = hint, f"tool:{tool}"
    org_kind = _org_kind(primary, inst, rules)
    category = _category(org_kind, item_type, tool, set(by_role))
    if start_at and not due_at and item_type in ("assignment", "other"):
        warnings.append("scheduled_block_without_due")

    uids = sorted({c.event.uid for c in bundle if c.event.uid})
    if not uids:
        warnings.append("missing_uid")
    if not primary.course_code:
        warnings.append("no_course_code")
    if any(c.event.rrule for c in bundle):
        warnings.append("recurring_event_not_expanded")

    cancelled = any((c.event.status or "").upper() == "CANCELLED" for c in bundle)
    in_term = _in_course_term(primary.course_label, due_at or start_at or available_at or available_until)
    if in_term is False:
        warnings.append("outside_course_term")
    key_uid = primary.event.uid or f"{primary.event.summary}|{primary.event.dtstart and primary.event.dtstart.value}"

    return NormalizedItem(
        institution=inst.slug,
        course_code=primary.course_code,
        course_name=primary.course_name,
        title=primary.base_title,
        type=item_type,
        due_at=_iso(due_at),
        due_at_local=_iso(due_at.astimezone(tz)) if due_at else None,
        start_at=_iso(start_at),
        end_at=_iso(end_at),
        available_at=_iso(available_at),
        available_until=_iso(available_until),
        all_day=all_day,
        cancelled=cancelled,
        source=SOURCE,
        external_source_id=_hash(inst.slug, key_uid),
        fingerprint=_hash(inst.slug, course_key, base_norm, str(ordinal)),
        raw_event_title=primary.event.summary,
        raw_event_uids=uids,
        org_unit_id=primary.org_unit,
        course_label=primary.course_label,
        venue=primary.venue,
        org_kind=org_kind,
        source_tool=tool,
        category=category,
        lms_event_id=lms_event_id(primary.event.uid),
        in_term=in_term,
        course_detection=primary.course_how,
        type_detection=type_how,
        source_url=primary.event.url,
        warnings=warnings,
    )


def _point(c: _Classified, rules: Rules, tz, warnings: list[str]) -> tuple[datetime | None, bool]:
    """Instant for a due/start/end marker event."""
    ev = c.event
    t = ev.dtstart or ev.dtend
    if t is None:
        warnings.append(f"{c.role}_event_without_time")
        return None, False
    if t.kind == "date":
        hh, mm = (int(x) for x in rules.all_day_due_time.split(":"))
        return datetime.combine(t.value, time(hh, mm), tz).astimezone(timezone.utc), True
    start = t.to_utc(tz)
    if ev.dtstart and ev.dtend and ev.dtend.kind != "date":
        end = ev.dtend.to_utc(tz)
        if end and start and abs(end - start) > timedelta(minutes=1):
            warnings.append(f"{c.role}_event_has_duration")
    return start, False


# "Quizzes: Quiz 1 - https://..." -> the D2L tool that owns the calendar event
_TOOL_PREFIX = re.compile(
    r"^\s*(checklists?|quizzes|quizzing|dropbox|assignments?|materials|modules|content|discussions?|surveys?|grades|self assessments?)\s*:",
    re.I,
)
TOOL_TYPE_HINTS = {
    "quizzes": "quiz", "dropbox": "assignment", "assignments": "assignment", "discussions": "discussion",
}
DELIVERABLE_TOOLS = {"quizzes", "dropbox", "assignments", "discussions", "surveys", "self assessments"}
CONTENT_TOOLS = {"materials", "modules", "content"}


def source_tool(description: str | None) -> str | None:
    m = _TOOL_PREFIX.match(description or "")
    if not m:
        return None
    tool = m.group(1).lower()
    return {"checklist": "checklists", "quizzing": "quizzes", "assignment": "assignments",
            "discussion": "discussions", "survey": "surveys", "self assessment": "self assessments"}.get(tool, tool)


def lms_event_id(uid: str | None) -> str | None:
    """D2L UIDs look like '<root org id>-<event id>@<host>'."""
    m = re.match(r"^\d+-(\d+)@", uid or "")
    return m.group(1) if m else None


def _org_kind(c: "_Classified", inst: InstitutionConfig, rules: Rules) -> str:
    if c.course_code:
        return "course"
    label = (c.course_label or "").casefold()
    institution_labels = {inst.name.casefold(), *(x.casefold() for x in inst.rules.get("institution_labels", []))}
    if label and label in institution_labels:
        return "institution"
    return "community" if label else "course"


def _category(org_kind: str, item_type: str, tool: str | None, roles: set[str]) -> str:
    """How the product should treat the item: only deliverables and exams drive deadline answers."""
    if org_kind != "course":
        return "non_course"
    if item_type in ("exam", "midterm"):
        return "exam"
    if tool in CONTENT_TOOLS:
        return "content"
    if tool in ("checklists", "checklist"):
        return "checklist"
    if tool in DELIVERABLE_TOOLS or roles & {"due", "end"}:
        return "deliverable"
    return "event"


_TERM_IN_LABEL = re.compile(r"\b(fall|winter|spring|summer)\s*(?:term\s*)?(20\d{2})\b", re.I)
_TERM_MONTHS = {"winter": (1, 4), "spring": (5, 8), "summer": (5, 8), "fall": (9, 12)}
TERM_MARGIN_BEFORE = timedelta(days=45)  # pre-term onboarding items
TERM_MARGIN_AFTER = timedelta(days=21)   # exams / late submissions


def _in_course_term(label: str | None, when: datetime | None) -> bool | None:
    """Courses copied from an old offering can carry stale dates (a real
    "CS 115 - Fall 2026" feed had 2018 dates). Flag items outside the term
    named in the course label."""
    m = _TERM_IN_LABEL.search(label or "")
    if not m or when is None:
        return None
    first, last = _TERM_MONTHS[m.group(1).lower()]
    year = int(m.group(2))
    start = datetime(year, first, 1, tzinfo=timezone.utc)
    end = datetime(year + (last == 12), last % 12 + 1, 1, tzinfo=timezone.utc)
    return start - TERM_MARGIN_BEFORE <= when < end + TERM_MARGIN_AFTER


def classify_type(title: str, rules: Rules) -> tuple[str, str]:
    for item_type, pattern in rules.type_rules:
        m = pattern.search(title)
        if m:
            return item_type, f"keyword:{m.group(0).lower()}"
    return "other", "default"


# ---------------------------------------------------------------- helpers

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat().replace("+00:00", "Z")
