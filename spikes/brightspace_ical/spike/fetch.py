"""Fetch a Brightspace calendar feed safely and store a timestamped snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from . import config
from .config import FeedConfig, InstitutionConfig
from .redact import redact, safe_url

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 15.0
MAX_REDIRECTS = 3
USER_AGENT = "sms-academic-assistant-spike/0.1 (+calendar feed viability test)"


class FetchError(Exception):
    def __init__(self, message: str):
        super().__init__(redact(message))


def check_url(url: str, inst: InstitutionConfig) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(f"refusing non-HTTPS feed URL: {safe_url(url)}")
    if not inst.allows_host(parts.hostname):
        raise FetchError(
            f"host '{parts.hostname}' is not in the allow-list for {inst.slug} "
            f"({', '.join(inst.hosts)}). If this is genuinely the school's feed host, "
            "add it to institutions.yaml."
        )


def fetch_feed(
    feed: FeedConfig,
    inst: InstitutionConfig,
    *,
    client: httpx.Client | None = None,
) -> bytes:
    """GET the feed, following redirects only within the institution's hosts."""
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    url = feed.url
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url, inst)
            with client.stream("GET", url, follow_redirects=False) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    url = urljoin(url, location)
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code} from {safe_url(url)}")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError(f"feed exceeds {MAX_BYTES} bytes")
                return _validate_ics(bytes(body), resp.headers.get("content-type", ""))
        raise FetchError(f"too many redirects (> {MAX_REDIRECTS})")
    except httpx.HTTPError as exc:
        raise FetchError(f"network error: {exc}") from None
    finally:
        if owns_client:
            client.close()


def _validate_ics(body: bytes, content_type: str) -> bytes:
    head = body.lstrip(b"\xef\xbb\xbf \r\n\t")[:64].upper()
    if not head.startswith(b"BEGIN:VCALENDAR"):
        hint = " (looks like an HTML/login page - token invalid or feeds disabled?)" if b"<" in head else ""
        raise FetchError(f"response is not an iCalendar file; content-type={content_type!r}{hint}")
    return body


def snapshot_dir(feed: FeedConfig) -> Path:
    return config.RAW_DIR / feed.institution / feed.label


def save_snapshot(feed: FeedConfig, body: bytes, *, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    path = snapshot_dir(feed) / f"{now.strftime('%Y%m%dT%H%M%SZ')}.ics"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def list_snapshots(feed: FeedConfig) -> list[Path]:
    """Snapshots for a feed, oldest first (filenames sort chronologically)."""
    directory = snapshot_dir(feed)
    return sorted(directory.glob("*.ics")) if directory.exists() else []


def snapshot_time(path: Path) -> datetime:
    return datetime.strptime(path.stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
