from datetime import datetime, timezone
from pathlib import Path

from spike.analyze import Snapshot, analyze_feed, answers, render
from spike.config import FeedConfig, InstitutionConfig
from spike.normalize import normalize_events
from spike.parse import parse_ics

FIXTURES = Path(__file__).parent / "fixtures"
INST = InstitutionConfig(
    slug="waterloo", name="UW", lms_name="LEARN", hosts=("learn.example.ca",), timezone="America/Toronto"
)
FEED = FeedConfig(label="uw-test", institution="waterloo", url="https://learn.example.ca/feed.ics?token=SECRET999")


def _snap(name: str, when: datetime) -> Snapshot:
    events = parse_ics((FIXTURES / name).read_bytes()).events
    return Snapshot(when, when.strftime("%Y%m%dT%H%M%SZ"), events, normalize_events(events, INST))


def _history():
    return [
        _snap("synthetic_before.ics", datetime(2026, 10, 4, 12, tzinfo=timezone.utc)),
        _snap("synthetic_after.ics", datetime(2026, 10, 5, 12, tzinfo=timezone.utc)),
    ]


def test_single_snapshot_is_pending():
    a = analyze_feed(FEED, INST, _history()[:1])
    assert all(v.startswith("⏳") for v in answers(a).values())


def test_longitudinal_answers_from_two_snapshots():
    a = analyze_feed(FEED, INST, _history())
    ans = answers(a)
    assert ans["Q10"].startswith("⚠️") and "1 items re-appeared under a new UID" in ans["Q10"]
    assert ans["Q11"].startswith("✅") and "1/1 time changes kept the same UID" in ans["Q11"]
    assert "SEQUENCE bumped on 1/1" in ans["Q11"]
    assert ans["Q15"].startswith("✅") and "1 removed (1 before their due date" in ans["Q15"]
    assert a.uids_seen == 12 and a.uids_gone == 3


def test_render_has_coverage_checklist_and_changelog_without_secrets():
    md = render([analyze_feed(FEED, INST, _history())], generated_at=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert "| CS135 |" in md and "### Course coverage" in md
    assert "- [ ] Fri Oct 09 10:00 PM · **CS135** · Assignment 4 (assignment)" in md
    assert "due_changed · CS135 · Assignment 4" in md
    assert "SECRET999" not in md


def test_no_snapshots():
    md = render([analyze_feed(FEED, INST, [])])
    assert "No snapshots yet." in md
