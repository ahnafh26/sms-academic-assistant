"""run-all: fetch -> snapshot -> normalize -> report -> diff vs previous, for every feed."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import config, diff, fetch, inspect_feed, normalize, parse
from .config import FeedConfig, InstitutionConfig


@dataclass
class FeedResult:
    feed: FeedConfig
    ok: bool
    message: str
    items: list[normalize.NormalizedItem]


def process_feed(feed: FeedConfig, inst: InstitutionConfig, *, offline: bool = False) -> FeedResult:
    if offline:
        snapshots = fetch.list_snapshots(feed)
        if not snapshots:
            return FeedResult(feed, False, "no saved snapshots (run without --offline first)", [])
        snapshot = snapshots[-1]
    else:
        try:
            snapshot = fetch.save_snapshot(feed, fetch.fetch_feed(feed, inst))
        except fetch.FetchError as exc:
            return FeedResult(feed, False, f"fetch failed: {exc}", [])

    parsed = parse.parse_ics(snapshot.read_bytes())
    items = normalize.normalize_events(parsed.events, inst)
    stamp = snapshot.stem
    report_dir = config.REPORTS_DIR / feed.institution / feed.label
    report_dir.mkdir(parents=True, exist_ok=True)

    _write_json(config.OUT_DIR / feed.institution / f"{feed.label}.json", items)
    (report_dir / f"{stamp}.md").write_text(
        inspect_feed.build_report(parsed, inst, label=feed.label, fetched_at=fetch.snapshot_time(snapshot), items=items),
        encoding="utf-8",
    )

    courses = len({i.course_code or i.org_unit_id for i in items})
    warned = sum(1 for i in items if i.warnings)
    msg = f"{len(parsed.events)} events -> {len(items)} items, {courses} courses, {warned} items with warnings"

    previous = [s for s in fetch.list_snapshots(feed) if s.stem < stamp]
    if previous:
        old_events = parse.parse_ics(previous[-1].read_bytes()).events
        event_diff = diff.diff_events(old_events, parsed.events)
        changes = diff.diff_items(normalize.normalize_events(old_events, inst), items)
        (report_dir / f"{stamp}.diff.md").write_text(
            diff.render_markdown(event_diff, changes, old_label=previous[-1].stem, new_label=stamp),
            encoding="utf-8",
        )
        retention = event_diff.uid_retention
        significant = sum(c.significant for c in changes)
        msg += (
            f" | vs {previous[-1].stem}: UID retention "
            f"{'n/a' if retention is None else f'{retention:.0%}'}, {significant} significant changes"
        )
    return FeedResult(feed, True, msg, items)


def run_all(label: str | None = None, *, offline: bool = False) -> list[FeedResult]:
    institutions = config.load_institutions()
    feeds = config.load_feeds(institutions=institutions)
    if label:
        feeds = [f for f in feeds if f.label == label]
    results = [process_feed(f, institutions[f.institution], offline=offline) for f in feeds]
    all_items = [i for r in results for i in r.items]
    _write_json(config.OUT_DIR / "all_items.json", all_items)
    return results


def _write_json(path: Path, items: list[normalize.NormalizedItem]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False), encoding="utf-8")
