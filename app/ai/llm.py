"""Thin OpenAI-compatible chat client (Gemini / Groq / Ollama / custom).

The agent depends only on the `ChatModel` protocol, so tests use a
scripted fake and the production SMS service can swap providers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    raw_arguments: str
    # the provider's tool-call object exactly as returned. Gemini 3 attaches a thought_signature
    # (extra_content.google.thought_signature) that must be sent back unchanged in the next request.
    raw: dict | None = None

    def as_message_part(self) -> dict:
        return self.raw or {"id": self.id, "type": "function",
                            "function": {"name": self.name, "arguments": self.raw_arguments}}


@dataclass
class AssistantTurn:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMError(Exception):
    pass


class ChatModel(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantTurn: ...


class OpenAICompatModel:
    def __init__(self, base_url: str, api_key: str, model: str, *, temperature: float = 0.2, timeout: float = 60):
        import openai  # imported lazily so tests don't need network config

        self._openai = openai
        # free tiers have short 503 high-demand spikes; the SDK retries 429/5xx with backoff
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=4)
        self.model = model
        self.temperature = temperature

    def complete(self, messages: list[dict], tools: list[dict]) -> AssistantTurn:
        o = self._openai
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                **({"tools": tools} if tools else {}),
            )
        except o.AuthenticationError:
            raise LLMError("the API key was rejected; check LLM_API_KEY in prototype/.env") from None
        except o.RateLimitError as exc:
            if "PerDay" in str(exc):
                raise LLMError(f"daily free-tier limit reached for {self.model}; set LLM_MODEL to another model "
                               "(e.g. gemini-flash-lite-latest) or wait until tomorrow") from None
            raise LLMError("rate limited by the provider (free tiers have per-minute limits); wait a bit") from None
        except o.NotFoundError as exc:
            raise LLMError(f"model or endpoint not found ({self.model}); check LLM_MODEL: {exc}") from None
        except o.APIConnectionError:
            raise LLMError("couldn't reach the LLM endpoint (offline, or Ollama not running?)") from None
        except o.APIStatusError as exc:
            raise LLMError(f"provider error {exc.status_code}: {exc.message}") from None

        if not resp.choices:
            raise LLMError("provider returned no choices")
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {"__invalid_json__": raw}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args if isinstance(args, dict) else {},
                                  raw_arguments=raw, raw=tc.model_dump(exclude_none=True)))
        return AssistantTurn(content=msg.content, tool_calls=calls)


# provider -> (base_url, default model, needs an API key). Model names drift; LLM_MODEL overrides.
PRESETS: dict[str, tuple[str, str, bool]] = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-flash-lite-latest", True),
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", True),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:7b", False),
}


def model_from_settings(settings) -> OpenAICompatModel | None:
    """The configured LLM, or None when it isn't set up yet (no key for a provider that needs one)."""
    provider = (settings.llm_provider or "gemini").lower()
    base, default_model, needs_key = PRESETS.get(provider, (settings.llm_base_url or "", settings.llm_model or "", True))
    key = (settings.llm_api_key or "").strip()
    if key in ("", "paste-your-key-here") and needs_key:
        return None
    base_url = settings.llm_base_url or base
    model = settings.llm_model or default_model
    if not base_url or not model:
        return None
    return OpenAICompatModel(base_url, key or "not-needed", model)
