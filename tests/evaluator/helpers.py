"""Task 5 測試共用 builder：合成 IssueCard 與 ProbeOutcome（spec §9）。"""

from __future__ import annotations

from patchmud.deck.model import (
    CompatibilityRubric,
    CompatProbe,
    CriticalRequirement,
    FunctionalRubric,
    IssueCard,
    MaintainabilityRubric,
    PowerRubric,
    PublicRequirement,
    RegressionProbe,
    RobustnessRubric,
    RubricGroup,
    RuntimeEfficiencyRubric,
)
from patchmud.sandbox.probes import ProbeOutcome

# 合成 probe 路徑常數（synthetic card 專用）
CR1 = "hidden/test_cr1.py"
CR2 = "hidden/test_cr2.py"
EDGES = "hidden/test_edges.py"
API = "tests/public/test_api.py"
HIDDEN_COMPAT = "hidden/test_compat.py"
PERF1 = "hidden/test_perf.py"
PERF2 = "hidden/test_perf2.py"


def make_outcome(
    status: str = "passed",
    cases_total: int = 1,
    cases_passed: int | None = None,
    wall_ms: int = 100,
    fingerprints: tuple[str, ...] = (),
) -> ProbeOutcome:
    if cases_passed is None:
        cases_passed = cases_total if status == "passed" else 0
    return ProbeOutcome(
        status=status,  # type: ignore[arg-type]
        cases_total=cases_total,
        cases_passed=cases_passed,
        failure_fingerprints=tuple(fingerprints),
        wall_ms=wall_ms,
        cpu_ms=wall_ms,
    )


def make_card(
    *,
    expected_patch_loc: tuple[int, int] = (30, 80),
    allowed_paths: tuple[str, ...] = ("src/**", "tests/agent/**"),
    expected_paths: tuple[str, ...] = ("src/snapshot.py",),
    functional_groups: tuple[tuple[str, int], ...] = ((CR1, 40), (CR2, 20)),
    robustness_probes: tuple[str, ...] = (EDGES,),
    robustness_points: int = 15,
    compat_rubric_probes: tuple[str, ...] = (API, HIDDEN_COMPAT),
    compat_points: int = 10,
    maintainability_points: int = 10,
    perf_probes: tuple[str, ...] = (PERF1, PERF2),
    perf_points: int = 5,
    timeout_factor: float = 3.0,
    card_compat_probes: tuple[str, ...] = (API,),
    regression_probes: tuple[RegressionProbe, ...] = (
        RegressionProbe(path="tests/starter/"),
    ),
) -> IssueCard:
    rubric = PowerRubric(
        functional=FunctionalRubric(
            points=sum(points for _, points in functional_groups),
            groups=tuple(
                RubricGroup(id=f"G{i}", points=points, probe=probe)
                for i, (probe, points) in enumerate(functional_groups, start=1)
            ),
        ),
        robustness=RobustnessRubric(
            points=robustness_points, probes=tuple(robustness_probes)
        ),
        compatibility=CompatibilityRubric(
            points=compat_points, probes=tuple(compat_rubric_probes)
        ),
        maintainability=MaintainabilityRubric(points=maintainability_points),
        runtime_efficiency=RuntimeEfficiencyRubric(
            points=perf_points, probes=tuple(perf_probes), timeout_factor=timeout_factor
        ),
    )
    return IssueCard(
        schema_version=1,
        issue_id="synthetic-v1",
        archetype="state-recovery",
        difficulty="medium",
        difficulty_scale=1.0,
        expected_patch_loc=tuple(expected_patch_loc),
        wall_clock_seconds=600,
        max_turns=8,
        allowed_paths=tuple(allowed_paths),
        expected_paths=tuple(expected_paths),
        public_requirements=(
            PublicRequirement(id="MAIN-1", text="t", probe="tests/public/test_main.py"),
        ),
        critical_requirements=(CriticalRequirement(id="CR-1", hidden_probe=CR1),),
        regression_probes=tuple(regression_probes),
        compat_probes=tuple(CompatProbe(probe=p) for p in card_compat_probes),
        power_rubric=rubric,
        reference_cost=None,
    )


def green(*probes: str, cases: int = 1) -> dict[str, ProbeOutcome]:
    return {p: make_outcome("passed", cases_total=cases) for p in probes}
