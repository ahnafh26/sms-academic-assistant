"""Terminal chat with the assistant: python -m chat [--debug] [--now ISO]."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .agent import Agent
from .config import Settings, SettingsError, load_settings
from .data import Store
from .llm import LLMError, OpenAICompatModel
from .timeutil import fmt_when
from .tools import Toolbox

HELP = """Ask anything about your deadlines, e.g.
  what's due today · what about tomorrow · what's next · anything bad this week
  what's due for BU111 · what exams do I have · what's my busiest day
  I finished econ · CS will take 3 hours · what should I work on rn · refresh
Commands: /help  /debug (show tool calls)  /reset (forget conversation)  /quit"""


def make_refresh(settings: Settings):
    """Re-run the spike pipeline (fetch + normalize + analysis) for an immediate sync."""
    python = settings.spike_dir / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = settings.spike_dir / ".venv" / "bin" / "python"

    def refresh() -> str:
        proc = subprocess.run(
            [str(python), "-m", "spike", "run-all"], cwd=settings.spike_dir,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
        )
        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip().startswith(("ok", "FAIL"))]
        return "; ".join(lines) or f"sync exited with code {proc.returncode}"
    return refresh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chat", description="Local test chat for the SMS academic assistant")
    parser.add_argument("--debug", action="store_true", help="print tool calls and results")
    parser.add_argument("--now", help="pretend the current time is this ISO datetime (testing)")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")

    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"Setup needed: {exc}")
        return 2

    tz = ZoneInfo(settings.timezone)
    fixed_now = datetime.fromisoformat(args.now).astimezone(timezone.utc) if args.now else None
    now_fn = (lambda: fixed_now) if fixed_now else (lambda: datetime.now(timezone.utc))

    store = Store(settings.items_path, settings.state_path)
    try:
        store.load()
    except FileNotFoundError as exc:
        print(exc)
        return 2

    debug = {"on": args.debug}

    def on_event(kind: str, payload: object) -> None:
        if not debug["on"]:
            return
        text = json.dumps(payload, ensure_ascii=False)
        print(f"  [{kind}] {text[:600]}{'…' if len(text) > 600 else ''}")

    toolbox = Toolbox(store, tz, now_fn=now_fn, refresh_fn=make_refresh(settings))
    model = OpenAICompatModel(settings.base_url, settings.api_key, settings.model)
    agent = Agent(model, toolbox, tz, now_fn=now_fn, on_event=on_event)

    courses = ", ".join(sorted({c for _, c, _ in store.courses()})) or "none"
    synced = fmt_when(store.data_as_of, tz) if store.data_as_of else "unknown"
    print(f"Academic assistant (local test) · {settings.provider}/{settings.model}")
    print(f"{len(store.items)} deadlines · courses: {courses} · last synced {synced}")
    print("Type /help for examples.\n")

    while True:
        try:
            text = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text in ("/quit", "/exit", "/q"):
            return 0
        if text == "/help":
            print(HELP + "\n")
            continue
        if text == "/reset":
            agent.reset()
            print("(conversation cleared)\n")
            continue
        if text == "/debug":
            debug["on"] = not debug["on"]
            print(f"(debug {'on' if debug['on'] else 'off'})\n")
            continue
        try:
            answer = agent.reply(text)
        except LLMError as exc:
            print(f"bot > (LLM problem: {exc})\n")
            continue
        print(f"bot > {answer}\n")


if __name__ == "__main__":
    sys.exit(main())
