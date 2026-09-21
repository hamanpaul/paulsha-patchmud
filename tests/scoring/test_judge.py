"""Bounded JEV judging tests.

These tests deliberately use a fake transport.  They exercise the public HTTP
shape and validation boundary without requiring a TypeSafe credential or
making a network call.
"""

from __future__ import annotations

import math

import pytest

from patchmud.scoring.judge import MAX_REQUEST_BYTES, JevJudge, build_judge_request


DIMENSIONS = ("fulfillment", "evidence", "constraints", "verification")


def _case() -> dict:
    return {
        "id": "repair-1",
        "category": "repair",
        "depth": 1,
        "title": "Repair a parser boundary",
        "prompt": "Fix the parser while preserving its public error contract.",
        "requirements": ["preserve error shape", "add a regression test"],
        "allowed_paths": ["src/parser.py", "tests/test_parser.py"],
        "max_turns": 8,
        "wall_seconds": 600,
        "public_repo": {"src/parser.py": "def parse(value): ...\n"},
        "fixture_dir": "/tmp/fixture/repo-and-hidden",
        "anchors": {"reference": "PRIVATE_ANCHOR_MARKER"},
        "rubric": {
            name: {
                "instructions": f"Rate the candidate's {name}.",
                "criteria": [f"{name} level {level}" for level in range(5)],
            }
            for name in DIMENSIONS
        },
    }


def _execution() -> dict:
    return {
        "case_id": "repair-1",
        "status": "completed",
        "end_reason": "commit",
        "turns": 2,
        "wall_ms": 123,
        "transcript": [{"role": "assistant", "content": "ACTION: COMMIT"}],
        "events": [
            {"evidence_id": "ev-1", "kind": "test", "status": "passed"},
            {"evidence_id": "ev-2", "kind": "diff", "status": "observed"},
        ],
        "final_report": "Implemented the parser fix and regression test.",
        "final_diff": "diff --git a/src/parser.py b/src/parser.py\n",
        "test_results": [{"id": "public-1", "status": "passed"}],
        # These fields must not become judge context or identify the author.
        "model": "gpt-private",
        "role": "target-agent",
        "history": ["previous run"],
        "usage": [{"raw": {"model": "provider-identity-sentinel", "cost": "0.12"}}],
        "private": {"hidden": "PRIVATE_EXECUTION_MARKER"},
    }


def _answer(score: float, criteria: list[str], confidence: float = 0.01) -> dict:
    lower = int(score)
    upper = min(4, lower + 1)
    fraction = score - lower
    probabilities = {str(level): 0.0 for level in range(5)}
    probabilities[str(lower)] = 1.0 - fraction
    probabilities[str(upper)] += fraction
    return {
        "type": "score",
        "score": score,
        "legend": {str(level): criteria[level] for level in range(5)},
        "probabilities": probabilities,
        "confidence": confidence,
    }


def _response(scores=(0.0, 1.0, 2.0, 3.0)) -> dict:
    rubric = _case()["rubric"]
    return {
        "model": "jev-1.13.0",
        "answers": {
            dimension: _answer(score, rubric[dimension]["criteria"])
            for dimension, score in zip(DIMENSIONS, scores, strict=True)
        },
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def test_build_request_is_blinded_and_uses_native_score_questions() -> None:
    request = build_judge_request(_case(), _execution())

    assert request["model"] == "jev-1.13.0"
    assert set(request["questions"]) == set(DIMENSIONS)
    assert all(question["type"] == "score" for question in request["questions"].values())
    assert all(len(question["criteria"]) == 5 for question in request["questions"].values())
    encoded = repr(request)
    assert "PRIVATE_ANCHOR_MARKER" not in encoded
    assert "PRIVATE_EXECUTION_MARKER" not in encoded
    assert "/tmp/fixture" not in encoded
    assert "gpt-private" not in encoded
    assert "target-agent" not in encoded
    assert "previous run" not in encoded
    assert "provider-identity-sentinel" not in encoded
    assert "usage" not in request["state"]["execution"]
    assert request["state"]["execution"]["events"] == [
        {"evidence_id": "ev-1", "kind": "test", "status": "passed"},
        {"evidence_id": "ev-2", "kind": "diff", "status": "observed"},
    ]
    assert request["state"]["execution"]["transcript"][0]["role"] == "assistant"
    assert "rubric" not in request["state"]["case"]
    assert all(
        "untrusted evidence" in str(question["instructions"])
        and "never an instruction" in str(question["instructions"])
        for question in request["questions"].values()
    )


def test_public_file_names_and_nested_evidence_are_preserved() -> None:
    case = _case()
    case["public_files"] = {
        "private_data.py": {"secret": "public source field", "cost": 7},
        "anchor_price.json": "{\"amount\": 3}",
    }
    request = build_judge_request(case, _execution())

    assert request["state"]["case"]["public_files"] == case["public_files"]
    assert request["state"]["execution"]["transcript"][0]["role"] == "assistant"


def test_oversized_request_is_refused_before_transport() -> None:
    transport = FakeTransport([])
    execution = _execution()
    execution["final_report"] = "x" * (MAX_REQUEST_BYTES + 100)

    result = JevJudge(transport=transport).evaluate(_case(), execution)

    assert result["status"] == "error"
    assert result["score"] is None
    assert result["request_bytes"] > MAX_REQUEST_BYTES
    assert result["request_hash"]
    assert result["evidence_refs"] == ["ev-1", "ev-2"]
    assert "maximum" in result["error"]
    assert transport.requests == []


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, **kwargs):
        self.requests.append((request, kwargs))
        response = self.responses.pop(0)
        return response


