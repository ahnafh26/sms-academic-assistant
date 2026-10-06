"""Fetch a Brightspace calendar feed safely (ported from the iCal spike)."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

import httpx

from .institutions import InstitutionConfig
from .redact import redact, safe_url

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 15.0
MAX_REDIRECTS = 3
USER_AGENT = "sms-academic-assistant/0.1 (calendar sync)"


class FetchError(Exception):
    def __init__(self, message: str):
        super().__init__(redact(message))


def validate_feed_url(url: str, inst: InstitutionConfig) -> str | None:
    """A student-facing problem with a pasted feed link, or None when it looks right."""
    text = (url or "").strip()
    if not text:
        return "Paste the link you copied from Subscribe."
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "That doesn't look like a link. Copy the whole thing from the Subscribe box, starting with https://"
    if parts.scheme != "https":
        return "The link should start with https://. Copy it again from Subscribe."
    if not inst.allows_host(parts.hostname):
        return (f"This link is from {parts.hostname}, but {inst.lms_name} links start with {inst.hosts[0]}. "
                f"Check that you copied it from {inst.lms_name}.")
    if "/d2l/le/calendar/feed/" not in parts.path:
        return f"That's a {inst.lms_name} link, but not the calendar feed. In Calendar, tap Subscribe and copy that link."
    if "token=" not in (parts.query or ""):
        return "The link is missing its private token at the end. Copy the full link from the Subscribe box."
    return None


def check_url(url: str, inst: InstitutionConfig) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(f"refusing non-HTTPS feed URL: {safe_url(url)}")
    if not inst.allows_host(parts.hostname):
        raise FetchError(f"host '{parts.hostname}' is not allowed for {inst.slug}")


def fetch_feed(url: str, inst: InstitutionConfig, *, client: httpx.Client | None = None) -> bytes:
    """GET the feed, following redirects only within the school's hosts."""
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            check_url(url, inst)
            with client.stream("GET", url, follow_redirects=False) as resp:
                if resp.is_redirect:
                    url = urljoin(url, resp.headers.get("location", ""))
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code} from {safe_url(url)}")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError(f"feed exceeds {MAX_BYTES} bytes")
                head = bytes(body).lstrip(b"\xef\xbb\xbf \r\n\t")[:64].upper()
                if not head.startswith(b"BEGIN:VCALENDAR"):
                    # real case: an invalid LEARN token returns 200 with an HTML login page
                    raise FetchError("response is not a calendar (link expired, reset, or feeds turned off?)")
                return bytes(body)
        raise FetchError(f"too many redirects (> {MAX_REDIRECTS})")
    except httpx.HTTPError as exc:
        raise FetchError(f"network error: {exc}") from None
    finally:
        if owns_client:
            client.close()
