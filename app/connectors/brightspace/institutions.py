"""Registry of supported Brightspace schools (institutions.yaml)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REGISTRY = Path(__file__).with_name("institutions.yaml")


@dataclass(frozen=True)
class InstitutionConfig:
    slug: str
    name: str
    lms_name: str
    hosts: tuple[str, ...]
    timezone: str
    verified: bool = False
    aliases: tuple[str, ...] = ()
    rules: dict[str, Any] = field(default_factory=dict)

    def allows_host(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.lower().rstrip(".")
        return any(host == h or host.endswith("." + h) for h in self.hosts)


@lru_cache
def load_institutions(path: Path = REGISTRY) -> dict[str, InstitutionConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        slug: InstitutionConfig(
            slug=slug,
            name=item["name"],
            lms_name=item.get("lms_name", "Brightspace"),
            hosts=tuple(h.lower() for h in item.get("hosts", [])),
            timezone=item.get("timezone", "America/Toronto"),
            verified=bool(item.get("verified", False)),
            aliases=tuple(a.lower() for a in item.get("aliases", [])),
            rules=item.get("rules") or {},
        )
        for slug, item in (raw.get("institutions") or {}).items()
    }


def get_institution(slug: str) -> InstitutionConfig:
    return load_institutions()[slug]


def match_institution(text: str) -> list[InstitutionConfig]:
    """Schools a student's free-text answer could mean, best first ("uw" -> Waterloo).

    Exact alias or name matches win; otherwise any alias appearing as a word
    in the text. Several results mean the answer was ambiguous.
    """
    t = re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower().replace("’", "'")).strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return []
    insts = list(load_institutions().values())
    exact = [i for i in insts if t in i.aliases or t == i.name.lower()]
    if exact:
        return exact
    words = f" {t} "
    hits = [i for i in insts if any(len(a) >= 3 and f" {a} " in words for a in i.aliases)]
    if hits:
        return hits
    return [i for i in insts if len(t) >= 4 and (t in i.name.lower() or any(a.startswith(t) for a in i.aliases))]
