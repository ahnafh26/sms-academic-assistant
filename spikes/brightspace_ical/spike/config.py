"""Load institution registry and local (secret) feed configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import redact

SPIKE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = SPIKE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = DATA_DIR / "out"
REPORTS_DIR = DATA_DIR / "reports"

INSTITUTIONS_FILE = SPIKE_DIR / "institutions.yaml"
FEEDS_FILE = SPIKE_DIR / "feeds.local.yaml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class InstitutionConfig:
    slug: str
    name: str
    lms_name: str
    hosts: tuple[str, ...]
    timezone: str
    verified: bool = False
    rules: dict[str, Any] = field(default_factory=dict)

    def allows_host(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.lower().rstrip(".")
        return any(host == h or host.endswith("." + h) for h in self.hosts)


@dataclass(frozen=True)
class FeedConfig:
    label: str
    institution: str
    url: str


def load_institutions(path: Path = INSTITUTIONS_FILE) -> dict[str, InstitutionConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    result: dict[str, InstitutionConfig] = {}
    for slug, item in (raw.get("institutions") or {}).items():
        result[slug] = InstitutionConfig(
            slug=slug,
            name=item["name"],
            lms_name=item.get("lms_name", "Brightspace"),
            hosts=tuple(h.lower() for h in item.get("hosts", [])),
            timezone=item.get("timezone", "America/Toronto"),
            verified=bool(item.get("verified", False)),
            rules=item.get("rules") or {},
        )
    return result


def load_feeds(
    path: Path = FEEDS_FILE, institutions: dict[str, InstitutionConfig] | None = None
) -> list[FeedConfig]:
    if not path.exists():
        raise ConfigError(
            f"{path.name} not found. Copy feeds.local.example.yaml to feeds.local.yaml "
            "and paste your feed URL(s)."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    feeds = [
        FeedConfig(label=f["label"], institution=f["institution"], url=str(f["url"]).strip())
        for f in raw.get("feeds") or []
    ]
    if institutions is not None:
        for feed in feeds:
            if feed.institution not in institutions:
                raise ConfigError(f"feed '{feed.label}': unknown institution '{feed.institution}'")
    for feed in feeds:
        redact.register_url(feed.url)
    labels = [f.label for f in feeds]
    if len(labels) != len(set(labels)):
        raise ConfigError("feed labels must be unique")
    return feeds
