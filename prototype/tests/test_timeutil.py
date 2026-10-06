from datetime import datetime, timezone

import pytest

from chat.timeutil import RangeError, fmt_relative, fmt_when, resolve_range
from conftest import NOW, TZ  # Mon Oct 5 2026 09:00 Toronto


def _days(name, **kw):
    start, end, label = resolve_range(name, NOW, TZ, **kw)
    return start.date().isoformat(), end.date().isoformat(), label


@pytest.mark.parametrize(
    "name,expected",
    [
        ("today", ("2026-10-05", "2026-10-06", "today")),
        ("tomorrow", ("2026-10-06", "2026-10-07", "tomorrow")),
        ("this_week", ("2026-10-05", "2026-10-12", "this week")),      # through Sunday Oct 11
        ("next_week", ("2026-10-12", "2026-10-19", "next week")),
        ("next_7_days", ("2026-10-05", "2026-10-13", "the next 7 days")),
        ("this_month", ("2026-10-05", "2026-11-01", "the rest of this month")),
    ],
)
def test_named_ranges(name, expected):
    assert _days(name) == expected


def test_custom_range_inclusive_end():
    assert _days("custom", start_date="2026-10-05", end_date="2026-10-09") == (
        "2026-10-05", "2026-10-10", "Mon Oct 5 to Fri Oct 9")


def test_custom_range_errors():
    with pytest.raises(RangeError):
        resolve_range("custom", NOW, TZ, start_date="Friday")
    with pytest.raises(RangeError):
        resolve_range("custom", NOW, TZ, start_date="2026-10-09", end_date="2026-10-01")
    with pytest.raises(RangeError):
        resolve_range("someday", NOW, TZ)


def test_fmt_when_and_dst():
    assert fmt_when(datetime(2026, 10, 6, 3, 59, tzinfo=timezone.utc), TZ) == "Mon Oct 5, 11:59 PM"
    assert fmt_when(datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc), TZ) == "Mon Oct 5, 10:00 AM"
    assert fmt_when(datetime(2026, 11, 3, 4, 59, tzinfo=timezone.utc), TZ) == "Mon Nov 2, 11:59 PM"  # EST
    assert fmt_when(datetime(2026, 12, 12, 5, 0, tzinfo=timezone.utc), TZ, all_day=True) == "Sat Dec 12 (all day)"


def test_fmt_relative():
    assert fmt_relative(datetime(2026, 10, 5, 16, 0, tzinfo=TZ), NOW) == "in 7 hours"
    assert fmt_relative(datetime(2026, 10, 8, 9, 0, tzinfo=TZ), NOW) == "in 3 days"
    assert fmt_relative(datetime(2026, 10, 5, 8, 30, tzinfo=TZ), NOW) == "30 min ago"
