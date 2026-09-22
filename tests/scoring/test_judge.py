"""Bounded JEV judging tests.

These tests deliberately use a fake transport.  They exercise the public HTTP
shape and validation boundary without requiring a TypeSafe credential or
making a network call.
"""

from __future__ import annotations

import copy
import hashlib
import math

import pytest

from patchmud.scoring.evidence_views import JUDGE_PROTOCOL_VERSION
from patchmud.scoring.judge import (
    MAX_REQUEST_BYTES,
    JevJudge,
    _canonical_json,
    _rehydrate_shared_text,
    build_judge_request,
)


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


def test_build_request_preserves_native_execution_policy_and_phase_shape() -> None:
    case = _case()
    case.update(
        {
            "max_turns": None,
            "stages": [{"phase": 1, "message": "run the validation stage"}],
            "execution_policy": {
                "protocol": "native-engineering-v1",
                "tools": "native-cli",
                "turn_limit": None,
            },
        }
    )

    request = build_judge_request(case, _execution())

    assert request["state"]["case"]["max_turns"] is None
    assert request["state"]["case"]["stages"] == [
        {"phase": 1, "message": "run the validation stage"}
    ]
    assert request["state"]["case"]["execution_policy"] == case["execution_policy"]


def test_public_file_names_and_nested_evidence_are_preserved() -> None:
    case = _case()
    case["public_files"] = {
        "private_data.py": {"secret": "public source field", "cost": 7},
        "anchor_price.json": "{\"amount\": 3}",
    }
    request = build_judge_request(case, _execution())

    assert request["state"]["case"]["public_files"] == case["public_files"]
    assert request["state"]["execution"]["transcript"][0]["role"] == "assistant"


def test_repeated_state_text_is_lossless_and_does_not_mutate_execution() -> None:
    execution = _execution()
    repeated = "candidate evidence line\n" * 20
    execution["final_report"] = repeated
    execution["events"] = [
        {"evidence_id": "ev-1", "content": repeated},
        {"evidence_id": "ev-2", "content": repeated},
    ]
    original = copy.deepcopy(execution)

    request = build_judge_request(_case(), execution)
    state = request["state"]

    assert execution == original
    assert isinstance(state.get("shared_text"), dict)
    refs = [
        state["execution"]["final_report"],
        state["execution"]["events"][0]["content"],
        state["execution"]["events"][1]["content"],
    ]
    assert refs[0] == refs[1] == refs[2]
    assert set(refs[0]) == {"shared_text_ref"}
    restored = _rehydrate_shared_text(state)
    expected = build_judge_request(_case(), _execution())["state"]
    expected["execution"]["final_report"] = repeated
    expected["execution"]["events"] = [
        {"evidence_id": "ev-1", "content": repeated},
        {"evidence_id": "ev-2", "content": repeated},
    ]
    assert restored == expected


def test_repeated_large_state_text_reduces_actual_payload() -> None:
    execution = _execution()
    repeated = "same evidence payload " * 30
    execution["final_report"] = repeated
    execution["events"] = [{"content": repeated}, {"content": repeated}]

    request = build_judge_request(_case(), execution)
    compressed_state = request["state"]
    uncompressed_state = _rehydrate_shared_text(compressed_state)

    assert len(_canonical_json(compressed_state)) < len(_canonical_json(uncompressed_state))


def test_distinct_long_text_with_same_prefix_is_not_merged() -> None:
    execution = _execution()
    prefix = "same prefix " * 30
    first = prefix + "one"
    second = prefix + "two"
    execution["events"] = [{"content": first}, {"content": second}]

    request = build_judge_request(_case(), execution)

    assert "shared_text" not in request["state"]
    assert request["state"]["execution"]["events"][0]["content"] == first
    assert request["state"]["execution"]["events"][1]["content"] == second


def test_reserved_shared_text_ref_collision_disables_encoding_losslessly() -> None:
    execution = _execution()
    repeated = "candidate supplied text " * 30
    execution["events"] = [
        {"content": repeated, "shared_text_ref": "candidate-ref"},
        {"content": repeated, "shared_text_ref": "candidate-ref-2"},
    ]

    request = build_judge_request(_case(), execution)

    assert "shared_text" not in request["state"]
    assert request["state"]["execution"]["events"] == execution["events"]


