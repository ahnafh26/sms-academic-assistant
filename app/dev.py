"""Local fake phone: `python -m app.dev` (or run_dev.bat).

Starts the web server (for the connect link) and opens a terminal "phone"
that texts the assistant through the exact same code path real texts
will use: router -> sign-up / brain -> outbox. Texts the assistant sends
later (like "Connected!" after you finish the web page) pop in on their own.

The scheduler runs here too, so reminders, due-soon texts and the morning
briefing arrive like they would on a real phone. /briefing shows today's
briefing now, and /tick runs the scheduler once without waiting a minute.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

from .config import ROOT_DIR, get_settings

PROTOTYPE_ENV = ROOT_DIR / "prototype" / ".env"


def _borrow_prototype_key() -> None:
    """Dev convenience: reuse the free LLM key already pasted into prototype/.env."""
    if os.environ.get("LLM_API_KEY") or get_settings().llm_api_key or not PROTOTYPE_ENV.exists():
        return
    for line in PROTOTYPE_ENV.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL") and value:
            os.environ.setdefault(key, value)
    get_settings.cache_clear()


def _start_server(port: int) -> None:
    import uvicorn

    from .main import create_app

    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return
        time.sleep(0.05)
    raise SystemExit(f"web server didn't start on port {port} (already in use? try --port 8001)")


def outdated_tables() -> list[str]:
    """Tables in the local SQLite file that are missing columns the models now have."""
    from sqlalchemy import inspect

    from .db import Base, session_factory

    inspector = inspect(session_factory().kw["bind"])
    return [table.name for table in Base.metadata.sorted_tables
            if {c.name for c in table.columns} - {c["name"] for c in inspector.get_columns(table.name)}]


def deliver_pending(phone: str, out=print) -> int:
    """Print texts queued for this number (the fake phone is the delivery transport)."""
    from .db import session_scope
    from .messaging import take_queued

    with session_scope() as session:
        bodies = [m.body for m in take_queued(session, phone)]
    for body in bodies:
        out("\n".join(["", "📱 Assistant:", *("   " + line for line in body.splitlines()), ""]))
    return len(bodies)


def send_from_phone(phone: str, body: str) -> None:
    from .db import session_scope
    from .router import handle_inbound

    with session_scope() as session:
        handle_inbound(session, phone, body)


def briefing_now(phone: str) -> bool:
    """Dev only: text this number its morning briefing right now, whatever the time."""
    from sqlalchemy import select

    from .db import session_scope, utcnow
    from .messaging import send_text
    from .models import User
    from .scheduler import briefing_text

    with session_scope() as session:
        user = session.scalar(select(User).where(User.phone == phone))
        if user is None or user.onboarding_step != "done":
            return False
        send_text(session, user, briefing_text(session, user, utcnow()), kind="system")
    return True


def tick_now() -> dict[str, int]:
    from .db import session_scope
    from .scheduler import tick

    with session_scope() as session:
        return tick(session)


def forget(phone: str) -> None:
    """Dev only: delete this number's student so sign-up starts over."""
    from sqlalchemy import delete, select

    from .db import session_scope
    from .models import Message, User

    with session_scope() as session:
        user = session.scalar(select(User).where(User.phone == phone))
        if user:
            session.delete(user)
        session.execute(delete(Message).where(Message.phone == phone))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="app.dev", description="Text the assistant from a fake phone")
    parser.add_argument("--phone", default="+15195550187", help="your fake phone number")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    os.environ.setdefault("PUBLIC_BASE_URL", f"http://localhost:{args.port}")
    _borrow_prototype_key()
    get_settings.cache_clear()

    from . import scheduler
    from .brain import current_model
    from .db import configure
    from .security import normalize_phone

    phone = normalize_phone(args.phone)
    configure()
    stale = outdated_tables()
    if stale:
        raise SystemExit(f"Your local database is from an older version (missing columns in {', '.join(stale)}). "
                         f"Delete data/app.db (local test data only) and run this again.")
    _start_server(args.port)
    brain = "on" if current_model() else "off (paste a free Gemini key into prototype/.env or .env)"
    print(f"Fake phone {phone} · connect pages at http://localhost:{args.port} · AI brain: {brain}")
    print('Text like you would on your phone. Start with "hi".')
    print("Commands: /briefing (morning text now)  /tick (run reminders now)  /reset (start over)  /quit\n")

    stop = threading.Event()
    scheduler_stop = scheduler.start(interval_seconds=30)

    def poll() -> None:
        while not stop.is_set():
            try:
                if deliver_pending(phone):
                    print("you > ", end="", flush=True)
            except Exception as exc:  # keep the phone alive in dev
                print(f"\n(poll error: {exc})")
            stop.wait(1.0)

    threading.Thread(target=poll, daemon=True).start()
    try:
        while True:
            try:
                text = input("you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not text:
                continue
            if text in ("/quit", "/exit"):
                return 0
            if text == "/reset":
                forget(phone)
                print("(this number was forgotten; text \"hi\" to sign up again)\n")
                continue
            if text == "/briefing":
                if not briefing_now(phone):
                    print("(finish signing up first)\n")
                deliver_pending(phone)
                continue
            if text == "/tick":
                print(f"(scheduler ran: {tick_now()})")
                deliver_pending(phone)
                continue
            send_from_phone(phone, text)
            deliver_pending(phone)
    finally:
        stop.set()
        scheduler_stop.set()


if __name__ == "__main__":
    sys.exit(main())
