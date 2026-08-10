"""Shared AI types: intents, sentiments, and the structured classification output.

These types form the contract between the Groq provider (which parses the
model's JSON output) and the AI Service (which executes tools and builds the
final reply). Nothing here touches the database or the API layer.
"""

from __future__ import annotations

import enum
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class CustomerIntent(enum.StrEnum):
    """Customer intents the AI can identify in a message."""

    GENERAL_QUESTION = "general_question"
    KNOWLEDGE_BASE_QUERY = "knowledge_base_query"
    PRODUCT_SEARCH = "product_search"
    PRODUCT_RECOMMENDATION = "product_recommendation"
    ORDER_STATUS = "order_status"
    ORDER_TRACKING = "order_tracking"
    ORDER_CANCELLATION = "order_cancellation"
    RETURN_REQUEST = "return_request"
    REFUND_REQUEST = "refund_request"
    ACCOUNT_HELP = "account_help"
    COMPLAINT = "complaint"
    SUPPORT_REQUEST = "support_request"
    HUMAN_HANDOFF = "human_handoff"
    UNKNOWN = "unknown"


class CustomerSentiment(enum.StrEnum):
    """Basic customer sentiment buckets, used as handoff context."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    FRUSTRATED = "frustrated"
    ANGRY = "angry"


def _coerce_enum(value: Any, enum_cls: type[enum.StrEnum]) -> Any:
    """Accept enum values or names (case-insensitive) as emitted by the model."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        candidate = value.strip().lower()
        try:
            return enum_cls(candidate)
        except ValueError:
            pass
        try:
            return enum_cls[candidate.upper()]
        except KeyError:
            pass
    raise ValueError(f"Invalid value for {enum_cls.__name__}: {value!r}")


class ToolRequest(BaseModel):
    """A single tool invocation requested by the model.

    Arguments are validated per-tool at execution time by the AI Service —
    the model never passes unchecked values into the database.
    """

    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def _coerce_arguments(cls, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}


class IntentClassification(BaseModel):
    """Structured output of the intent-detection stage."""

    intent: CustomerIntent
    sentiment: CustomerSentiment
    confidence: float = Field(default=0.5)
    requires_human: bool = False
    tool_request: ToolRequest | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_enums(cls, data: Any) -> Any:
        if isinstance(data, dict):
            for key in ("intent", "sentiment"):
                if key in data and data[key] is not None:
                    try:
                        enum_cls = CustomerIntent if key == "intent" else CustomerSentiment
                        data[key] = _coerce_enum(data[key], enum_cls)
                    except ValueError:
                        data[key] = None  # fails field validation below
        return data

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp_confidence(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, number))
