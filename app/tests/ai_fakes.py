"""Shared fakes for AI tests — Groq is always mocked, never called for real.

The suite must work without a GROQ_API_KEY: every test injects a
``FakeGroqClient`` (or exercises the fallback path with no client at all).
"""

import json
from types import SimpleNamespace


class FakeGroqClient:
    """Stand-in for ``groq.Groq``: records requests, returns queued contents.

    ``contents`` is a FIFO of raw content strings (one per call, in call
    order: first the classification, then the response). ``error`` raises the
    given exception on every call.
    """

    def __init__(self, contents=None, error=None) -> None:
        self._contents = list(contents or [])
        self._error = error
        self.requests: list[dict] = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self._error is not None:
            raise self._error
        content = self._contents.pop(0) if self._contents else ""
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def classify_json(
    intent,
    *,
    sentiment="neutral",
    confidence=0.9,
    requires_human=False,
    tool=None,
) -> str:
    """Build a valid classification JSON string for the fake client."""
    payload = {
        "intent": intent,
        "sentiment": sentiment,
        "confidence": confidence,
        "requires_human": requires_human,
        "tool_request": tool,
    }
    return json.dumps(payload)


def respond_json(message: str) -> str:
    """Build a valid response-stage JSON string for the fake client."""
    return json.dumps({"message": message})
