"""End-to-end run-all with the network and data directories stubbed out."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from spike import config, fetch, pipeline
from spike.config import FeedConfig

FIXTURES = Path(__file__).parent / "fixtures"
FEED = FeedConfig(label="uw-test", institution="waterloo", url="https://learn.uwaterloo.ca/feed.ics?token=T0KEN12345")


def test_run_all_twice_produces_outputs_and_diff(tmp_path, monkeypatch):
    for name in ("DATA_DIR", "RAW_DIR", "OUT_DIR", "REPORTS_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower())
    monkeypatch.setattr(config, "load_feeds", lambda **_: [FEED])

    bodies = iter([(FIXTURES / "synthetic_before.ics").read_bytes(), (FIXTURES / "synthetic_after.ics").read_bytes()])
    monkeypatch.setattr(fetch, "fetch_feed", lambda feed, inst: next(bodies))

    clock = iter([datetime(2026, 10, 4, 12, tzinfo=timezone.utc) + timedelta(days=d) for d in range(2)])
    real_save = fetch.save_snapshot
    monkeypatch.setattr(fetch, "save_snapshot", lambda feed, body: real_save(feed, body, now=next(clock)))

    (first,) = pipeline.run_all()
    assert first.ok and "10 events -> 8 items" in first.message and "vs" not in first.message

    (second,) = pipeline.run_all()
    assert second.ok and "UID retention 70%" in second.message and "4 significant changes" in second.message

    out = json.loads((tmp_path / "out_dir" / "all_items.json").read_text(encoding="utf-8"))
    assert {"institution", "course_code", "title", "type", "due_at", "start_at", "source",
            "external_source_id", "raw_event_title"} <= set(out[0])

    reports = sorted(p.name for p in (tmp_path / "reports_dir" / "waterloo" / "uw-test").iterdir())
    assert reports == ["20261004T120000Z.md", "20261005T120000Z.diff.md", "20261005T120000Z.md"]
    assert all("T0KEN12345" not in p.read_text(encoding="utf-8")
               for p in (tmp_path / "reports_dir").rglob("*.md"))
