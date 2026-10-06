"""Deterministic date ranges and phone-friendly formatting.

The LLM picks a named range ("today", "this_week", ...) or passes
explicit YYYY-MM-DD dates; all date math happens here. Small models
are unreliable at calendar arithmetic, and wrong dates are the one
error this product can't afford.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

RANGES = ["today", "tomorrow", "this_week", "next_week", "next_7_days", "next_14_days", "this_month", "custom"]


class RangeError(ValueError):
    pass


def local_midnight(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tz)


def resolve_range(
    name: str, now: datetime, tz: ZoneInfo, start_date: str | None = None, end_date: str | None = None
) -> tuple[datetime, datetime, str]:
    """Return (start, end_exclusive, human label) in the student's timezone."""
    today = now.astimezone(tz).date()
    if name == "today":
        return local_midnight(today, tz), local_midnight(today + timedelta(1), tz), "today"
    if name == "tomorrow":
        d = today + timedelta(1)
        return local_midnight(d, tz), local_midnight(d + timedelta(1), tz), "tomorrow"
    if name == "this_week":  # through Sunday night (weeks run Mon–Sun)
        end = today + timedelta(days=7 - today.weekday())
        return local_midnight(today, tz), local_midnight(end, tz), "this week"
    if name == "next_week":
        start = today + timedelta(days=7 - today.weekday())
        return local_midnight(start, tz), local_midnight(start + timedelta(7), tz), "next week"
    if name in ("next_7_days", "next_14_days"):
        days = 7 if name == "next_7_days" else 14
        return local_midnight(today, tz), local_midnight(today + timedelta(days + 1), tz), f"the next {days} days"
    if name == "this_month":
        first_next = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        return local_midnight(today, tz), local_midnight(first_next, tz), "the rest of this month"
    if name == "custom":
        try:
            start = date.fromisoformat(start_date) if start_date else today
            end = date.fromisoformat(end_date) if end_date else start
        except ValueError as exc:
            raise RangeError(f"dates must be YYYY-MM-DD: {exc}") from None
        if end < start:
            raise RangeError("end_date is before start_date")
        label = fmt_day(start) if start == end else f"{fmt_day(start)} to {fmt_day(end)}"
        return local_midnight(start, tz), local_midnight(end + timedelta(1), tz), label
    raise RangeError(f"unknown range '{name}' (use one of {', '.join(RANGES)})")


def fmt_day(d: date) -> str:
    return f"{d:%a %b} {d.day}"


def fmt_when(dt: datetime, tz: ZoneInfo, all_day: bool = False) -> str:
    """'Tue Oct 6, 10:00 AM' / 'Tue Oct 6, 11:59 PM' / 'Sat Dec 12 (all day)'."""
    local = dt.astimezone(tz)
    if all_day:
        return f"{fmt_day(local.date())} (all day)"
    hour = local.hour % 12 or 12
    return f"{fmt_day(local.date())}, {hour}:{local:%M} {'AM' if local.hour < 12 else 'PM'}"


def fmt_relative(dt: datetime, now: datetime) -> str:
    """'in 3 hours', 'in 2 days', '5 hours ago'."""
    delta = dt - now
    secs = delta.total_seconds()
    past = secs < 0
    secs = abs(secs)
    if secs < 3600:
        amount = f"{max(1, round(secs / 60))} min"
    elif secs < 48 * 3600:
        h = round(secs / 3600)
        amount = f"{h} hour{'s' if h != 1 else ''}"
    else:
        d = round(secs / 86400)
        amount = f"{d} days"
    return f"{amount} ago" if past else f"in {amount}"
