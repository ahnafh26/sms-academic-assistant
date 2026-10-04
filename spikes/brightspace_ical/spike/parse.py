"""Parse an .ics file into RawEvents, preserving everything for inspection.

No interpretation happens here (that's normalize.py) — we record exactly
what the feed said, including how each time was expressed, because the
spike's job is to learn what real Brightspace feeds contain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

from icalendar import Calendar

TimeKind = Literal["utc", "tzid", "floating", "date"]


@dataclass
class EventTime:
    value: datetime | date
    kind: TimeKind
    tzid: str | None = None

    def to_utc(self, default_tz) -> datetime | None:
        """Aware UTC datetime; None for all-day dates (caller decides policy)."""
        if self.kind == "date":
            return None
        dt = self.value
        assert isinstance(dt, datetime)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz)
        return dt.astimezone(timezone.utc)


@dataclass
class RawEvent:
    uid: str | None
    summary: str
    description: str | None
    location: str | None
    categories: list[str]
    url: str | None
    dtstart: EventTime | None
    dtend: EventTime | None
    status: str | None
    sequence: int | None
    rrule: str | None
    last_modified: datetime | None
    properties: dict[str, list[str]] = field(default_factory=dict)  # every property, as text


@dataclass
class ParsedFeed:
    calendar_props: dict[str, str]
    timezones: list[str]
    events: list[RawEvent]


def parse_ics(data: bytes) -> ParsedFeed:
    cal = Calendar.from_ical(data)
    calendar_props = {
        name: _text(value)
        for name, value in cal.items()
        if not isinstance(value, list)
    }
    timezones = [str(tz.get("TZID")) for tz in cal.walk("VTIMEZONE")]
    events = [_event(component) for component in cal.walk("VEVENT")]
    return ParsedFeed(calendar_props=calendar_props, timezones=timezones, events=events)


def _event(c) -> RawEvent:
    properties: dict[str, list[str]] = {}
    for name, value in c.items():
        values = value if isinstance(value, list) else [value]
        properties[name] = [_text(v) for v in values]

    seq = c.get("SEQUENCE")
    last_mod = c.get("LAST-MODIFIED")
    return RawEvent(
        uid=_opt(c.get("UID")),
        summary=_opt(c.get("SUMMARY")) or "",
        description=_opt(c.get("DESCRIPTION")),
        location=_opt(c.get("LOCATION")),
        categories=_categories(c.get("CATEGORIES")),
        url=_opt(c.get("URL")),
        dtstart=_time(c.get("DTSTART")),
        dtend=_time(c.get("DTEND")),
        status=_opt(c.get("STATUS")),
        sequence=int(seq) if seq is not None else None,
        rrule=_text(c.get("RRULE")) if c.get("RRULE") is not None else None,
        last_modified=last_mod.dt if last_mod is not None else None,
        properties=properties,
    )


def _time(prop) -> EventTime | None:
    if prop is None:
        return None
    dt = prop.dt
    if isinstance(dt, datetime):
        tzid = prop.params.get("TZID")
        if dt.tzinfo is None:
            return EventTime(dt, "floating")
        if tzid:
            return EventTime(dt, "tzid", str(tzid))
        return EventTime(dt, "utc")
    if isinstance(dt, date):
        return EventTime(dt, "date")
    return None


def _categories(prop) -> list[str]:
    if prop is None:
        return []
    props = prop if isinstance(prop, list) else [prop]
    out: list[str] = []
    for p in props:
        cats = getattr(p, "cats", None)
        out.extend(str(c) for c in cats) if cats is not None else out.append(_text(p))
    return out


def _opt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text(value: Any) -> str:
    to_ical = getattr(value, "to_ical", None)
    if callable(to_ical):
        raw = to_ical()
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return str(value)
