"""TypeSafe（JEV）adapter：request 組裝、重試語意、成本換算，全程 fake transport。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from patchmud.judge.structured import ChoiceQuestion, JudgeError, StructuredRequest
from patchmud.judge.typesafe import (
    JEV_DEFAULT_MODEL,
    JEV_ENDPOINT,
    TypeSafeHttpReply,
    TypeSafeJudgeAdapter,
)

REQUEST = StructuredRequest(
    state={"acceptance_criterion": "x", "evidence": []},
    questions={"verdict": ChoiceQuestion(instructions="Decide.", criteria={"yes": None, "no": None})},
)
OK_BODY = {
    "model": "jev-1.13.0",
    "answers": {"verdict": {"type": "choice", "choice": "no", "confidence": 0.9, "probabilities": {"yes": 0.05, "no": 0.95}}},
    "usage": {"input_tokens": 1000, "output_tokens": 20},
}
ENV = {"TYPESAFE_API_KEY": "test-key-not-real"}


class FakeTransport:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, url, headers, payload):
        self.calls.append((url, headers, payload))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        self.now += 0.25
        return self.now


def _adapter(transport, sleeps=None, **kwargs):
    return TypeSafeJudgeAdapter(
        transport=transport,
        clock=FakeClock(),
        sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
        environ=ENV,
        **kwargs,
    )


def test_request_shape_pins_model_and_uses_bearer_key():
    transport = FakeTransport([TypeSafeHttpReply(200, OK_BODY)])
    response = _adapter(transport).judge(REQUEST)
    url, headers, payload = transport.calls[0]
    assert url == JEV_ENDPOINT
    assert headers["Authorization"] == "Bearer test-key-not-real"
    assert payload["model"] == JEV_DEFAULT_MODEL == "jev-1.13.0"
    assert payload["state"] == REQUEST.state
    assert payload["questions"]["verdict"]["type"] == "choice"
    assert response.answers["verdict"].choice == "no"
    assert response.model == "jev-1.13.0"
    assert response.wall_ms == 250
    assert response.attempts == 1


def test_cost_is_list_price_per_input_token():
    response = _adapter(FakeTransport([TypeSafeHttpReply(200, OK_BODY)])).judge(REQUEST)
    assert response.cost.usd == Decimal("0.000042")
    assert response.cost.to_dict()["usd"] == "0.000042"
    assert "list_price" in response.cost.basis
    assert response.usage == {"input_tokens": 1000, "output_tokens": 20}


def test_missing_key_is_a_config_error_and_sends_nothing():
    transport = FakeTransport([])
    adapter = TypeSafeJudgeAdapter(transport=transport, environ={})
    with pytest.raises(JudgeError) as exc:
        adapter.judge(REQUEST)
    assert exc.value.kind == "config"
    assert transport.calls == []


def test_429_and_529_retry_with_backoff_honoring_retry_after():
    sleeps = []
    transport = FakeTransport(
        [
            TypeSafeHttpReply(429, {"error": "rate"}, {"retry-after": "3"}),
            TypeSafeHttpReply(529, {"error": "busy"}),
            TypeSafeHttpReply(200, OK_BODY),
        ]
    )
    response = _adapter(transport, sleeps).judge(REQUEST)
    assert sleeps == [3.0, 2.0]
    assert response.attempts == 3


def test_non_retryable_http_error_raises_transport_without_retry():
    transport = FakeTransport([TypeSafeHttpReply(422, {"detail": "bad question"})])
    with pytest.raises(JudgeError) as exc:
        _adapter(transport).judge(REQUEST)
    assert exc.value.kind == "transport" and exc.value.status == 422
    assert len(transport.calls) == 1


def test_retry_budget_is_bounded():
    transport = FakeTransport([TypeSafeHttpReply(429, {})] * 2)
    with pytest.raises(JudgeError) as exc:
        _adapter(transport, max_attempts=2).judge(REQUEST)
    assert exc.value.status == 429 and exc.value.retryable


def test_invalid_answer_is_not_retried():
    body = dict(OK_BODY, answers={"verdict": {"choice": "maybe"}})
    transport = FakeTransport([TypeSafeHttpReply(200, body)])
    with pytest.raises(JudgeError) as exc:
        _adapter(transport).judge(REQUEST)
    assert exc.value.kind == "invalid_output"
    assert len(transport.calls) == 1
