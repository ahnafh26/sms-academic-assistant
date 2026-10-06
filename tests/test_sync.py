from datetime import timedelta
from pathlib import Path

import pytest

from app.connectors.brightspace.fetch import FetchError
from app.models import AcademicItem, Course, LmsConnection, SyncRun, User
from app.security import encrypt
from app.sync import run_sync
from conftest import NOW

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://learn.uwaterloo.ca/d2l/le/calendar/feed/user/feed.ics?token=T"
EMPTY = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"


def fixture_fetcher(*names):
    bodies = [(FIXTURES / n).read_bytes() if n.endswith(".ics") else n.encode() for n in names]

    def fetch(url, inst):
        assert url == URL
        body = bodies.pop(0)
        if body == b"FAIL":
            raise FetchError("HTTP 403")
        return body
    return fetch


@pytest.fixture
def conn(session):
    user = User(phone="+15195550187", institution="waterloo")
    session.add(user)
    session.flush()
    c = LmsConnection(user_id=user.id, institution="waterloo", encrypted_secret=encrypt(URL))
    session.add(c)
    session.flush()
    return c


def _items(session, conn):
    return {i.title: i for i in session.query(AcademicItem).filter_by(user_id=conn.user_id)}


def test_first_sync_creates_items_without_reporting_them_as_new(session, conn):
    r = run_sync(session, conn, trigger="initial", fetcher=fixture_fetcher("synthetic_before.ics"), now=NOW)
    assert r.ok and r.items == 8 and r.created == []
    assert conn.last_success_at == NOW and conn.consecutive_failures == 0
    assert session.query(SyncRun).one().stats["items"] == 8


def test_second_sync_detects_new_moved_and_preserves_student_state(session, conn):
    fetch = fixture_fetcher("synthetic_before.ics", "synthetic_after.ics", "synthetic_after.ics")
    run_sync(session, conn, fetcher=fetch, now=NOW)
    items = _items(session, conn)
    items["Assignment 4"].completed_at = NOW
    items["Assignment 4"].estimate_minutes = 180

    r = run_sync(session, conn, fetcher=fetch, now=NOW + timedelta(hours=3))
    assert [i.title for i in r.created] == ["Assignment 5"]
    assert [(i.title, old.isoformat()) for i, old in r.moved] == [("Assignment 4", "2026-10-08T02:00:00+00:00")]
    a4 = _items(session, conn)["Assignment 4"]
    assert a4.completed_at == NOW and a4.estimate_minutes == 180  # never overwritten by a sync

    quiz = _items(session, conn)["Quiz 5"]  # removed from the feed
    assert quiz.missed_syncs == 1 and quiz.actionable  # one miss: still shown
    r3 = run_sync(session, conn, fetcher=fetch, now=NOW + timedelta(hours=6))
    assert r3.hidden == 1 and not _items(session, conn)["Quiz 5"].actionable


def test_reissued_uid_matched_by_fingerprint(session, conn):
    fetch = fixture_fetcher("synthetic_before.ics", "synthetic_after.ics")
    run_sync(session, conn, fetcher=fetch, now=NOW)
    before = {i.id for i in session.query(AcademicItem).filter_by(title="Weekly Reflection")}
    run_sync(session, conn, fetcher=fetch, now=NOW + timedelta(hours=3))
    after = session.query(AcademicItem).filter_by(title="Weekly Reflection").all()
    assert {i.id for i in after} == before and all(i.missed_syncs == 0 for i in after)


def test_failures_counted_and_connection_flagged(session, conn):
    fetch = fixture_fetcher("FAIL", "FAIL", "FAIL")
    for _ in range(3):
        r = run_sync(session, conn, fetcher=fetch, now=NOW)
    assert not r.ok and "403" in r.error
    assert conn.consecutive_failures == 3 and conn.status == "error"


def test_empty_feed_after_real_data_is_an_error(session, conn):
    def fetch(url, inst, calls=[]):
        calls.append(1)
        return (FIXTURES / "synthetic_before.ics").read_bytes() if len(calls) == 1 else EMPTY
    run_sync(session, conn, fetcher=fetch, now=NOW)
    r = run_sync(session, conn, fetcher=fetch, now=NOW)
    assert not r.ok and "empty" in r.error
    assert all(i.missed_syncs == 0 for i in session.query(AcademicItem))


def test_courses_include_ones_without_deadlines(session, conn):
    def fetch(url, inst):
        return (FIXTURES / "real_waterloo_anon.ics").read_bytes()
    run_sync(session, conn, trigger="initial", fetcher=fetch, now=NOW)
    courses = {c.code: c.has_calendar_items for c in session.query(Course) if c.code}
    # anonymized Waterloo: MATH 135 (XCIP 667) has deadlines; the other three courses don't
    assert courses == {"XCIP667": True, "QU434": False, "XCIP756": False, "QLSJA63": False}
