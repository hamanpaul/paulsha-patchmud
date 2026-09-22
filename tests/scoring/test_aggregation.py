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


def test_aggregation_accepts_live_jev_score_rounding_from_native_probabilities() -> None:
    criteria = [f"level {level}" for level in range(5)]
    scores = {
        "fulfillment": (3.62, [0.0, 0.02, 0.07, 0.18, 0.73], 0.69),
        "evidence": (3.0, [0.0, 0.01, 0.18, 0.59, 0.22], 0.63),
        "constraints": (3.72, [0.0, 0.0, 0.07, 0.12, 0.81], 0.77),
        "verification": (3.08, [0.0, 0.02, 0.04, 0.78, 0.16], 0.80),
    }
    dimensions = {
        dimension: {
            "type": "score",
            "score": score,
            "legend": {str(level): criteria[level] for level in range(5)},
            "probabilities": {str(level): probability for level, probability in enumerate(probabilities)},
            "confidence": confidence,
        }
        for dimension, (score, probabilities, confidence) in scores.items()
    }
    row = {
        "case_id": "repair-live",
        "repetition": 1,
        "judgment": {
            "status": "scored",
            "model": "jev-1.13.0",
            "score": 83.875,
            "dimensions": dimensions,
        },
    }
    expected = [
        {
            "id": "repair-live",
            "category": "repair",
            "rubric": {
                dimension: {"criteria": criteria}
                for dimension in dimensions
            },
        }
    ]

    summary = aggregate_results([row], expected)

    assert summary["complete"] is True
    assert summary["total"] == 83.875


def test_aggregation_accepts_two_decimal_probability_sum_rounding() -> None:
    row = _result("repair-rounding", 1, 0.0)
    row["judgment"]["dimensions"]["fulfillment"]["probabilities"]["0"] = 0.99

    summary = aggregate_results(
        [row], [{"id": "repair-rounding", "category": "repair"}]
    )

    assert summary["complete"] is True
    assert summary["total"] == 0.0


def test_aggregation_rejects_probability_sum_outside_two_decimal_rounding_bound() -> None:
    row = _result("repair-rounding", 1, 0.0)
    row["judgment"]["dimensions"]["fulfillment"]["probabilities"]["0"] = 0.97

    summary = aggregate_results(
        [row], [{"id": "repair-rounding", "category": "repair"}]
    )

    assert summary["complete"] is False
    assert summary["total"] is None
    assert any("probabilities do not sum to one" in error for error in summary["errors"])


def test_aggregation_rejects_material_score_probability_mismatch() -> None:
    criteria = [f"level {level}" for level in range(5)]
    dimensions = {
        dimension: {
            "type": "score",
            "score": 0.0,
            "legend": {str(level): criteria[level] for level in range(5)},
            "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0},
            "confidence": 1.0,
        }
        for dimension in ("fulfillment", "evidence", "constraints", "verification")
    }
    dimensions["constraints"]["score"] = 0.2
    row = {
        "case_id": "repair-mismatch",
        "repetition": 1,
        "judgment": {
            "status": "scored",
            "model": "jev-1.13.0",
            "score": 1.25,
            "dimensions": dimensions,
        },
    }

    summary = aggregate_results([row], [{"id": "repair-mismatch", "category": "repair"}])

    assert summary["complete"] is False
    assert summary["total"] is None
    assert any("score disagrees with probabilities" in error for error in summary["errors"])


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

    target_record["judge_protocol_version"] = "dimension-evidence-v1"
    assert compare_results(target_record, base_record)["compatible"] is False
    base_record["judge_protocol_version"] = "full-state-v1"
    assert compare_results(target_record, base_record)["compatible"] is False
    base_record["judge_protocol_version"] = "dimension-evidence-v1"
    assert compare_results(target_record, base_record)["total"]["delta"] == 10.0


def test_derived_comparison_rejects_mixed_runs_and_mismatched_source_engines() -> None:
    expected = _expected()
    summary = aggregate_results([_result(case_id, 1, 50) for case_id in [item["id"] for item in expected]], expected)
    identity = {
        "suite": {"id": "engineering-v1", "version": "1", "suite_hash": "suite", "rubric_version": "rubric", "case_ids": [item["id"] for item in expected]},
        "judge_model": "jev-1.13.0",
        "repeat": 1,
        "protocol_version": "protocol-1",
        "engine_digest": "a" * 64,
        "environment_digest": "environment",
        "tool_cohort": "native-tools",
        "budget": {"turns": 8, "wall_seconds": 600},
        "judge_engine_digest": "b" * 64,
    }
    derived = {
        "run_id": "derived-target",
        "created_at": "2026-09-21T01:00:00Z",
        "summary": summary,
        **identity,
        "provenance": {
            "kind": "derived-rejudgment-v1",
            "source_run_id": "target-source",
            "source_engine_digest": "a" * 64,
        },
    }
    ordinary = {"run_id": "ordinary-base", "created_at": "2026-09-20T01:00:00Z", "summary": summary, **identity}

    mixed = compare_results(derived, ordinary)
    assert mixed["compatible"] is False
    assert any("derived" in error for error in mixed["errors"])

    mismatched = copy_record(derived)
    mismatched["provenance"]["source_engine_digest"] = "c" * 64
    mismatched["engine_digest"] = "c" * 64
    pair = compare_results(derived, mismatched)
    assert pair["compatible"] is False
    assert any("source execution engine" in error for error in pair["errors"])


def copy_record(record: dict) -> dict:
    """Small local deep copy helper to keep the test independent of fixtures."""
    import copy

    return copy.deepcopy(record)