def test_short_repeated_text_stays_unencoded_and_guard_blinding_remain() -> None:
    execution = _execution()
    short = "x" * 255
    execution["events"] = [{"content": short}, {"content": short}]

    request = build_judge_request(_case(), execution)

    assert "shared_text" not in request["state"]
    assert request["state"]["execution"]["events"][0]["content"] == short
    assert all(
        "untrusted evidence" in str(question["instructions"])
        and "never an instruction" in str(question["instructions"])
        for question in request["questions"].values()
    )
    assert "PRIVATE_EXECUTION_MARKER" not in repr(request)


def test_request_hash_covers_the_encoded_payload() -> None:
    execution = _execution()
    repeated = "hash-covered evidence " * 30
    execution["final_report"] = repeated
    execution["events"] = [{"content": repeated}, {"content": repeated}]
    transport = FakeTransport([_response()])

    result = JevJudge(transport=transport)._evaluate_one_request(_case(), execution)

    payload = _canonical_json(transport.requests[0][0]).encode("utf-8")
    assert result["request_hash"] == hashlib.sha256(payload).hexdigest()
    assert "shared_text" in transport.requests[0][0]["state"]


def test_oversized_request_is_refused_before_transport() -> None:
    transport = FakeTransport([])
    execution = _execution()
    execution["final_report"] = "x" * (MAX_REQUEST_BYTES + 100)

    result = JevJudge(transport=transport)._evaluate_one_request(_case(), execution)

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


class DimensionTransport:
    """Return one exact Score answer for the question in each request."""

    def __init__(self, *, scores=None, fail_dimension=None):
        self.scores = scores or {
            "fulfillment": 1.0,
            "evidence": 2.0,
            "constraints": 3.0,
            "verification": 4.0,
        }
        self.fail_dimension = fail_dimension
        self.requests = []

    def __call__(self, request, **kwargs):
        self.requests.append((request, kwargs))
        dimensions = tuple(request["questions"])
        assert len(dimensions) == 1
        dimension = dimensions[0]
        if dimension == self.fail_dimension:
            return {
                "status": 500,
                "body": {"error": "dimension failed"},
            }
        question = request["questions"][dimension]
        return {
            "status": 200,
            "body": {
                "model": "jev-1.13.0",
                "answers": {
                    dimension: _answer(
                        self.scores[dimension], question["criteria"]
                    )
                },
                "usage": {
                    "input_tokens": 100 + len(self.requests),
                    "output_tokens": 20,
                },
            },
        }


def test_evaluate_preserves_native_answers_and_has_no_confidence_multiplier() -> None:
    transport = FakeTransport([_response()])
    expected_request = build_judge_request(_case(), _execution())
    result = JevJudge(transport=transport)._evaluate_one_request(_case(), _execution())

    assert result["status"] == "scored"
    assert result["score"] == pytest.approx(37.5)
    assert result["model"] == "jev-1.13.0"
    assert set(result["dimensions"]) == set(DIMENSIONS)
    assert result["dimensions"]["verification"]["score"] == 3.0
    assert result["evidence_refs"] == ["ev-1", "ev-2"]
    assert len(result["request_hash"]) == 64
    assert transport.requests[0][0]["model"] == "jev-1.13.0"
    assert transport.requests[0][0] == expected_request
    assert result["fallback_used"] is False
    assert result["context_fallback_attempted"] is False


def test_public_evaluate_fans_out_dimension_views_and_aggregates_manifest(monkeypatch) -> None:
    execution = _execution()

    def views(source):
        assert source is execution
        result = {}
        for dimension in DIMENSIONS:
            view = copy.deepcopy(source)
            view["final_report"] = f"report visible to {dimension}"
            view["events"] = [
                {
                    "evidence_id": f"{dimension}-event",
                    "kind": "test",
                    "status": "passed",
                }
            ]
            result[dimension] = view
        return result

    monkeypatch.setattr("patchmud.scoring.judge._dimension_evidence_views", views)
    transport = DimensionTransport()
    result = JevJudge(transport=transport).evaluate(_case(), execution)

    assert result["status"] == "scored"
    assert result["judge_protocol_version"] == JUDGE_PROTOCOL_VERSION
    assert result["score"] == pytest.approx(62.5)
    assert set(result["dimensions"]) == set(DIMENSIONS)
    assert len(transport.requests) == 4
    for dimension, (request, _) in zip(DIMENSIONS, transport.requests, strict=True):
        assert set(request["questions"]) == {dimension}
        assert request["state"]["execution"]["final_report"] == (
            f"report visible to {dimension}"
        )
        assert request["state"]["execution"]["events"][0]["evidence_id"] == (
            f"{dimension}-event"
        )
        assert result["per_dimension_results"][dimension]["requested_dimensions"] == [
            dimension
        ]

    manifest = [
        {"dimension": dimension, "request_hash": result["per_dimension_results"][dimension]["request_hash"]}
        for dimension in DIMENSIONS
    ]
    assert result["request_hash_kind"] == "ordered-dimension-manifest-sha256"
    assert result["request_hash"] == hashlib.sha256(
        _canonical_json(manifest).encode("utf-8")
    ).hexdigest()
    assert result["request_manifest"] == manifest
    assert [item["dimension"] for item in result["usage"]["requests"]] == list(DIMENSIONS)
    assert {item["dimension"] for item in result["attempt_usage"]} == set(DIMENSIONS)
    assert set(result["evidence_refs"]) == {
        "fulfillment-event",
        "evidence-event",
        "constraints-event",
        "verification-event",
    }


