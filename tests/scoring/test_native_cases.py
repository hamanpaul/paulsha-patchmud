"""Public contract checks for native coding-agent case execution."""

from __future__ import annotations

import pytest

from patchmud.scoring.cases import load_case, load_suite


STAGED_CASES = (
    "diagnosis-incident-timeline",
    "repair-migration-state",
    "scope-changing-boundary",
    "testing-schema-evolution",
    "recovery-flag-rollback",
    "audit-deployment-integrity",
)

TESTING_PATHS = {
    "testing-parser-matrix": ["src/parser.py", "tests/agent/**"],
    "testing-retry-fakeclock": [
        "src/dispatcher.py",
        "src/backoff.py",
        "src/cancel.py",
        "tests/agent/**",
    ],
    "testing-schema-evolution": [
        "src/envelope.py",
        "src/normalizer.py",
        "src/sink.py",
        "tests/agent/**",
    ],
}


@pytest.mark.parametrize("case_id", STAGED_CASES)
def test_staged_cases_expose_native_phases_without_turn_wording(case_id: str) -> None:
    case = load_case(case_id)

    assert [stage["native_phase"] for stage in case["stages"]] == [1, 2]
    assert [stage["after_turn"] for stage in case["stages"]] == [8, 16]

    public_text = " ".join(
        [
            case["prompt"],
            *case["requirements"],
            *(stage["message"] for stage in case["stages"]),
        ]
    ).lower()
    for forbidden in ("after turn", "turn eight", "turn sixteen", "after_turn boundary"):
        assert forbidden not in public_text


@pytest.mark.parametrize("case_id, expected_paths", TESTING_PATHS.items())
def test_testing_cases_scope_agent_owned_regressions(
    case_id: str, expected_paths: list[str]
) -> None:
    case = load_case(case_id)

    assert case["allowed_paths"] == expected_paths
    public_text = " ".join([case["prompt"], *case["requirements"]]).lower()
    assert "tests/agent/**" in public_text
    assert "read-only" in public_text or "read only" in public_text


def test_native_suite_version_is_current() -> None:
    assert load_suite()["version"] == "1.1.0"
