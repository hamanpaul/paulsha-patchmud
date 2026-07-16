"""Task 5 RED：Power rubric 公式逐條 pin（spec §9.3、F15）。

以合成 `ProbeOutcome` 鎖定每一分：
- functional：group all-or-nothing（error 也不綠）。
- robustness：case 級 `15 × passed/total`（分母 = 全部 robustness probes 的 case 總數）。
- compatibility：全綠 10 / 任一紅 0。
- maintainability 三小項：diff 大小 4 分（`L=100, hi=80 → 4×(1−20/80)=3.0`、
  低於下限不扣分）、SCOPE(hard) 殘留扣 2、`S_scope>0` 扣 1、lint 新增 diagnostics 扣 3。
- runtime_efficiency：`runtime ≤ timeout_factor × reference` 判定；分數以
  量測當下 outcome 封存值（PerfJudgment）輸出，L1 重算只引用封存值（spec §12.2）。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from patchmud.evaluator.power import (
    EvaluatorError,
    FileChange,
    PowerReport,
    runtime_efficiency_from_judgments,
    score_compatibility,
    score_functional,
    score_maintainability,
    score_power,
    score_robustness,
    score_runtime_efficiency,
)
from tests.evaluator.helpers import (
    API,
    CR1,
    CR2,
    EDGES,
    HIDDEN_COMPAT,
    PERF1,
    PERF2,
    green,
    make_card,
    make_outcome,
)

CARD = make_card()
RUBRIC = CARD.power_rubric

TIMINGS = {PERF1: 500, PERF2: 1000}  # ms；timeout_factor=3.0 → 預算 1500 / 3000


# ---------------------------------------------------------------------------
# functional 60：group all-or-nothing
# ---------------------------------------------------------------------------


class TestFunctional:
    def test_all_groups_green_full_points(self):
        assert score_functional(RUBRIC.functional, green(CR1, CR2)) == 60

    def test_failed_group_scores_zero_others_keep(self):
        outcomes = green(CR1) | {CR2: make_outcome("failed")}
        assert score_functional(RUBRIC.functional, outcomes) == 40

    def test_error_group_is_not_green(self):
        # error ≠ failed，但對 all-or-nothing 得分同樣不綠（三態原值另存於 status）
        outcomes = {CR1: make_outcome("error", cases_total=0), **green(CR2)}
        assert score_functional(RUBRIC.functional, outcomes) == 20

    def test_missing_outcome_fail_closed_zero(self):
        assert score_functional(RUBRIC.functional, green(CR2)) == 20


# ---------------------------------------------------------------------------
# robustness 15：case 級線性計分
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_case_level_linear_share(self):
        outcomes = {EDGES: make_outcome("failed", cases_total=10, cases_passed=7)}
        assert score_robustness(RUBRIC.robustness, outcomes) == 10.5  # 15×7/10

    def test_denominator_sums_all_robustness_probes(self):
        rubric = make_card(robustness_probes=(EDGES, HIDDEN_COMPAT)).power_rubric
        outcomes = {
            EDGES: make_outcome("passed", cases_total=3, cases_passed=3),
            HIDDEN_COMPAT: make_outcome("failed", cases_total=2, cases_passed=1),
        }
        assert score_robustness(rubric.robustness, outcomes) == 12.0  # 15×4/5

    def test_all_cases_passed_full_points(self):
        outcomes = {EDGES: make_outcome("passed", cases_total=4, cases_passed=4)}
        assert score_robustness(RUBRIC.robustness, outcomes) == 15.0

    def test_collection_error_zero_cases_scores_zero(self):
        outcomes = {EDGES: make_outcome("error", cases_total=0)}
        assert score_robustness(RUBRIC.robustness, outcomes) == 0.0


# ---------------------------------------------------------------------------
# compatibility 10：全綠 10 / 任一紅 0
# ---------------------------------------------------------------------------


class TestCompatibility:
    def test_all_green_full_points(self):
        assert score_compatibility(RUBRIC.compatibility, green(API, HIDDEN_COMPAT)) == 10

    def test_any_failed_zero(self):
        outcomes = green(API) | {HIDDEN_COMPAT: make_outcome("failed")}
        assert score_compatibility(RUBRIC.compatibility, outcomes) == 0

    def test_any_error_zero(self):
        outcomes = green(HIDDEN_COMPAT) | {API: make_outcome("error", cases_total=0)}
        assert score_compatibility(RUBRIC.compatibility, outcomes) == 0

    def test_missing_outcome_fail_closed_zero(self):
        assert score_compatibility(RUBRIC.compatibility, green(API)) == 0


# ---------------------------------------------------------------------------
# maintainability 10：diff 4 + scope (2+1) + lint 3
# ---------------------------------------------------------------------------


def _changes(*items: tuple[str, int, int]) -> tuple[FileChange, ...]:
    return tuple(FileChange(path=p, added=a, deleted=d) for p, a, d in items)


class TestMaintainability:
    def test_plan_example_L100_hi80_gives_3_0(self):
        # plan Task 5 指定例：L=100, hi=80 → 4×(1−20/80)=3.0
        report = score_maintainability(
            CARD, _changes(("src/snapshot.py", 60, 40)), lint_new_diagnostics=0
        )
        assert report.diff_size == 3.0
        assert report.production_loc == 100
        assert report.total == 3.0 + 3 + 3

    def test_L_at_or_below_hi_full_4(self):
        report = score_maintainability(
            CARD, _changes(("src/snapshot.py", 50, 30)), lint_new_diagnostics=0
        )
        assert report.diff_size == 4.0

    def test_L_below_lower_bound_no_deduction(self):
        # 低於下限（lo=30）不扣分
        report = score_maintainability(
            CARD, _changes(("src/snapshot.py", 3, 1)), lint_new_diagnostics=0
        )
        assert report.diff_size == 4.0

    def test_L_at_double_hi_scores_zero(self):
        report = score_maintainability(
            CARD, _changes(("src/snapshot.py", 160, 0)), lint_new_diagnostics=0
        )
        assert report.diff_size == 0.0

    def test_agent_tests_excluded_from_L(self):
        report = score_maintainability(
            CARD,
            _changes(("src/snapshot.py", 40, 0), ("tests/agent/test_mine.py", 300, 0)),
            lint_new_diagnostics=0,
        )
        assert report.production_loc == 40
        assert report.diff_size == 4.0

    def test_scope_hard_residual_loses_2(self):
        # production 變更落在 allowed_paths 外 → SCOPE(hard) 殘留
        report = score_maintainability(
            CARD,
            _changes(("src/snapshot.py", 10, 0), ("lib/util.py", 5, 0)),
            lint_new_diagnostics=0,
        )
        assert report.scope == 1  # 失去 2 分、S_scope=0 保 1 分
        assert report.scope_hard_files == ("lib/util.py",)

    def test_scope_soft_loc_positive_loses_1(self):
        # allowed 內但 expected 外 → S_scope>0
        report = score_maintainability(
            CARD,
            _changes(("src/snapshot.py", 10, 0), ("src/other.py", 4, 1)),
            lint_new_diagnostics=0,
        )
        assert report.scope == 2
        assert report.scope_soft_loc == 5

    def test_clean_scope_full_3(self):
        report = score_maintainability(
            CARD, _changes(("src/snapshot.py", 10, 0)), lint_new_diagnostics=0
        )
        assert report.scope == 3

    def test_lint_new_diagnostics_zero_gives_3(self):
        report = score_maintainability(CARD, (), lint_new_diagnostics=0)
        assert report.lint == 3

    def test_lint_new_diagnostics_positive_gives_0(self):
        report = score_maintainability(CARD, (), lint_new_diagnostics=2)
        assert report.lint == 0

    def test_lint_unverifiable_fail_closed_0(self):
        # ruff 無法執行 → 無法證明零新增 → 0（fail-closed）
        report = score_maintainability(CARD, (), lint_new_diagnostics=None)
        assert report.lint == 0

    def test_nonstandard_points_fail_closed(self):
        card = make_card(maintainability_points=8)
        with pytest.raises(EvaluatorError):
            score_maintainability(card, (), lint_new_diagnostics=0)


# ---------------------------------------------------------------------------
# runtime_efficiency 5：runtime ≤ timeout_factor × reference；封存 outcome
# ---------------------------------------------------------------------------


class TestRuntimeEfficiency:
    def test_both_within_budget_full_points(self):
        outcomes = {
            PERF1: make_outcome("passed", wall_ms=900),
            PERF2: make_outcome("passed", wall_ms=2900),
        }
        score, judgments = score_runtime_efficiency(
            RUBRIC.runtime_efficiency, outcomes, TIMINGS
        )
        assert score == 5.0
        assert [j.probe_id for j in judgments] == [PERF1, PERF2]
        j1 = judgments[0]
        assert (j1.wall_ms, j1.budget_ms, j1.passed, j1.within_budget) == (
            900,
            1500.0,
            True,
            True,
        )

    def test_exceeding_budget_earns_zero_share(self):
        outcomes = {
            PERF1: make_outcome("passed", wall_ms=1501),  # > 3.0×500
            PERF2: make_outcome("passed", wall_ms=100),
        }
        score, judgments = score_runtime_efficiency(
            RUBRIC.runtime_efficiency, outcomes, TIMINGS
        )
        assert score == 2.5
        assert judgments[0].within_budget is False
        assert judgments[0].passed is True

    def test_failed_probe_earns_zero_even_if_fast(self):
        outcomes = {
            PERF1: make_outcome("failed", wall_ms=10),
            PERF2: make_outcome("passed", wall_ms=10),
        }
        score, judgments = score_runtime_efficiency(
            RUBRIC.runtime_efficiency, outcomes, TIMINGS
        )
        assert score == 2.5
        assert judgments[0].passed is False

    def test_missing_reference_timing_fail_closed(self):
        outcomes = {
            PERF1: make_outcome("passed", wall_ms=10),
            PERF2: make_outcome("passed", wall_ms=10),
        }
        score, judgments = score_runtime_efficiency(
            RUBRIC.runtime_efficiency, outcomes, {PERF2: 1000}
        )
        assert score == 2.5
        assert judgments[0].budget_ms is None
        assert judgments[0].within_budget is False

    def test_error_probe_earns_zero(self):
        outcomes = {
            PERF1: make_outcome("error", cases_total=0, wall_ms=10),
            PERF2: make_outcome("passed", wall_ms=10),
        }
        score, _ = score_runtime_efficiency(RUBRIC.runtime_efficiency, outcomes, TIMINGS)
        assert score == 2.5

    def test_l1_recompute_uses_archived_judgments_only(self):
        # spec §12.2：分數判定在原始 run 當下定案並封存；重算只引用封存 outcome，
        # 不重新量測——竄改封存的 wall_ms 不改變重算結果（判定布林已定案）。
        outcomes = {
            PERF1: make_outcome("passed", wall_ms=900),
            PERF2: make_outcome("passed", wall_ms=3100),  # 超budget
        }
        score, judgments = score_runtime_efficiency(
            RUBRIC.runtime_efficiency, outcomes, TIMINGS
        )
        assert score == 2.5
        assert runtime_efficiency_from_judgments(5, judgments) == score

        tampered = (replace(judgments[0], wall_ms=999_999), judgments[1])
        assert runtime_efficiency_from_judgments(5, tampered) == score


# ---------------------------------------------------------------------------
# score_power：分項加總與封存欄位 wiring
# ---------------------------------------------------------------------------


class TestScorePower:
    def _outcomes(self) -> dict:
        return (
            green(CR1, CR2, API, HIDDEN_COMPAT)
            | {EDGES: make_outcome("failed", cases_total=10, cases_passed=7)}
            | {
                PERF1: make_outcome("passed", wall_ms=900),
                PERF2: make_outcome("passed", wall_ms=9999),
            }
        )

    def test_total_is_sum_of_subscores(self):
        report = score_power(
            CARD,
            self._outcomes(),
            _changes(("src/snapshot.py", 60, 40)),  # L=100 → 3.0
            lint_new_diagnostics=0,
            reference_timings_ms=TIMINGS,
        )
        assert isinstance(report, PowerReport)
        assert report.functional == 60
        assert report.robustness == 10.5
        assert report.compatibility == 10
        assert report.maintainability == 9.0  # 3.0 + 3 + 3
        assert report.runtime_efficiency == 2.5
        assert report.total == 92.0

    def test_report_archives_breakdown_and_judgments(self):
        report = score_power(
            CARD,
            self._outcomes(),
            _changes(("src/snapshot.py", 10, 0)),
            lint_new_diagnostics=None,
            reference_timings_ms=TIMINGS,
        )
        assert report.maintainability_breakdown.lint == 0
        assert report.maintainability_breakdown.lint_new_diagnostics is None
        assert [j.probe_id for j in report.perf_judgments] == [PERF1, PERF2]

    def test_perfect_run_scores_100(self):
        outcomes = (
            green(CR1, CR2, API, HIDDEN_COMPAT)
            | {EDGES: make_outcome("passed", cases_total=5, cases_passed=5)}
            | {
                PERF1: make_outcome("passed", wall_ms=100),
                PERF2: make_outcome("passed", wall_ms=100),
            }
        )
        report = score_power(
            CARD,
            outcomes,
            _changes(("src/snapshot.py", 20, 10)),
            lint_new_diagnostics=0,
            reference_timings_ms=TIMINGS,
        )
        assert report.total == 100.0