def test_public_evaluate_fails_closed_but_preserves_successful_dimensions(monkeypatch) -> None:
    monkeypatch.setattr(
        "patchmud.scoring.judge._dimension_evidence_views",
        lambda execution: {dimension: copy.deepcopy(execution) for dimension in DIMENSIONS},
    )
    transport = DimensionTransport(fail_dimension="constraints")
    result = JevJudge(transport=transport, max_attempts=1).evaluate(_case(), _execution())

    assert result["status"] == "error"
    assert result["score"] is None
    assert set(result["dimensions"]) == {"fulfillment", "evidence", "verification"}
    assert set(result["per_dimension_results"]) == set(DIMENSIONS)
    assert result["per_dimension_results"]["constraints"]["status"] == "error"
    assert result["per_dimension_results"]["constraints"]["usage"] is None
    assert "dimension constraints failed" in result["error"]
    assert len(transport.requests) == 4


def test_one_dimension_validator_rejects_extra_native_answers() -> None:
    response = _response()
    result = JevJudge(transport=FakeTransport([response]))._evaluate_one_request(
        _case(), _execution(), dimension="evidence"
    )

    assert result["status"] == "error"
    assert "exactly the requested answers" in result["error"]
    assert result["score"] is None


def test_one_dimension_score_uses_requested_dimension_mean() -> None:
    transport = DimensionTransport(
        scores={
            "fulfillment": 0.0,
            "evidence": 2.0,
            "constraints": 0.0,
            "verification": 0.0,
        }
    )
    result = JevJudge(transport=transport)._evaluate_one_request(
        _case(), _execution(), dimension="evidence"
    )

    assert result["status"] == "scored"
    assert result["score"] == pytest.approx(50.0)
    assert result["requested_dimensions"] == ["evidence"]


def test_public_evaluate_propagates_interrupt_for_outer_checkpoint(monkeypatch) -> None:
    monkeypatch.setattr(
        "patchmud.scoring.judge._dimension_evidence_views",
        lambda execution: {dimension: copy.deepcopy(execution) for dimension in DIMENSIONS},
    )

    class InterruptTransport:
        def __call__(self, request, **kwargs):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        JevJudge(transport=InterruptTransport(), max_attempts=1).evaluate(
            _case(), _execution()
        )


def test_evaluate_accepts_live_jev_score_rounding_from_native_probabilities() -> None:
    """Live Jev 1.13 returns two-decimal score/probability fields independently."""

    response = {
        "model": "jev-1.13.0",
        "answers": {
            "fulfillment": {
                "type": "score",
                "score": 3.62,
                "legend": {str(level): f"fulfillment level {level}" for level in range(5)},
                "probabilities": {"0": 0.0, "1": 0.02, "2": 0.07, "3": 0.18, "4": 0.73},
                "confidence": 0.69,
            },
            "evidence": {
                "type": "score",
                "score": 3.0,
                "legend": {str(level): f"evidence level {level}" for level in range(5)},
                "probabilities": {"0": 0.0, "1": 0.01, "2": 0.18, "3": 0.59, "4": 0.22},
                "confidence": 0.63,
            },
            "constraints": {
                "type": "score",
                "score": 3.72,
                "legend": {str(level): f"constraints level {level}" for level in range(5)},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.07, "3": 0.12, "4": 0.81},
                "confidence": 0.77,
            },
            "verification": {
                "type": "score",
                "score": 3.08,
                "legend": {str(level): f"verification level {level}" for level in range(5)},
                "probabilities": {"0": 0.0, "1": 0.02, "2": 0.04, "3": 0.78, "4": 0.16},
                "confidence": 0.80,
            },
        },
        "usage": {"input_tokens": 6507, "output_tokens": 60},
    }
    result = JevJudge(transport=FakeTransport([response]))._evaluate_one_request(_case(), _execution())

    assert result["status"] == "scored"
    assert result["dimensions"]["constraints"]["score"] == 3.72


