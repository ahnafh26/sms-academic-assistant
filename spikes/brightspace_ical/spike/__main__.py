"""CLI entry point: python -m spike <command>."""

from __future__ import annotations

import argparse
import sys

from . import config


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spike", description="Brightspace iCal viability spike")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="show institutions and configured feeds")
    p.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except config.ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
