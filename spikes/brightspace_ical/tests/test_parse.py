from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from spike.parse import parse_ics

FIXTURES = Path(__file__).parent / "fixtures"
TORONTO = ZoneInfo("America/Toronto")


def _feed():
    return parse_ics((FIXTURES / "synthetic_before.ics").read_bytes())


def _by_uid(feed, uid):
    return next(e for e in feed.events if e.uid == uid)


def test_calendar_level_props():
    feed = _feed()
    assert feed.calendar_props["X-WR-TIMEZONE"] == "America/Toronto"
    assert len(feed.events) == 10


def test_time_kinds_recorded():
    feed = _feed()
    assert _by_uid(feed, "6606-1001-due@synthetic.d2l").dtstart.kind == "tzid"
    assert _by_uid(feed, "6606-1001-due@synthetic.d2l").dtstart.tzid == "America/Toronto"
    assert _by_uid(feed, "6606-2001-due@synthetic.d2l").dtstart.kind == "utc"
    assert _by_uid(feed, "6606-4001-due@synthetic.d2l").dtstart.kind == "floating"
    final = _by_uid(feed, "6606-3002@synthetic.d2l").dtstart
    assert final.kind == "date" and final.value == date(2026, 12, 12)


def test_to_utc_handles_tzid_and_floating():
    feed = _feed()
    a4 = _by_uid(feed, "6606-1001-due@synthetic.d2l").dtstart.to_utc(TORONTO)
    assert a4 == datetime(2026, 10, 8, 2, 0, tzinfo=timezone.utc)  # 22:00 EDT
    # floating time after DST ends (Nov 1, 2026) uses EST (-5)
    wr = _by_uid(feed, "6606-4002-due@synthetic.d2l").dtstart.to_utc(TORONTO)
    assert wr == datetime(2026, 11, 3, 4, 59, tzinfo=timezone.utc)


def test_raw_properties_preserved():
    ev = _by_uid(_feed(), "6606-1001-due@synthetic.d2l")
    assert ev.properties["SUMMARY"] == ["Assignment 4 - Due"]
    assert "URL" in ev.properties and "DTSTAMP" in ev.properties


def test_status_captured():
    assert _by_uid(_feed(), "6606-6001-due@synthetic.d2l").status == "CANCELLED"
