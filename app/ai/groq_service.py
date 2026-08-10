"""Groq provider: the only LLM backend, isolated inside the AI layer.

This service owns every interaction with the Groq API — JSON-mode structured
output and the mapping of transport failures (auth, rate limits, timeouts,
network, invalid responses) to a single ``GroqServiceError``. Callers (the AI
Service) convert that error into a safe customer-facing fallback; nothing
Groq-specific ever reaches the API layer.
"""

import json
import logging
from typing import Any

from groq import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    Groq,
    RateLimitError,
)
from pydantic import ValidationError

from app.ai.prompts import (
    build_classification_system_prompt,
    build_response_system_prompt,
)
from app.ai.types import IntentClassification
from app.core.config import get_settings

logger = logging.getLogger(__name__)

GROQ_TRANSPORT_ERRORS = (
    AuthenticationError,
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
    BadRequestError,
    APIError,
)


class GroqServiceError(Exception):
    """Raised when Groq cannot produce a usable response.

    Never surfaced to customers — the AI Service catches it and returns a
    graceful fallback message instead.
    """


class GroqService:
    """Thin wrapper around the Groq chat-completions API.

    ``client`` may be injected (tests use a fake). When omitted, the client is
    built lazily from ``GROQ_API_KEY`` so the app runs fine without a key until
    a real call is actually attempted — the call then degrades to the
    AI Service fallback instead of crashing.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._timeout = timeout
        self.settings = get_settings()
        self.model = model or self.settings.GROQ_MODEL

    # ------------------------------------------------------------------ #
    # Public stages
    # ------------------------------------------------------------------ #
    def classify_intent(
        self,
        *,
        subject: str | None,
        history: list[dict[str, str]],
    ) -> IntentClassification:
        """Ask Groq to classify intent/sentiment/tool request (JSON mode)."""
        content = self._complete(build_classification_system_prompt(subject), history)
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return IntentClassification.model_validate(data)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            raise GroqServiceError("Groq returned an invalid classification") from exc

    def generate_response(
        self,
        *,
        subject: str | None,
        history: list[dict[str, str]],
        tool_result: str | None,
    ) -> str:
        """Ask Groq to write the final customer-facing message (JSON mode)."""
        messages = self._messages(build_response_system_prompt(subject), history)
        if tool_result:
            messages.append({"role": "user", "content": f"[Tool result]\n{tool_result}"})
        content = self._complete_from(messages)
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            message = str(data.get("message", "")).strip()
            if not message:
                raise ValueError("missing message")
            return message
        except (json.JSONDecodeError, ValueError) as exc:
            raise GroqServiceError("Groq returned an invalid response") from exc

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        api_key = self._api_key or self.settings.GROQ_API_KEY
        if not api_key:
            raise GroqServiceError("GROQ_API_KEY is not configured")
        return Groq(
            api_key=api_key,
            timeout=self._timeout or self.settings.GROQ_TIMEOUT_SECONDS,
        )

    def _complete(self, system_prompt: str, history: list[dict[str, str]]) -> str:
        return self._complete_from(self._messages(system_prompt, history))

    @staticmethod
    def _messages(
        system_prompt: str, history: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        return [{"role": "system", "content": system_prompt}, *history]

    def _complete_from(self, messages: list[dict[str, str]]) -> str:
        try:
            client = self._ensure_client()
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=700,
            )
        except GroqServiceError:
            raise
        except GROQ_TRANSPORT_ERRORS as exc:
            logger.warning("Groq request failed: %s", exc)
            raise GroqServiceError("Groq request failed") from exc
        except Exception as exc:  # pragma: no cover - defensive, unknown client errors
            logger.exception("Unexpected error while calling Groq")
            raise GroqServiceError("Groq request failed") from exc

        try:
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise GroqServiceError("Groq returned an empty response") from exc
        if not content or not content.strip():
            raise GroqServiceError("Groq returned an empty response")
        return content


def build_groq_service() -> GroqService:
    """Return a GroqService configured from the environment."""
    return GroqService()
