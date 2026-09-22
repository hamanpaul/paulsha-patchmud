"""Deterministic aggregation for JEV judgments.

The scoring engine deliberately keeps the arithmetic small and explicit.  JEV
produces the quality score; this module only averages already validated native
scores.  Execution outcomes, confidence, and test statuses are evidence and
never act as hidden score gates.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from typing import Any

from .judge import PROBABILITY_SUM_TOLERANCE, SCORE_PROBABILITY_TOLERANCE

__all__ = ["aggregate_results", "compare_results"]

_DIMENSIONS = ("fulfillment", "evidence", "constraints", "verification")


def _finite_number(value: Any, *, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and low <= float(value) <= high


def _case_id(row: Mapping[str, Any]) -> str | None:
    value = row.get("case_id")
    if value is None and isinstance(row.get("case"), Mapping):
        value = row["case"].get("id")
    if value is None and isinstance(row.get("execution"), Mapping):
        value = row["execution"].get("case_id")
    return value if isinstance(value, str) and value else None


def _case_metadata(case: Any) -> tuple[str | None, str | None]:
    if isinstance(case, str):
        return case, None
    if not isinstance(case, Mapping):
        return None, None
    case_id = case.get("id")
    category = case.get("category")
    return (
        case_id if isinstance(case_id, str) and case_id else None,
        category if isinstance(category, str) and category else None,
    )


def _judgment(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("judgment")
    return value if isinstance(value, Mapping) else row


def _repetition(row: Mapping[str, Any]) -> int | None:
    value = row.get("repetition", 1)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _valid_judgment(
    judgment: Mapping[str, Any], case: Mapping[str, Any] | None = None
) -> tuple[bool, float | None, str | None]:
    status = judgment.get("status")
    if status != "scored":
        return False, None, f"status {status!r} is not scored"
    model = judgment.get("model")
    if model != "jev-1.13.0":
        return False, None, "scored judgment is not from pinned JEV model"
    score = judgment.get("score")
    if not _finite_number(score, low=0.0, high=100.0):
        return False, None, f"invalid score {score!r}"

    dimensions = judgment.get("dimensions")
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(_DIMENSIONS):
        return False, None, "judgment dimensions are incomplete"
    dimension_scores: list[float] = []
    for dimension in _DIMENSIONS:
        answer = dimensions[dimension]
        if not isinstance(answer, Mapping) or answer.get("type") != "score" or not _finite_number(
            answer.get("score"), low=0.0, high=4.0
        ):
            return False, None, f"invalid {dimension} dimension"
        confidence = answer.get("confidence")
        if not _finite_number(confidence, low=0.0, high=1.0):
            return False, None, f"invalid {dimension} confidence"
        probabilities = answer.get("probabilities")
        if not isinstance(probabilities, Mapping) or set(str(key) for key in probabilities) != {str(index) for index in range(5)}:
            return False, None, f"invalid {dimension} probabilities"
        probability_values: list[float] = []
        for index in range(5):
            probability = probabilities.get(str(index), probabilities.get(index))
            if not _finite_number(probability, low=0.0, high=1.0):
                return False, None, f"invalid {dimension} probability"
            probability_values.append(float(probability))
        if not math.isclose(
            sum(probability_values),
            1.0,
            rel_tol=0.0,
            abs_tol=PROBABILITY_SUM_TOLERANCE + 1e-12,
        ):
            return False, None, f"{dimension} probabilities do not sum to one"
        if not math.isclose(
            float(answer["score"]),
            sum(index * probability_values[index] for index in range(5)),
            rel_tol=0.0,
            abs_tol=SCORE_PROBABILITY_TOLERANCE + 1e-12,
        ):
            return False, None, f"{dimension} score disagrees with probabilities"
        legend = answer.get("legend")
        if not isinstance(legend, Mapping) or set(str(key) for key in legend) != {str(index) for index in range(5)}:
            return False, None, f"invalid {dimension} legend"
        rubric = case.get("rubric") if isinstance(case, Mapping) else None
        if isinstance(rubric, Mapping) and isinstance(rubric.get(dimension), Mapping):
            criteria = rubric[dimension].get("criteria")
            expected_legend = {str(index): value for index, value in enumerate(criteria)} if isinstance(criteria, list) else None
            normalised_legend = {str(key): value for key, value in legend.items()}
            if expected_legend is not None and normalised_legend != expected_legend:
                return False, None, f"{dimension} legend does not match rubric"
        dimension_scores.append(float(answer["score"]))
    expected_score = 25.0 * sum(dimension_scores) / len(_DIMENSIONS)
    if not math.isclose(float(score), expected_score, rel_tol=0.0, abs_tol=1e-6):
        return False, None, "case score disagrees with native dimension mean"
    return True, float(score), None


def _summary_view(value: Mapping[str, Any]) -> Mapping[str, Any]:
    summary = value.get("summary")
    return summary if isinstance(summary, Mapping) else value


def aggregate_results(
    case_results: Iterable[Mapping[str, Any]],
    expected_cases: Iterable[Mapping[str, Any] | str],
    repeat: int = 1,
) -> dict[str, Any]:
    """Aggregate JEV case rows with fail-closed coverage.

    ``repeat`` is one-based: a run with ``repeat=2`` must contain exactly one
    valid judgment for every ``(case_id, 1)`` and ``(case_id, 2)`` slot.  Case
    repetitions are averaged first, then cases within each category, and the
    six (or however many the suite defines) categories are averaged equally.
    """

    errors: list[str] = []
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        errors.append(f"invalid repeat {repeat!r}")
        repeat = 1

    expected: dict[str, dict[str, Any]] = {}
    expected_order: list[str] = []
    for item in expected_cases:
        case_id, category = _case_metadata(item)
        if case_id is None:
            errors.append("expected case has no id")
            continue
        if case_id in expected:
            errors.append(f"duplicate expected case {case_id}")
            continue
        expected[case_id] = {"category": category or "unknown"}
        expected_order.append(case_id)
    if not expected:
        errors.append("no expected cases")

    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for row_index, row in enumerate(case_results):
        if not isinstance(row, Mapping):
            errors.append(f"row {row_index} is not an object")
            continue
        case_id = _case_id(row)
        repetition = _repetition(row)
        if case_id is None:
            errors.append(f"row {row_index} has no case id")
            continue
        if case_id not in expected:
            errors.append(f"unknown case {case_id}")
            continue
        if repetition is None or not 1 <= repetition <= repeat:
            errors.append(f"invalid repetition for {case_id}: {row.get('repetition')!r}")
            continue
        key = (case_id, repetition)
        if key in rows:
            errors.append(f"duplicate case/repetition {case_id}/{repetition}")
            continue
        judgment = _judgment(row)
        row_case = row.get("case") if isinstance(row.get("case"), Mapping) else None
        valid, score, reason = _valid_judgment(judgment, row_case)
        rows[key] = {
            "status": judgment.get("status"),
            "score": score if valid else None,
            "valid": valid,
        }
        if not valid:
            errors.append(f"invalid judgment {case_id}/{repetition}: {reason}")

    categories_order: list[str] = []
    for case_id in expected_order:
        category = expected[case_id]["category"]
        if category not in categories_order:
            categories_order.append(category)

    case_summaries: dict[str, dict[str, Any]] = {}
    scored_slots = 0
    for case_id in expected_order:
        repetition_values: dict[str, dict[str, Any]] = {}
        valid_scores: list[float] = []
        for repetition in range(1, repeat + 1):
            slot = rows.get((case_id, repetition))
            if slot is None:
                repetition_values[str(repetition)] = {
                    "status": "missing",
                    "score": None,
                }
                errors.append(f"missing case/repetition {case_id}/{repetition}")
                continue
            repetition_values[str(repetition)] = {
                "status": slot["status"],
                "score": slot["score"],
            }
            if slot["valid"]:
                scored_slots += 1
                valid_scores.append(slot["score"])
        complete = len(valid_scores) == repeat
        case_summaries[case_id] = {
            "category": expected[case_id]["category"],
            "expected_repetitions": repeat,
            "scored_repetitions": len(valid_scores),
            "repetitions": repetition_values,
            "score": (sum(valid_scores) / len(valid_scores)) if valid_scores else None,
            "complete": complete,
        }

    category_summaries: dict[str, dict[str, Any]] = {}
    complete_categories: list[float] = []
    for category in categories_order:
        category_cases = [
            case_summaries[case_id]
            for case_id in expected_order
            if case_summaries[case_id]["category"] == category
        ]
        values = [entry["score"] for entry in category_cases if entry["score"] is not None]
        category_complete = bool(category_cases) and all(entry["complete"] for entry in category_cases)
        category_score = sum(values) / len(values) if values else None
        category_summaries[category] = {
            "expected_cases": len(category_cases),
            "scored_cases": len(values),
            "cases": {
                case_id: case_summaries[case_id]["score"]
                for case_id in expected_order
                if case_summaries[case_id]["category"] == category
            },
            "score": category_score,
            "complete": category_complete,
        }
        if category_complete and category_score is not None:
            complete_categories.append(category_score)

    expected_slots = len(expected_order) * repeat
    complete = (
        bool(expected_order)
        and not errors
        and scored_slots == expected_slots
        and len(category_summaries) == len(categories_order)
        and all(value["complete"] for value in category_summaries.values())
    )
    total = (sum(complete_categories) / len(complete_categories)) if complete else None
    return {
        "total": total,
        "categories": category_summaries,
        "cases": case_summaries,
        "coverage": {"expected": expected_slots, "scored": scored_slots},
        "complete": complete,
        "repeat": repeat,
        "errors": errors,
    }


def _record_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "run_id": record.get("run_id"),
        "created_at": record.get("created_at"),
    }


def _compatibility_errors(target: Mapping[str, Any], base: Mapping[str, Any]) -> list[str]:
    """Check dimensions that must match for a score comparison.

    Profiles may intentionally differ in harness/model/effort.  Suite, rubric,
    judge, repeat, engine, budget, and tool-cohort identity must remain equal.
    Missing optional metadata is tolerated for old/offline summary callers.
    """

    errors: list[str] = []
    target_provenance = target.get("provenance")
    base_provenance = base.get("provenance")
    target_derived = isinstance(target_provenance, Mapping) and target_provenance.get("kind") == "derived-rejudgment-v1"
    base_derived = isinstance(base_provenance, Mapping) and base_provenance.get("kind") == "derived-rejudgment-v1"
    if target_derived != base_derived:
        errors.append("derived and ordinary runs cannot be compared")
    elif target_derived and base_derived:
        target_source_engine = target_provenance.get("source_engine_digest")
        base_source_engine = base_provenance.get("source_engine_digest")
        if not isinstance(target_source_engine, str) or not isinstance(base_source_engine, str):
            errors.append("source execution engine digest missing")
        elif target_source_engine != base_source_engine:
            errors.append("source execution engine mismatch")
        target_judge_engine = target.get("judge_engine_digest", target_provenance.get("judge_engine_digest"))
        base_judge_engine = base.get("judge_engine_digest", base_provenance.get("judge_engine_digest"))
        if not isinstance(target_judge_engine, str) or not isinstance(base_judge_engine, str):
            errors.append("judge engine digest missing")
        elif target_judge_engine != base_judge_engine:
            errors.append("judge engine mismatch")
    # A plain aggregate summary has no identity metadata and remains useful to
    # callers doing offline arithmetic.  Full run records, however, must carry
    # the cohort contract before a comparison is publishable.
    explicitly_unverified = bool(target.get("identity_unverified")) and bool(base.get("identity_unverified"))
    is_record = any(key in target or key in base for key in ("suite", "profile", "judge_model", "run_id")) and not explicitly_unverified

    def lookup(record: Mapping[str, Any], key: str) -> tuple[bool, Any]:
        if key in record:
            return True, record[key]
        profile = record.get("profile")
        if isinstance(profile, Mapping) and key in profile:
            return True, profile[key]
        aliases = {
            "engine_digest": ("engine", "engine_version"),
            "environment_digest": ("environment", "environment_version"),
        }
        for alias in aliases.get(key, ()):
            if alias in record:
                return True, record[alias]
            if isinstance(profile, Mapping) and alias in profile:
                return True, profile[alias]
        return False, None

    target_suite = target.get("suite")
    base_suite = base.get("suite")
    if is_record:
        if not isinstance(target_suite, Mapping) or not isinstance(base_suite, Mapping):
            errors.append("suite metadata missing")
        else:
            for key in ("id", "version", "suite_hash", "rubric_version", "case_ids"):
                if key not in target_suite or key not in base_suite:
                    errors.append(f"suite.{key} missing")
                elif target_suite[key] != base_suite[key]:
                    errors.append(f"suite.{key} mismatch")
    elif isinstance(target_suite, Mapping) and isinstance(base_suite, Mapping):
        for key in ("id", "version", "suite_hash", "rubric_version", "case_ids"):
            if key in target_suite and key in base_suite and target_suite[key] != base_suite[key]:
                errors.append(f"suite.{key} mismatch")

    required_identity = (
        "judge_model",
        "repeat",
        "protocol_version",
        "engine_digest",
        "environment_digest",
        "tool_cohort",
        "budget",
    )
    for key in required_identity:
        target_has, target_value = lookup(target, key)
        base_has, base_value = lookup(base, key)
        # Older bare summaries can omit identity by construction; complete run
        # records cannot silently compare unlike or unknown cohorts.
        if is_record and (not target_has or not base_has):
            errors.append(f"{key} metadata missing")
        elif target_has and base_has and target_value != base_value:
            errors.append(f"{key} mismatch")
    # Legacy records predate explicit judge protocol metadata.  They can still
    # compare with each other, but cannot cross a changed evidence contract.
    if "judge_protocol_version" in target or "judge_protocol_version" in base:
        if not target.get("judge_protocol_version") or not base.get("judge_protocol_version"):
            errors.append("judge_protocol_version metadata missing")
        elif target["judge_protocol_version"] != base["judge_protocol_version"]:
            errors.append("judge_protocol_version mismatch")
    return errors


def _score_delta(target: Any, base: Any) -> dict[str, Any]:
    valid_target = _finite_number(target, low=0.0, high=100.0)
    valid_base = _finite_number(base, low=0.0, high=100.0)
    return {
        "target": float(target) if valid_target else None,
        "base": float(base) if valid_base else None,
        "delta": (float(target) - float(base)) if valid_target and valid_base else None,
    }


def compare_results(target: Mapping[str, Any], base: Mapping[str, Any]) -> dict[str, Any]:
    """Return like-for-like JEV score deltas for two run records or summaries."""

    target = target if isinstance(target, Mapping) else {}
    base = base if isinstance(base, Mapping) else {}
    target_summary = _summary_view(target)
    base_summary = _summary_view(base)
    compatibility_errors = _compatibility_errors(target, base)
    if not (target.get("identity_unverified") and base.get("identity_unverified")):
        if "complete" not in target_summary or "complete" not in base_summary:
            compatibility_errors.append("summary completeness metadata missing")
        if "coverage" not in target_summary or "coverage" not in base_summary:
            compatibility_errors.append("summary coverage metadata missing")

    target_cases = target_summary.get("cases", {})
    base_cases = base_summary.get("cases", {})
    if not isinstance(target_cases, Mapping):
        target_cases = {}
    if not isinstance(base_cases, Mapping):
        base_cases = {}
    case_deltas: dict[str, dict[str, Any]] = {}
    for case_id in sorted(set(target_cases) & set(base_cases)):
        target_value = target_cases[case_id]
        base_value = base_cases[case_id]
        if isinstance(target_value, Mapping) and target_value.get("complete") is False:
            continue
        if isinstance(base_value, Mapping) and base_value.get("complete") is False:
            continue
        if isinstance(target_value, Mapping):
            target_value = target_value.get("score")
        if isinstance(base_value, Mapping):
            base_value = base_value.get("score")
        case_deltas[case_id] = _score_delta(target_value, base_value)

    target_categories = target_summary.get("categories", {})
    base_categories = base_summary.get("categories", {})
    if not isinstance(target_categories, Mapping):
        target_categories = {}
    if not isinstance(base_categories, Mapping):
        base_categories = {}
    category_deltas: dict[str, dict[str, Any]] = {}
    for category in sorted(set(target_categories) & set(base_categories)):
        target_value = target_categories[category]
        base_value = base_categories[category]
        if isinstance(target_value, Mapping):
            target_value = target_value.get("score")
        if isinstance(base_value, Mapping):
            base_value = base_value.get("score")
        category_deltas[category] = _score_delta(target_value, base_value)

    total = _score_delta(target_summary.get("total"), base_summary.get("total"))
    if compatibility_errors:
        total = {"target": None, "base": None, "delta": None}
        category_deltas = {}
        case_deltas = {}

    deltas = {
        "total": total,
        "categories": category_deltas,
        "cases": case_deltas,
    }
    result = {
        "target_run_id": target.get("run_id"),
        "base_run_id": base.get("run_id"),
        "target_created_at": target.get("created_at"),
        "base_created_at": base.get("created_at"),
        "reused": bool((target.get("comparison") or {}).get("reused", False))
        if isinstance(target.get("comparison"), Mapping)
        else False,
        "compatible": not compatibility_errors,
        "complete": bool(target_summary.get("complete", True)) and bool(base_summary.get("complete", True)) and not compatibility_errors,
        "matched_repeat": (
            target_summary.get("repeat")
            if target_summary.get("complete", True)
            and base_summary.get("complete", True)
            and target_summary.get("repeat") == base_summary.get("repeat")
            else None
        ),
        "errors": compatibility_errors,
        "like_for_like": {
            "case_ids": sorted(case_deltas),
            "categories": sorted(category_deltas),
        },
        "deltas": deltas,
        # Keep the three common views at the top level for simple consumers.
        "total": total,
        "categories": category_deltas,
        "cases": case_deltas,
    }
    return result
