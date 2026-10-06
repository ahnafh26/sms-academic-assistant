"""Secrets at rest, one-time links, and phone-number normalization.

- Calendar feed URLs embed a personal token, so they're encrypted with
  Fernet (AES-128-CBC + HMAC). Production sets ENCRYPTION_KEY; local dev
  generates a key in data/ on first use.
- One-time setup links: 32 random bytes; only the SHA-256 is stored, the
  link expires, and it's single-use.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import DATA_DIR, get_settings
from .db import utcnow
from .models import SetupToken, User

DEV_KEY_FILE = DATA_DIR / "dev_encryption.key"


# ---------------------------------------------------------------- encryption

@lru_cache
def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not DEV_KEY_FILE.exists():
            DEV_KEY_FILE.write_bytes(Fernet.generate_key())
        key = DEV_KEY_FILE.read_text().strip()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes) -> str:
    try:
        return _fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken:
        raise ValueError("could not decrypt secret (wrong ENCRYPTION_KEY?)") from None


# ---------------------------------------------------------------- one-time links

def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_setup_token(session: Session, user: User, purpose: str, minutes: int | None = None) -> str:
    raw = secrets.token_urlsafe(32)
    minutes = minutes or get_settings().setup_link_minutes
    session.add(SetupToken(user_id=user.id, purpose=purpose, token_hash=_hash(raw),
                           expires_at=utcnow() + timedelta(minutes=minutes)))
    session.flush()
    return raw


@dataclass
class TokenCheck:
    token: SetupToken | None
    problem: str | None  # None when valid


def check_setup_token(session: Session, raw: str, purpose: str) -> TokenCheck:
    token = session.scalar(select(SetupToken).where(SetupToken.token_hash == _hash(raw or "")))
    if token is None or token.purpose != purpose:
        return TokenCheck(None, "This link isn't valid. Text the assistant to get a new one.")
    if token.used_at is not None:
        return TokenCheck(token, "This link was already used. Text the assistant if you need a new one.")
    if token.expires_at < utcnow():
        return TokenCheck(token, "This link expired. Text \"connect\" to the assistant to get a new one.")
    return TokenCheck(token, None)


def setup_link(raw: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/s/{raw}"


# ---------------------------------------------------------------- phone numbers

def normalize_phone(raw: str) -> str:
    """North American numbers -> E.164 (+1XXXXXXXXXX); anything already +E.164 is kept."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    raise ValueError(f"not a phone number: {raw!r}")


def mask_phone(phone: str) -> str:
    return f"(•••) •••-{phone[-4:]}" if len(phone) >= 4 else "•••"