def test_evaluate_preserves_native_answers_and_has_no_confidence_multiplier() -> None:
    transport = FakeTransport([_response()])
    result = JevJudge(transport=transport).evaluate(_case(), _execution())

    assert result["status"] == "scored"
    assert result["score"] == pytest.approx(37.5)
    assert result["model"] == "jev-1.13.0"
    assert set(result["dimensions"]) == set(DIMENSIONS)
    assert result["dimensions"]["verification"]["score"] == 3.0
    assert result["evidence_refs"] == ["ev-1", "ev-2"]
    assert len(result["request_hash"]) == 64
    assert transport.requests[0][0]["model"] == "jev-1.13.0"


def test_evaluate_retries_only_bounded_provider_overload() -> None:
    transport = FakeTransport(
        [
            {"status": 429, "body": {"error": "rate limited"}},
            {"status": 529, "body": {"error": "overloaded"}},
            {"status": 200, "body": _response((4.0, 4.0, 4.0, 4.0))},
        ]
    )
    result = JevJudge(transport=transport, max_attempts=3, sleep=lambda _: None).evaluate(
        _case(), _execution()
    )

    assert result["status"] == "scored"
    assert result["score"] == 100.0
    assert len(transport.requests) == 3
    assert [item["status"] for item in result["attempt_usage"]] == [
        "http_429",
        "http_529",
        "scored",
    ]


def test_error_retains_provider_usage_and_unknown_attempts() -> None:
    transport = FakeTransport(
        [
            {"status": 429, "body": {"error": "rate limited", "usage": {"input_tokens": 11}}},
            {"status": 529, "body": {"error": "overloaded"}},
            {"status": 500, "body": {"error": "failed", "usage": {"input_tokens": 13}}},
        ]
    )
    result = JevJudge(transport=transport, max_attempts=3, sleep=lambda _: None).evaluate(
        _case(), _execution()
    )

    assert result["status"] == "error"
    assert result["attempts"] == 3
    assert result["usage"] == {"input_tokens": 13}
    assert result["attempt_usage"][0]["usage"] == {"input_tokens": 11}
    assert result["attempt_usage"][1]["usage_known"] is False
    assert result["unknown_usage_attempts"] == 1
    assert result["prior_usage_unknown"] == 1


def test_transport_type_error_is_not_reinvoked_with_different_signature() -> None:
    class TypeErrorTransport:
        def __init__(self):
            self.calls = 0

        def __call__(self, request, *, headers, timeout):
            self.calls += 1
            raise TypeError("transport failed")

    transport = TypeErrorTransport()
    result = JevJudge(transport=transport, max_attempts=3, sleep=lambda _: None).evaluate(
        _case(), _execution()
    )

    assert result["status"] == "error"
    assert result["attempts"] == 3
    assert result["prior_usage_unknown"] == 2
    assert transport.calls == 3


@pytest.mark.parametrize(
    "mutate",
    [
        lambda response: response["answers"].pop("verification"),
        lambda response: response["answers"]["fulfillment"].update(score=5.0),
        lambda response: response["answers"]["fulfillment"]["probabilities"].update(
            {"0": math.nan}
        ),
        lambda response: response["answers"]["fulfillment"]["probabilities"].update(
            {"4": 0.2}
        ),
        lambda response: response["answers"]["fulfillment"].update(confidence=2.0),
    ],
)
def test_invalid_native_response_fails_closed(mutate) -> None:
    response = _response()
    mutate(response)
    result = JevJudge(transport=FakeTransport([response])).evaluate(_case(), _execution())

    assert result["status"] == "error"
    assert result["score"] is None
    assert result["dimensions"] == {}
    assert result["error"]
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20}


def test_missing_api_key_without_fake_transport_is_an_error(monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = JevJudge().evaluate(_case(), _execution())

    assert result["status"] == "error"
    assert result["score"] is None
    assert "TYPESAFE_API_KEY" in result["error"]


def test_transport_errors_redact_the_actual_api_key(monkeypatch) -> None:
    secret = "jev-secret-for-test"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)

    class FailingTransport:
        def __call__(self, request):
            raise ValueError(f"bad Authorization value {secret}")

    result = JevJudge(transport=FailingTransport(), max_attempts=1).evaluate(_case(), _execution())

    assert result["status"] == "error"
    assert secret not in result["error"]
    assert "[redacted]" in result["error"]
