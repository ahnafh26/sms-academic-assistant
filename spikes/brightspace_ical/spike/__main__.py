"""CLI entry point: python -m spike <command>."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config, diff, fetch, inspect_feed, normalize, parse
from .redact import redact


def cmd_list(args: argparse.Namespace) -> int:
    institutions = config.load_institutions()
    print("Institutions:")
    for inst in institutions.values():
        mark = "verified" if inst.verified else "unverified"
        print(f"  {inst.slug:<11} {inst.lms_name:<17} {', '.join(inst.hosts):<30} [{mark}]")
    try:
        feeds = config.load_feeds(institutions=institutions)
    except config.ConfigError as exc:
        print(f"\nFeeds: {exc}")
        return 0
    print("\nConfigured feeds:")
    for feed in feeds:
        print(f"  {feed.label:<20} {feed.institution}")
    return 0


def _selected_feeds(label: str | None) -> tuple[dict, list[config.FeedConfig]]:
    institutions = config.load_institutions()
    feeds = config.load_feeds(institutions=institutions)
    if label:
        feeds = [f for f in feeds if f.label == label]
        if not feeds:
            raise config.ConfigError(f"no feed labelled '{label}' in feeds.local.yaml")
    return institutions, feeds


def cmd_fetch(args: argparse.Namespace) -> int:
    institutions, feeds = _selected_feeds(args.label)
    failures = 0
    for feed in feeds:
        try:
            body = fetch.fetch_feed(feed, institutions[feed.institution])
        except fetch.FetchError as exc:
            failures += 1
            print(f"  FAIL {feed.label}: {exc}")
            continue
        path = fetch.save_snapshot(feed, body)
        print(f"  ok   {feed.label}: {len(body):,} bytes -> {path.relative_to(config.SPIKE_DIR)}")
    return 1 if failures else 0


def cmd_normalize(args: argparse.Namespace) -> int:
    institutions = config.load_institutions()
    if args.institution not in institutions:
        raise config.ConfigError(f"unknown institution '{args.institution}'")
    feed = parse.parse_ics(Path(args.ics).read_bytes())
    items = normalize.normalize_events(feed.events, institutions[args.institution])
    payload = json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"{len(feed.events)} events -> {len(items)} items -> {args.out}")
    else:
        print(redact(payload))
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    institutions = config.load_institutions()
    if args.institution not in institutions:
        raise config.ConfigError(f"unknown institution '{args.institution}'")
    path = Path(args.ics)
    report = inspect_feed.build_report(
        parse.parse_ics(path.read_bytes()), institutions[args.institution], label=path.stem
    )
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"report -> {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(report)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    institutions = config.load_institutions()
    if args.label:
        _, (feed,) = _selected_feeds(args.label)
        snapshots = fetch.list_snapshots(feed)
        if len(snapshots) < 2:
            raise config.ConfigError(f"need at least 2 snapshots for '{args.label}', found {len(snapshots)}")
        old_path, new_path = snapshots[-2], snapshots[-1]
        inst = institutions[feed.institution]
    else:
        if not (args.old and args.new and args.institution):
            raise config.ConfigError("use --label L, or OLD.ics NEW.ics --institution X")
        old_path, new_path = Path(args.old), Path(args.new)
        inst = institutions[args.institution]

    old_events = parse.parse_ics(old_path.read_bytes()).events
    new_events = parse.parse_ics(new_path.read_bytes()).events
    report = diff.render_markdown(
        diff.diff_events(old_events, new_events),
        diff.diff_items(normalize.normalize_events(old_events, inst), normalize.normalize_events(new_events, inst)),
        old_label=old_path.stem,
        new_label=new_path.stem,
    )
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"diff -> {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(report)
    return 0


def _anon_salt() -> str:
    """One random salt per machine (kept in gitignored data/) so mappings line up across snapshots."""
    path = config.DATA_DIR / "anon_salt.txt"
    if not path.exists():
        import secrets as _secrets

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_secrets.token_hex(16), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def cmd_anonymize(args: argparse.Namespace) -> int:
    from .anonymize import anonymize_ics
    from .rules import build_rules

    institutions = config.load_institutions()
    rules = build_rules(institutions[args.institution].rules)
    out = anonymize_ics(Path(args.ics).read_bytes(), salt=_anon_salt(), rules=rules)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_bytes(out)
    print(f"anonymized -> {args.out}  (REVIEW IT BY HAND before committing)")
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    from .pipeline import run_all

    results = run_all(args.label, offline=args.offline)
    if not results:
        print("no feeds configured")
        return 1
    for r in results:
        print(f"  {'ok  ' if r.ok else 'FAIL'} {r.feed.label}: {redact(r.message)}")
    print(f"\noutputs: {config.OUT_DIR.relative_to(config.SPIKE_DIR)}/  reports: {config.REPORTS_DIR.relative_to(config.SPIKE_DIR)}/")
    return 0 if all(r.ok for r in results) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spike", description="Brightspace iCal viability spike")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="show institutions and configured feeds")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("fetch", help="download configured feeds into data/raw/")
    p.add_argument("--label", help="only this feed label")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("normalize", help="normalize one .ics file to JSON")
    p.add_argument("ics", help="path to an .ics snapshot")
    p.add_argument("--institution", required=True, help="institution slug (rules + timezone)")
    p.add_argument("--out", help="write JSON here instead of stdout")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("inspect", help="markdown report of what an .ics file contains")
    p.add_argument("ics", help="path to an .ics snapshot")
    p.add_argument("--institution", required=True, help="institution slug (rules + timezone)")
    p.add_argument("--out", help="write the report here instead of stdout")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("diff", help="compare two snapshots (UID stability + item changes)")
    p.add_argument("old", nargs="?", help="older .ics")
    p.add_argument("new", nargs="?", help="newer .ics")
    p.add_argument("--institution", help="institution slug (when passing files)")
    p.add_argument("--label", help="diff the two latest snapshots of this feed instead")
    p.add_argument("--out", help="write the report here instead of stdout")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("anonymize", help="make a commit-safe fixture from a real snapshot")
    p.add_argument("ics", help="real .ics snapshot")
    p.add_argument("--institution", required=True, help="institution slug (course-code format)")
    p.add_argument("--out", required=True, help="e.g. tests/fixtures/real_waterloo_01.ics")
    p.set_defaults(func=cmd_anonymize)

    p = sub.add_parser("run-all", help="fetch + normalize + report + diff every configured feed")
    p.add_argument("--label", help="only this feed label")
    p.add_argument("--offline", action="store_true", help="reprocess the latest saved snapshot, no network")
    p.set_defaults(func=cmd_run_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except config.ConfigError as exc:
        print(f"config error: {redact(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:  # never let a traceback print a feed URL
        if os.environ.get("SPIKE_DEBUG"):
            raise
        print(f"error: {type(exc).__name__}: {redact(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
