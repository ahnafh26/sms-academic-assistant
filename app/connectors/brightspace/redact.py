"""Keep feed tokens out of every log line, report, and error message.

Two layers:
1. Known secrets: every configured feed URL (and its token values) is
   registered at load time and replaced verbatim wherever it appears.
2. Pattern fallback: any `token=` / `key=`-style query parameter is masked,
   so an unregistered URL (e.g. from a redirect) still never leaks.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlsplit

_SECRET_PARAM = re.compile(
    r"(?P<prefix>[?&](?:token|key|access_token|auth|sig|signature|t)=)[^&#\s\"'<>]+",
    re.IGNORECASE,
)
_MASK = "REDACTED"
_known: set[str] = set()


def register_url(url: str) -> None:
    """Register a feed URL so it and its query values are always masked."""
    if not url:
        return
    _known.add(url)
    for _, value in parse_qsl(urlsplit(url).query, keep_blank_values=False):
        if len(value) >= 8:
            _known.add(value)


def redact(text: object) -> str:
    out = str(text)
    # Longest first so a full URL is replaced before its token substring.
    for secret in sorted(_known, key=len, reverse=True):
        if secret in out:
            out = out.replace(secret, safe_url(secret) if "://" in secret else _MASK)
    return _SECRET_PARAM.sub(lambda m: m.group("prefix") + _MASK, out)


def safe_url(url: str) -> str:
    """scheme://host/path with every query value masked — safe to print."""
    parts = urlsplit(url)
    query = "&".join(f"{k}={_MASK}" for k, _ in parse_qsl(parts.query, keep_blank_values=True))
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + (f"?{query}" if query else "")


def clear_registry() -> None:
    """Test helper."""
    _known.clear()
