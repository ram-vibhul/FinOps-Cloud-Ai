"""Rewrite factual summaries in plain words with Claude. Never invents numbers."""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol

from ccradar.config import Settings

log = logging.getLogger(__name__)

SYSTEM = (
    "You rewrite cloud cost findings for busy engineers and finance people. Use only the "
    "facts given; never add numbers, causes or advice that are not in them. Two to four "
    "short sentences, plain words, no headings, no lists, no markdown."
)


class TextWriter(Protocol):
    def rewrite(self, facts: str, system: str | None = None) -> str | None: ...


class StructuredWriter(Protocol):
    def structured(
        self, prompt: str, system: str, schema: dict[str, Any]
    ) -> dict[str, Any] | None: ...


class AnthropicWriter:
    def __init__(self, settings: Settings) -> None:
        import anthropic

        assert settings.anthropic_api_key is not None  # noqa: S101
        self._anthropic = anthropic
        self._model = settings.ai_model
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key.get_secret_value(), timeout=30.0, max_retries=1
        )

    def rewrite(self, facts: str, system: str | None = None) -> str | None:
        a = self._anthropic
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system or SYSTEM,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": facts}],
            )
        except (a.APIStatusError, a.APIConnectionError) as error:
            log.warning("AI wording failed: %s", type(error).__name__)
            return None
        if response.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None

    def structured(self, prompt: str, system: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        """One JSON object matching ``schema`` (structured output), or None."""
        a = self._anthropic
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=2048,
                system=system,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": schema},
                },
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt}],
            )
        except (a.APIStatusError, a.APIConnectionError) as error:
            log.warning("AI query failed: %s", type(error).__name__)
            return None
        if response.stop_reason in ("refusal", "max_tokens"):
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        try:
            out = json.loads(text)
        except ValueError:
            return None
        return out if isinstance(out, dict) else None


def build_writer(settings: Settings) -> TextWriter | None:
    return AnthropicWriter(settings) if settings.ai_enabled else None