def test_evaluate_accepts_two_decimal_probability_sum_rounding() -> None:
    response = _response()
    answer = response["answers"]["fulfillment"]
    answer["probabilities"] = {"0": 0.99, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0}

    result = JevJudge(transport=FakeTransport([response]))._evaluate_one_request(_case(), _execution())

    assert result["status"] == "scored"
    assert result["dimensions"]["fulfillment"]["probabilities"]["0"] == 0.99


def test_evaluate_rejects_probability_sum_outside_two_decimal_rounding_bound() -> None:
    response = _response()
    response["answers"]["fulfillment"]["probabilities"] = {
        "0": 0.97,
        "1": 0.0,
        "2": 0.0,
        "3": 0.0,
        "4": 0.0,
    }

    result = JevJudge(transport=FakeTransport([response]))._evaluate_one_request(_case(), _execution())

    assert result["status"] == "error"
    assert "probabilities do not sum to one" in result["error"]


def test_evaluate_rejects_material_score_probability_mismatch() -> None:
    response = _response()
    response["answers"]["fulfillment"]["score"] = 0.2

    result = JevJudge(transport=FakeTransport([response]))._evaluate_one_request(_case(), _execution())

    assert result["status"] == "error"
    assert result["score"] is None
    assert "score disagrees with probabilities" in result["error"]


