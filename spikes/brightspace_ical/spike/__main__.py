"""CLI entry point: python -m spike <command>."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import config, fetch, normalize, parse
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
