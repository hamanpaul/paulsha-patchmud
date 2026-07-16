"""Power rubric：100 分、公式全 pin（spec §9.3、F15）。

全部為純函數：輸入 probe outcomes / diff 檔案變更 / card，輸出分數與封存值。
- functional 60：group 內 probe 全綠得該組分數（all-or-nothing），否則 0。
- robustness 15：分母 = 全部 robustness probes 的 pytest case 總數；
  得分 = points × passed_cases / total_cases。
- compatibility 10：rubric compat probes 全綠得滿分，任一紅 0
  （§9.2 API hard-gate cap 另由 gates.py 疊加取低）。
- maintainability 10 = diff 4 + scope (2+1) + lint 3；小項常數為 spec pin 值。
- runtime_efficiency 5：每個 perf probe「通過且 runtime ≤ timeout_factor × reference」
  各得均分；判定以量測當下值定案並封存為 `PerfJudgment`，L1 重算只引用封存
  布林、不重新量測（spec §12.2、F2）。

三態原則：`error` 與 `failed` 永不混同，但對「綠」的判定兩者皆不綠；
無法驗證的項目（lint 不可執行、reference timing 缺漏）一律 fail-closed 得 0。
"""

from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from patchmud.deck.model import (
    CompatibilityRubric,
    FunctionalRubric,
    IssueCard,
    RobustnessRubric,
    RuntimeEfficiencyRubric,
)
from patchmud.sandbox.probes import ProbeOutcome

__all__ = [
    "EvaluatorError",
    "FileChange",
    "MaintainabilityReport",
    "PerfJudgment",
    "PowerReport",
    "runtime_efficiency_from_judgments",
    "score_compatibility",
    "score_functional",
    "score_maintainability",
    "score_power",
    "score_robustness",
    "score_runtime_efficiency",
]

# spec §9.3 pin 死的 maintainability 小項配分
_DIFF_POINTS = 4
_SCOPE_HARD_POINTS = 2
_SCOPE_SOFT_POINTS = 1
_LINT_POINTS = 3
_MAINTAINABILITY_POINTS = (
    _DIFF_POINTS + _SCOPE_HARD_POINTS + _SCOPE_SOFT_POINTS + _LINT_POINTS
)

#: agent 自寫測試路徑；非 production，不計入 L 與 SCOPE
_AGENT_TEST_PREFIX = "tests/agent/"


class EvaluatorError(Exception):
    """evaluator 契約違反（rubric 常數不符、資產缺漏等），fail-closed。"""


@dataclass(frozen=True)
class FileChange:
    """final diff 中單一檔案的變更量（git numstat 語意）。"""

    path: str
    added: int
    deleted: int

    @property
    def loc(self) -> int:
        return self.added + self.deleted


@dataclass(frozen=True)
class PerfJudgment:
    """perf probe 量測當下的封存判定（L1 重算唯一依據，spec §12.2）。"""

    probe_id: str
    wall_ms: int
    budget_ms: float | None
    passed: bool
    within_budget: bool

    @property
    def earned(self) -> bool:
        return self.passed and self.within_budget


@dataclass(frozen=True)
class MaintainabilityReport:
    """maintainability 三小項的分數與封存觀測值。"""

    diff_size: float
    scope: int
    lint: int
    total: float
    production_loc: int
    scope_hard_files: tuple[str, ...]
    scope_soft_loc: int
    lint_new_diagnostics: int | None


@dataclass(frozen=True)
class PowerReport:
    """Power 分項與總分；含封存欄位供 L1 重算引用。"""

    functional: int
    robustness: float
    compatibility: int
    maintainability: float
    runtime_efficiency: float
    total: float
    maintainability_breakdown: MaintainabilityReport
    perf_judgments: tuple[PerfJudgment, ...]


def _is_green(outcome: ProbeOutcome | None) -> bool:
    return outcome is not None and outcome.status == "passed"


def score_functional(
    rubric: FunctionalRubric, outcomes: Mapping[str, ProbeOutcome]
) -> int:
    """group all-or-nothing：綁定 probe 綠得該組分數，否則（含 error／缺漏）0。"""
    return sum(
        group.points for group in rubric.groups if _is_green(outcomes.get(group.probe))
    )


def score_robustness(
    rubric: RobustnessRubric, outcomes: Mapping[str, ProbeOutcome]
) -> float:
    """case 級線性計分；收集不到任何 case（分母 0）→ 0（fail-closed）。"""
    present = [outcomes[p] for p in rubric.probes if p in outcomes]
    total = sum(o.cases_total for o in present)
    if total <= 0:
        return 0.0
    passed = sum(o.cases_passed for o in present)
    return rubric.points * passed / total


def score_compatibility(
    rubric: CompatibilityRubric, outcomes: Mapping[str, ProbeOutcome]
) -> int:
    """rubric compat probes 全綠得滿分；任一不綠（含缺漏）→ 0。"""
    if rubric.probes and all(_is_green(outcomes.get(p)) for p in rubric.probes):
        return rubric.points
    return 0


