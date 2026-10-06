"""python -m mailspike run [--inbox DIR]"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .classify import classify
from .parse import load_inbox
from .report import build_report

SPIKE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SPIKE_DIR / "data"
INSTITUTIONS = SPIKE_DIR.parent / "brightspace_ical" / "institutions.yaml"


def load_hosts(path: Path = INSTITUTIONS) -> tuple[dict[str, str], dict[str, str]]:
    """host -> institution slug, and slug -> timezone, from the calendar spike's registry."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["institutions"]
    hosts = {h: slug for slug, inst in raw.items() for h in inst.get("hosts", [])}
    tzs = {slug: inst.get("timezone", "America/Toronto") for slug, inst in raw.items()}
    return hosts, tzs


def cmd_run(args: argparse.Namespace) -> int:
    inbox = Path(args.inbox) if args.inbox else DATA_DIR / "inbox"
    emails = load_inbox(inbox) if inbox.exists() else []
    if not emails:
        print(f"No .eml files under {inbox}. See README step 2 for how to save notification emails.")
        return 1
    hosts, tzs = load_hosts()
    pairs = []
    for e in emails:
        folder_school = Path(e.path).parent.name if e.path else None  # data/inbox/<school>/x.eml
        tz = ZoneInfo(tzs.get(folder_school, "America/Toronto"))
        pairs.append((e, classify(e, hosts, tz)))

    events = [ev for _, ev in pairs if ev]
    (DATA_DIR / "out").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "reports").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "out" / "email_events.json").write_text(
        json.dumps([ev.to_dict() for ev in events], indent=2, ensure_ascii=False), encoding="utf-8")
    (DATA_DIR / "reports" / "EMAIL_REPORT.md").write_text(build_report(pairs), encoding="utf-8")

    kinds: dict[str, int] = {}
    for ev in events:
        kinds[ev.kind] = kinds.get(ev.kind, 0) + 1
    print(f"{len(emails)} emails -> {len(events)} Brightspace notifications {kinds}")
    print("events: data/out/email_events.json   report: data/reports/EMAIL_REPORT.md")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="mailspike")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="classify every saved email and write the report")
    p.add_argument("--inbox", help="folder of .eml files (default data/inbox)")
    p.set_defaults(func=cmd_run)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
