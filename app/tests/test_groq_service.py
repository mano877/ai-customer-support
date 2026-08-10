"""Tests for the Groq provider — every call is mocked, no real API, no key."""

import json

import pytest
from groq import APIConnectionError, APIError, APITimeoutError, RateLimitError

from app.ai.groq_service import GroqService, GroqServiceError
from app.ai.types import CustomerIntent, CustomerSentiment
from app.tests.ai_fakes import FakeGroqClient


def _error(cls):
    """Build an SDK exception instance without depending on constructor args."""
    instance = cls.__new__(cls)
    instance.args = ("boom",)
    instance.message = "boom"
    return instance


def _service(client) -> GroqService:
    return GroqService(client=client, model="test-model")


def _history() -> list[dict[str, str]]:
    return [{"role": "user", "content": "Where is my order?"}]


class TestClassifyIntent:
    def test_parses_structured_output(self):
        client = FakeGroqClient(
            [
                json.dumps(
                    {
                        "intent": "order_status",
                        "sentiment": "frustrated",
                        "confidence": 0.9,
                        "requires_human": False,
                        "tool_request": None,
                    }
                )
            ]
        )
        classification = _service(client).classify_intent(subject=None, history=_history())
        assert classification.intent == CustomerIntent.ORDER_STATUS
        assert classification.sentiment == CustomerSentiment.FRUSTRATED
        assert classification.confidence == 0.9
        assert classification.requires_human is False
        assert classification.tool_request is None

    def test_accepts_uppercase_names_and_clamps_confidence(self):
        client = FakeGroqClient(
            [
                json.dumps(
                    {
                        "intent": "ORDER_STATUS",
                        "sentiment": "ANGRY",
                        "confidence": 1.7,
                        "requires_human": "true",
                    }
                )
            ]
        )
        classification = _service(client).classify_intent(subject=None, history=_history())
        assert classification.intent == CustomerIntent.ORDER_STATUS
        assert classification.sentiment == CustomerSentiment.ANGRY
        assert classification.confidence == 1.0
        assert classification.requires_human is True

    def test_parses_tool_request(self):
        client = FakeGroqClient(
            [
                json.dumps(
                    {
                        "intent": "knowledge_base_query",
                        "sentiment": "neutral",
                        "confidence": 0.8,
                        "requires_human": False,
                        "tool_request": {
                            "name": "knowledge_search",
                            "arguments": {"q": "return policy"},
                        },
                    }
                )
            ]
        )
        classification = _service(client).classify_intent(subject=None, history=_history())
        assert classification.tool_request is not None
        assert classification.tool_request.name == "knowledge_search"
        assert classification.tool_request.arguments == {"q": "return policy"}

    def test_invalid_json_raises(self):
        client = FakeGroqClient(["this is not json"])
        with pytest.raises(GroqServiceError):
            _service(client).classify_intent(subject=None, history=_history())

    def test_wrong_shape_raises(self):
        client = FakeGroqClient(["[1, 2, 3]"])
        with pytest.raises(GroqServiceError):
            _service(client).classify_intent(subject=None, history=_history())

    def test_invalid_intent_value_raises(self):
        client = FakeGroqClient(
            [json.dumps({"intent": "teleport", "sentiment": "neutral"})]
        )
        with pytest.raises(GroqServiceError):
            _service(client).classify_intent(subject=None, history=_history())

    def test_empty_content_raises(self):
        client = FakeGroqClient([""])
        with pytest.raises(GroqServiceError):
            _service(client).classify_intent(subject=None, history=_history())

    def test_request_uses_json_mode_and_bounded_messages(self):
        client = FakeGroqClient(
            [json.dumps({"intent": "unknown", "sentiment": "neutral"})]
        )
        service = _service(client)
        history = _history() * 5
        service.classify_intent(subject="Order help", history=history)

        request = client.requests[0]
        assert request["model"] == "test-model"
        assert request["response_format"] == {"type": "json_object"}
        assert request["messages"][0]["role"] == "system"
        assert "Order help" in request["messages"][0]["content"]
        assert request["messages"][1:] == history


class TestGenerateResponse:
    def test_returns_message(self):
        client = FakeGroqClient([json.dumps({"message": "Your order is on its way."})])
        message = _service(client).generate_response(
            subject=None, history=_history(), tool_result=None
        )
        assert message == "Your order is on its way."

    def test_appends_tool_result_when_provided(self):
        client = FakeGroqClient([json.dumps({"message": "ok"})])
        service = _service(client)
        service.generate_response(
            subject=None,
            history=_history(),
            tool_result="Order ORD-123: status pending",
        )
        request = client.requests[0]
        assert "[Tool result]" in request["messages"][-1]["content"]

    def test_missing_message_key_raises(self):
        client = FakeGroqClient([json.dumps({"reply": "hi"})])
        with pytest.raises(GroqServiceError):
            _service(client).generate_response(
                subject=None, history=_history(), tool_result=None
            )

    def test_invalid_json_raises(self):
        client = FakeGroqClient(["nope"])
        with pytest.raises(GroqServiceError):
            _service(client).generate_response(
                subject=None, history=_history(), tool_result=None
            )

    def test_blank_message_raises(self):
        client = FakeGroqClient([json.dumps({"message": "   "})])
        with pytest.raises(GroqServiceError):
            _service(client).generate_response(
                subject=None, history=_history(), tool_result=None
            )


class TestErrorHandling:
    def test_missing_api_key_raises(self):
        # No injected client and no key configured → call fails cleanly.
        service = GroqService(model="test-model")
        with pytest.raises(GroqServiceError):
            service.classify_intent(subject=None, history=_history())

    @pytest.mark.parametrize(
        "error_cls",
        [APIError, APIConnectionError, APITimeoutError, RateLimitError],
    )
    def test_transport_errors_become_groq_service_error(self, error_cls):
        client = FakeGroqClient(error=_error(error_cls))
        with pytest.raises(GroqServiceError):
            _service(client).classify_intent(subject=None, history=_history())

    def test_empty_response_raises(self):
        client = FakeGroqClient([])  # no queued content → empty content
        with pytest.raises(GroqServiceError):
            _service(client).classify_intent(subject=None, history=_history())
