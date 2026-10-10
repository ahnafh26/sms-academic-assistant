"""Deleting an account by text: "DELETE MY ACCOUNT", then "DELETE 4821" within 10 minutes.

A phone number can be spoofed, so a one-time code sent to that number
has to come back before anything is erased. Deletion is a hard delete of
every row the student owns, done explicitly per table (SQLite doesn't
enforce ON DELETE CASCADE unless told to).
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .messaging import send_to_phone
from .models import (AcademicItem, ConversationTurn, Course, LmsConnection, Message, Reminder, SetupToken, SyncRun,
                     User)

PURPOSE = "delete_account"
CODE_MINUTES = 10
REQUEST = re.compile(r"^(please\s+)?(delete|erase|remove)\s+(my\s+)?(account|data|everything|number)$", re.I)
CONFIRM = re.compile(r"^delete\s+(\d{4})$", re.I)

ASK_TEXT = ("This permanently deletes your account and everything I've stored: your calendar link, deadlines, "
            "reminders and messages. To confirm, reply DELETE {code} within 10 minutes.")
WRONG_CODE_TEXT = "That code doesn't match or has expired. Text DELETE MY ACCOUNT to get a new one."
GOODBYE_TEXT = ("Your account and data are deleted. You won't get more texts from me. "
                "If you ever want to come back, just text me.")
OWNED_TABLES = (Reminder, ConversationTurn, SetupToken, SyncRun, AcademicItem, Course, LmsConnection)


def _hash(user: User, code: str) -> str:
    return hashlib.sha256(f"{user.id}:{code}".encode()).hexdigest()


def request_deletion(session: Session, user: User, now: datetime) -> str:
    code = f"{secrets.randbelow(10_000):04d}"
    session.add(SetupToken(user_id=user.id, purpose=PURPOSE, token_hash=_hash(user, code),
                           expires_at=now + timedelta(minutes=CODE_MINUTES), created_at=now))
    session.flush()
    return ASK_TEXT.format(code=code)


def confirm_deletion(session: Session, user: User, code: str, now: datetime) -> bool:
    token = session.scalar(select(SetupToken).where(
        SetupToken.user_id == user.id, SetupToken.purpose == PURPOSE, SetupToken.token_hash == _hash(user, code)))
    if token is None or token.used_at is not None or token.expires_at < now:
        return False
    phone = user.phone
    delete_everything(session, user)
    send_to_phone(session, phone, GOODBYE_TEXT, now=now)
    return True


def delete_everything(session: Session, user: User) -> None:
    for table in OWNED_TABLES:
        session.execute(delete(table).where(table.user_id == user.id))
    session.execute(delete(Message).where((Message.user_id == user.id) | (Message.phone == user.phone)))
    session.execute(delete(User).where(User.id == user.id))
    session.flush()
