"""JEV-only score aggregation and like-for-like comparison tests."""

from __future__ import annotations

import math

from patchmud.scoring.aggregation import aggregate_results, compare_results


def _expected() -> list[dict]:
    return [
        {"id": "repair-1", "category": "repair"},
        {"id": "repair-2", "category": "repair"},
        {"id": "audit-1", "category": "audit"},
        {"id": "audit-2", "category": "audit"},
    ]


def _result(case_id: str, repetition: int, score: float, *, status="scored") -> dict:
    dimension_score = score / 25
    if math.isfinite(dimension_score):
        lower = int(dimension_score)
        fraction = dimension_score - lower
        probabilities = {str(level): 0.0 for level in range(5)}
        probabilities[str(lower)] = 1.0 - fraction
        probabilities[str(min(4, lower + 1))] += fraction
    else:
        probabilities = {str(level): (math.nan if level == 0 else 0.0) for level in range(5)}
    return {
        "case_id": case_id,
        "repetition": repetition,
        "judgment": {
            "status": status,
            "model": "jev-1.13.0",
            "score": score if status == "scored" else None,
            "dimensions": {
                dimension: {
                    "type": "score",
                    "score": dimension_score,
                    "legend": {str(level): f"level {level}" for level in range(5)},
                    "probabilities": probabilities,
                    "confidence": 1.0,
                }
                for dimension in ("fulfillment", "evidence", "constraints", "verification")
            },
        },
    }


def test_aggregation_is_equal_weighted_by_case_then_category() -> None:
    results = [
        _result("repair-1", 1, 20),
        _result("repair-1", 2, 40),
        _result("repair-2", 1, 60),
        _result("repair-2", 2, 80),
        _result("audit-1", 1, 100),
        _result("audit-1", 2, 80),
        _result("audit-2", 1, 40),
        _result("audit-2", 2, 60),
    ]
    summary = aggregate_results(results, _expected(), repeat=2)

    assert summary["complete"] is True
    assert summary["coverage"] == {"expected": 8, "scored": 8}
    assert summary["cases"]["repair-1"]["score"] == 30.0
    assert summary["categories"]["repair"]["score"] == 50.0
    assert summary["categories"]["audit"]["score"] == 70.0
    assert summary["total"] == 60.0


def test_missing_duplicate_and_invalid_slots_are_incomplete_and_no_total() -> None:
    results = [
        _result("repair-1", 1, 50),
        _result("repair-1", 1, 60),  # duplicate
        _result("repair-1", 2, 50),
        _result("repair-2", 1, math.nan),  # invalid
        _result("audit-1", 1, 20),
        _result("audit-2", 1, 20),
    ]
    summary = aggregate_results(results, _expected(), repeat=2)

    assert summary["complete"] is False
    assert summary["total"] is None
    assert summary["coverage"]["expected"] == 8
    assert summary["coverage"]["scored"] < 8
    assert any("duplicate" in error for error in summary["errors"])
    assert any("invalid" in error for error in summary["errors"])


def test_compare_only_emits_like_for_like_deltas_and_metadata() -> None:
    expected = _expected()
    target = aggregate_results(
        [_result(case_id, 1, score) for case_id, score in (
            ("repair-1", 80),
            ("repair-2", 60),
            ("audit-1", 50),
            ("audit-2", 30),
        )],
        expected,
    )
    base = aggregate_results(
        [_result(case_id, 1, score) for case_id, score in (
            ("repair-1", 70),
            ("repair-2", 50),
            ("audit-1", 40),
            ("audit-2", 20),
        )],
        expected,
    )
    common_identity = {
        "suite": {"id": "engineering-v1", "version": "1", "suite_hash": "suite", "rubric_version": "rubric", "case_ids": [item["id"] for item in expected]},
        "judge_model": "jev-1.13.0",
        "repeat": 1,
        "profile": {
            "protocol_version": "protocol-1",
            "engine_digest": "engine",
            "environment_digest": "environment",
            "tool_cohort": "tools",
            "budget": {"turns": 8, "wall_seconds": 600},
        },
    }
    target_record = {"run_id": "target", "created_at": "2026-09-21T01:00:00Z", "summary": target, **common_identity}
    base_record = {"run_id": "base", "created_at": "2026-09-20T01:00:00Z", "summary": base, **common_identity}

    comparison = compare_results(target_record, base_record)

    assert comparison["target_run_id"] == "target"
    assert comparison["base_run_id"] == "base"
    assert comparison["total"]["delta"] == 10.0
    assert comparison["categories"]["repair"]["delta"] == 10.0
    assert comparison["cases"]["repair-1"]["delta"] == 10.0
    assert comparison["target_created_at"] == "2026-09-21T01:00:00Z"
    assert comparison["base_created_at"] == "2026-09-20T01:00:00Z"
