"""Settings for the local chat prototype, loaded from prototype/.env + environment.

Any OpenAI-compatible chat endpoint works. Presets cover free options;
model names drift over time, so LLM_MODEL can always override them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROTO_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = PROTO_DIR.parent
SPIKE_DIR = REPO_DIR / "spikes" / "brightspace_ical"

# provider -> (base_url, default model, needs a real API key)
PRESETS: dict[str, tuple[str, str, bool]] = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.5-flash", True),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", True),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:7b", False),
}


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    provider: str
    base_url: str
    model: str
    api_key: str
    timezone: str
    items_path: Path
    state_path: Path
    spike_dir: Path


def load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE loader; real environment variables take precedence."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(env_file: Path = PROTO_DIR / ".env") -> Settings:
    load_env_file(env_file)
    provider = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()
    preset = PRESETS.get(provider)
    if preset is None and provider != "custom":
        raise SettingsError(f"LLM_PROVIDER must be one of {', '.join(PRESETS)} or custom (got '{provider}')")

    base_url = os.environ.get("LLM_BASE_URL") or (preset[0] if preset else "")
    model = os.environ.get("LLM_MODEL") or (preset[1] if preset else "")
    needs_key = preset[2] if preset else True
    api_key = os.environ.get("LLM_API_KEY", "").strip()
    if api_key == "paste-your-key-here":  # untouched template value
        api_key = ""
    if not base_url or not model:
        raise SettingsError("custom provider needs LLM_BASE_URL and LLM_MODEL in prototype/.env")
    if needs_key and not api_key:
        raise SettingsError(
            f"No LLM_API_KEY set for provider '{provider}'. Copy prototype/.env.example to prototype/.env "
            "and paste your key there (see prototype/README.md for free options)."
        )

    spike_dir = Path(os.environ.get("SPIKE_DIR", SPIKE_DIR))
    return Settings(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key or "not-needed",
        timezone=os.environ.get("ASSISTANT_TIMEZONE", "America/Toronto"),
        items_path=Path(os.environ.get("ITEMS_PATH", spike_dir / "data" / "out" / "all_items.json")),
        state_path=Path(os.environ.get("STATE_PATH", PROTO_DIR / "state.json")),
        spike_dir=spike_dir,
    )
