""".eml -> RawEmail. Interprets nothing beyond decoding (that's classify.py)."""

from __future__ import annotations

import email
import email.policy
import email.utils
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

_URL = re.compile(r"https?://[^\s<>\"')\]]+")


@dataclass
class RawEmail:
    path: str | None
    message_id: str | None
    sender: str
    sender_name: str
    subject: str
    sent_at: datetime | None
    text: str
    links: list[str] = field(default_factory=list)
    was_html: bool = False


class _HTMLText(HTMLParser):
    """Visible text + hrefs from an HTML email body."""

    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith("http"):
                self.links.append(href)
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head") and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts).replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in joined.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def parse_eml(data: bytes, path: Path | None = None) -> RawEmail:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    name, addr = email.utils.parseaddr(str(msg.get("From", "")))
    sent_at = None
    if msg.get("Date"):
        try:
            sent_at = email.utils.parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            sent_at = None

    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    links: list[str] = []
    was_html = False
    if html is not None:
        parser = _HTMLText()
        parser.feed(html.get_content())
        links = parser.links
        html_text = parser.text()
        was_html = True
    text = plain.get_content().strip() if plain is not None else (html_text if html is not None else "")
    links += [u for u in _URL.findall(text) if u not in links]

    return RawEmail(
        path=str(path) if path else None,
        message_id=str(msg.get("Message-ID", "")).strip() or None,
        sender=addr.lower(),
        sender_name=name,
        subject=re.sub(r"\s+", " ", str(msg.get("Subject", ""))).strip(),
        sent_at=sent_at,
        text=text,
        links=[link.rstrip(".,;") for link in links],
        was_html=was_html,
    )


def load_inbox(directory: Path) -> list[RawEmail]:
    """Every .eml under directory (recursively), oldest first."""
    emails = [parse_eml(p.read_bytes(), p) for p in sorted(directory.rglob("*.eml"))]
    return sorted(emails, key=lambda e: e.sent_at.timestamp() if e.sent_at else 0)