def test_evaluate_retries_only_bounded_provider_overload() -> None:
    transport = FakeTransport(
        [
            {"status": 429, "body": {"error": "rate limited"}},
            {"status": 529, "body": {"error": "overloaded"}},
            {"status": 200, "body": _response((4.0, 4.0, 4.0, 4.0))},
        ]
    )
    result = JevJudge(transport=transport, max_attempts=3, sleep=lambda _: None)._evaluate_one_request(
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


def test_context_limit_retries_once_with_lossless_fragment_request() -> None:
    shared = "pytest shared evidence line with stable public output\n" * 32
    execution = _execution()
    execution["events"] = [
        {"evidence_id": "ev-a", "content": shared + "first suffix\n"},
        {"evidence_id": "ev-b", "content": shared + "second suffix\n"},
        {"evidence_id": "ev-c", "content": shared + "third suffix\n"},
    ]
    transport = FakeTransport(
        [
            {"status": 400, "body": {"detail": {"error_type": "max_tokens_exceeded"}}},
            {"status": 200, "body": _response()},
        ]
    )

    result = JevJudge(transport=transport, max_attempts=1)._evaluate_one_request(
        _case(), execution
    )

    assert result["status"] == "scored"
    assert result["fallback_used"] is True
    assert len(transport.requests) == 2
    first_request, second_request = [item[0] for item in transport.requests]
    assert len(_canonical_json(second_request).encode("utf-8")) < len(
        _canonical_json(first_request).encode("utf-8")
    )
    assert _rehydrate_shared_text(second_request["state"]) == _rehydrate_shared_text(
        first_request["state"]
    )
    first_hash = hashlib.sha256(
        _canonical_json(first_request).encode("utf-8")
    ).hexdigest()
    second_hash = hashlib.sha256(
        _canonical_json(second_request).encode("utf-8")
    ).hexdigest()
    assert result["original_request_hash"] == first_hash
    assert result["request_hash"] == second_hash
    assert result["final_request_hash"] == second_hash
    assert result["attempt_usage"][0]["request_hash"] == first_hash
    assert result["attempt_usage"][1]["request_hash"] == second_hash
    assert result["unknown_usage_attempts"] == 1
    assert result["prior_usage_unknown"] == 1


def test_context_retry_requires_safe_provider_error_type() -> None:
    transport = FakeTransport(
        [
            {
                "status": 400,
                "body": {"detail": {"message": "max_tokens_exceeded"}},
            }
        ]
    )

    result = JevJudge(transport=transport, max_attempts=1)._evaluate_one_request(
        _case(), _execution()
    )

    assert result["status"] == "error"
    assert result["fallback_used"] is False
    assert result["context_fallback_attempted"] is False
    assert result["context_fallback_error"] is None
    assert len(transport.requests) == 1


def test_context_retry_collision_keeps_original_evidence_and_usage() -> None:
    shared = "collision evidence line that must remain ordinary text\n" * 32
    execution = _execution()
    execution["events"] = [
        {
            "evidence_id": "ev-a",
            "content": shared + "first suffix\n",
            "shared_text_parts": "public evidence field",
        },
        {
            "evidence_id": "ev-b",
            "content": shared + "second suffix\n",
            "shared_text_parts": "public evidence field",
        },
    ]
    transport = FakeTransport(
        [
            {
                "status": 400,
                "body": {
                    "detail": {"error_type": "max_tokens_exceeded"},
                    "usage": {"input_tokens": 17, "output_tokens": 0},
                },
            }
        ]
    )

    result = JevJudge(transport=transport, max_attempts=1)._evaluate_one_request(
        _case(), execution
    )

    assert result["status"] == "error"
    assert result["fallback_used"] is False
    assert result["context_fallback_attempted"] is True
    assert result["context_fallback_error"] == "no smaller lossless fallback request"
    assert len(transport.requests) == 1
    assert result["usage"] == {"input_tokens": 17, "output_tokens": 0}
    assert "shared_text_parts" not in transport.requests[0][0]["state"].get(
        "shared_text", {}
    )
    assert transport.requests[0][0]["state"]["execution"]["events"][0][
        "shared_text_parts"
    ] == "public evidence field"


def test_error_retains_provider_usage_and_unknown_attempts() -> None:
    transport = FakeTransport(
        [
            {"status": 429, "body": {"error": "rate limited", "usage": {"input_tokens": 11}}},
            {"status": 529, "body": {"error": "overloaded"}},
            {"status": 500, "body": {"error": "failed", "usage": {"input_tokens": 13}}},
        ]
    )
    result = JevJudge(transport=transport, max_attempts=3, sleep=lambda _: None)._evaluate_one_request(
        _case(), _execution()
    )

    assert result["status"] == "error"
    assert result["attempts"] == 3
    assert result["usage"] == {"input_tokens": 13}
    assert result["attempt_usage"][0]["usage"] == {"input_tokens": 11}
    assert result["attempt_usage"][1]["usage_known"] is False
    assert result["unknown_usage_attempts"] == 1
    assert result["prior_usage_unknown"] == 1


def test_http_error_keeps_bounded_redacted_provider_message(monkeypatch) -> None:
    secret = "jev-http-secret-for-test"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    body = {
        "error": {
            "message": f"request rejected: {secret}",
            "request": {"authorization": f"Bearer {secret}"},
        },
        "usage": {"input_tokens": 17, "output_tokens": 0},
    }

    result = JevJudge(
        transport=FakeTransport([{"status": 400, "body": body}]), max_attempts=1
    )._evaluate_one_request(_case(), _execution())

    assert result["status"] == "error"
    assert "HTTP 400" in result["error"]
    assert "request rejected: [redacted]" in result["error"]
    assert secret not in result["error"]
    assert "authorization" not in result["error"].lower()
    assert result["usage"] == {"input_tokens": 17, "output_tokens": 0}


def test_transport_type_error_is_not_reinvoked_with_different_signature() -> None:
    class TypeErrorTransport:
        def __init__(self):
            self.calls = 0

        def __call__(self, request, *, headers, timeout):
            self.calls += 1
            raise TypeError("transport failed")

    transport = TypeErrorTransport()
    result = JevJudge(transport=transport, max_attempts=3, sleep=lambda _: None)._evaluate_one_request(
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
    result = JevJudge(transport=FakeTransport([response]))._evaluate_one_request(_case(), _execution())

    assert result["status"] == "error"
    assert result["score"] is None
    assert result["dimensions"] == {}
    assert result["error"]
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 20}


def test_missing_api_key_without_fake_transport_is_an_error(monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = JevJudge()._evaluate_one_request(_case(), _execution())

    assert result["status"] == "error"
    assert result["score"] is None
    assert "TYPESAFE_API_KEY" in result["error"]


def test_transport_errors_redact_the_actual_api_key(monkeypatch) -> None:
    secret = "jev-secret-for-test"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)

    class FailingTransport:
        def __call__(self, request):
            raise ValueError(f"bad Authorization value {secret}")

    result = JevJudge(transport=FailingTransport(), max_attempts=1)._evaluate_one_request(_case(), _execution())

    assert result["status"] == "error"
    assert secret not in result["error"]
    assert "[redacted]" in result["error"]