def score_maintainability(
    card: IssueCard,
    file_changes: Sequence[FileChange],
    lint_new_diagnostics: int | None,
) -> MaintainabilityReport:
    """diff 4 + scope (2+1) + lint 3（spec §9.3 pin 值）。

    - `L` 只計 production 檔案（`tests/agent/**` 除外）的 added+deleted。
    - `L ≤ hi` → 4；`L > hi` → `4 × max(0, 1 − (L−hi)/hi)`；低於下限不扣分。
    - 無 SCOPE(hard) 殘留（production 變更全在 allowed_paths 內）→ 2 分。
    - `S_scope`（allowed 內、expected 外的 production LOC）= 0 → 再 1 分。
    - lint：`lint_new_diagnostics == 0` → 3 分；>0 或 None（無法驗證）→ 0。
    """
    points = card.power_rubric.maintainability.points
    if points != _MAINTAINABILITY_POINTS:
        raise EvaluatorError(
            f"maintainability points 必須為 {_MAINTAINABILITY_POINTS}（spec §9.3 pin），"
            f"card 宣告 {points}"
        )

    lo, hi = card.expected_patch_loc
    if hi <= 0:
        raise EvaluatorError(f"expected_patch_loc 上限必須 > 0：{card.expected_patch_loc}")

    production = [
        fc for fc in file_changes if not fc.path.startswith(_AGENT_TEST_PREFIX)
    ]
    loc = sum(fc.loc for fc in production)
    if loc <= hi:
        diff_size = float(_DIFF_POINTS)
    else:
        diff_size = _DIFF_POINTS * max(0.0, 1.0 - (loc - hi) / hi)

    hard_files = tuple(
        sorted(
            fc.path
            for fc in production
            if not _matches_any(fc.path, card.allowed_paths)
        )
    )
    soft_loc = sum(
        fc.loc
        for fc in production
        if _matches_any(fc.path, card.allowed_paths)
        and not _matches_any(fc.path, card.expected_paths)
    )
    scope = (0 if hard_files else _SCOPE_HARD_POINTS) + (
        _SCOPE_SOFT_POINTS if soft_loc == 0 else 0
    )

    lint = _LINT_POINTS if lint_new_diagnostics == 0 else 0

    return MaintainabilityReport(
        diff_size=diff_size,
        scope=scope,
        lint=lint,
        total=diff_size + scope + lint,
        production_loc=loc,
        scope_hard_files=hard_files,
        scope_soft_loc=soft_loc,
        lint_new_diagnostics=lint_new_diagnostics,
    )


def score_runtime_efficiency(
    rubric: RuntimeEfficiencyRubric,
    outcomes: Mapping[str, ProbeOutcome],
    reference_timings_ms: Mapping[str, float],
) -> tuple[float, tuple[PerfJudgment, ...]]:
    """每個 perf probe「通過且 wall_ms ≤ timeout_factor × reference」各得均分。

    判定以量測當下值定案並封存；reference timing 缺漏 → budget None、
    within_budget False（fail-closed）。
    """
    judgments: list[PerfJudgment] = []
    for probe_id in rubric.probes:
        outcome = outcomes.get(probe_id)
        reference = reference_timings_ms.get(probe_id)
        budget = rubric.timeout_factor * reference if reference is not None else None
        wall_ms = outcome.wall_ms if outcome is not None else 0
        judgments.append(
            PerfJudgment(
                probe_id=probe_id,
                wall_ms=wall_ms,
                budget_ms=budget,
                passed=_is_green(outcome),
                within_budget=budget is not None and wall_ms <= budget,
            )
        )
    return (
        runtime_efficiency_from_judgments(rubric.points, judgments),
        tuple(judgments),
    )


def runtime_efficiency_from_judgments(
    points: int, judgments: Sequence[PerfJudgment]
) -> float:
    """由封存判定重算 runtime_efficiency 分數（L1 重算入口，spec §12.2）。"""
    if not judgments:
        return 0.0
    earned = sum(1 for j in judgments if j.earned)
    return points * earned / len(judgments)


def score_power(
    card: IssueCard,
    outcomes: Mapping[str, ProbeOutcome],
    file_changes: Sequence[FileChange],
    *,
    lint_new_diagnostics: int | None,
    reference_timings_ms: Mapping[str, float],
) -> PowerReport:
    """依 card rubric 計算全部分項與總分（未套 hard-gate cap；見 gates.apply_power_cap）。"""
    rubric = card.power_rubric
    functional = score_functional(rubric.functional, outcomes)
    robustness = score_robustness(rubric.robustness, outcomes)
    compatibility = score_compatibility(rubric.compatibility, outcomes)
    maintainability = score_maintainability(card, file_changes, lint_new_diagnostics)
    runtime_efficiency, judgments = score_runtime_efficiency(
        rubric.runtime_efficiency, outcomes, reference_timings_ms
    )
    total = (
        functional
        + robustness
        + compatibility
        + maintainability.total
        + runtime_efficiency
    )
    return PowerReport(
        functional=functional,
        robustness=robustness,
        compatibility=compatibility,
        maintainability=maintainability.total,
        runtime_efficiency=runtime_efficiency,
        total=total,
        maintainability_breakdown=maintainability,
        perf_judgments=judgments,
    )


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(
        path == pattern or fnmatch.fnmatch(path, pattern) for pattern in patterns
    )
