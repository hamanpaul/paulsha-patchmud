"""Task 15 RED：token-efficiency 稽核指標（spec §10.3、報告 §5.6.2–§5.6.5、
F17、報告 §19.9；plan Task 15 Step 1）。

鎖定：
- `TokensPerClear = Σ T^work / Σ Clear`：零 clear → 無限大；NA 傳染——
  含 NA（None）entry 的 run 集 → work 欄 `None`、observable 欄照算（雙欄）。
- `QATY = 10^6·Σ Clear·(Power/100) / Σ T^work`：手算例；失敗 run token
  留在分母。
- `EuTB(B) = (1/B)∫₀^B R(b) db`：預算與積分網格必須來自 pre-registered
  檔（§10.4、報告 §19.9）；registered 檔缺失 → `NotRegisteredError`
  （fail-closed）；2 run、B=1000 手算例。
- `MTY_t = (Q_t − Q_{t−1}) / (ΔT_t/1000)`（報告 §5.6.5）。
- 效率排名輸出帶 `disclosure_cohort`；跨 cohort 排名請求 → raise（F17）。
- `bootstrap_ci(values, b=10000, seed)` 固定 seed 重現、seed 記錄在輸出。
"""

from __future__ import annotations

import math

import pytest

from patchmud.metrics.bootstrap import BootstrapError, bootstrap_ci
from patchmud.metrics.economy import HumanRunExcluded, RunSample
from patchmud.metrics.efficiency import (
    COHORT_FULL,
    COHORT_OBSERVABLE,
    CohortMismatchError,
    EfficiencyError,
    EfficiencyResult,
    NotRegisteredError,
    RegisteredBudget,
    eutb,
    load_eutb_budget,
    mty,
    qaty,
    rank_efficiency,
    tokens_per_clear,
)


def _run(
    *,
    clear: int = 1,
    power: float = 100.0,
    work: int | None = None,
    obs: int = 0,
    human: bool = False,
) -> RunSample:
    return RunSample(
        clear=clear,
        power=power,
        cost=None,
        work_tokens=work,
        observable_tokens=obs,
        human=human,
    )


BUDGET = RegisteredBudget(budget_tokens=1000, grid_points=256)


def _write_budget(tmp_path, text: str):
    path = tmp_path / "eutb_budget.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# TokensPerClear（報告 §5.6.2；NA 傳染 + observable 雙欄）
# ---------------------------------------------------------------------------


class TestTokensPerClear:
    def test_hand_computed_full_cohort(self) -> None:
        # (400+600)/1 = 1000；observable (300+500)/1 = 800；全揭露 cohort
        runs = [
            _run(clear=1, work=400, obs=300),
            _run(clear=0, work=600, obs=500),
        ]
        r = tokens_per_clear(runs)
        assert isinstance(r, EfficiencyResult)
        assert r.value == pytest.approx(1000.0)
        assert r.observable == pytest.approx(800.0)
        assert r.disclosure_cohort == COHORT_FULL

    def test_na_propagates_with_observable_dual_column(self) -> None:
        # 含 NA entry 的 run 集：work 欄 NA（None）、observable 欄照算（雙欄）
        runs = [
            _run(clear=1, work=None, obs=300),
            _run(clear=1, work=600, obs=500),
        ]
        r = tokens_per_clear(runs)
        assert r.value is None
        assert r.observable == pytest.approx(400.0)
        assert r.disclosure_cohort == COHORT_OBSERVABLE

    def test_zero_clears_is_infinite(self) -> None:
        runs = [_run(clear=0, work=400, obs=300)]
        r = tokens_per_clear(runs)
        assert r.value == math.inf and r.observable == math.inf

    def test_human_run_rejected(self) -> None:
        with pytest.raises(HumanRunExcluded):
            tokens_per_clear([_run(clear=1, work=1, obs=1), _run(human=True)])

    def test_empty_runs_rejected(self) -> None:
        with pytest.raises(EfficiencyError):
            tokens_per_clear([])


# ---------------------------------------------------------------------------
# QATY（報告 §5.6.3）
# ---------------------------------------------------------------------------


class TestQaty:
    def test_hand_computed_example(self) -> None:
        # 10^6·(1·0.8 + 0) / (400k+600k) = 0.8；observable 分母 800k → 1.0
        runs = [
            _run(clear=1, power=80.0, work=400_000, obs=300_000),
            _run(clear=0, power=50.0, work=600_000, obs=500_000),
        ]
        r = qaty(runs)
        assert r.value == pytest.approx(0.8)
        assert r.observable == pytest.approx(1.0)
        assert r.disclosure_cohort == COHORT_FULL

    def test_failed_run_tokens_stay_in_denominator(self) -> None:
        solo = qaty([_run(clear=1, power=80.0, work=400_000, obs=400_000)])
        with_fail = qaty(
            [
                _run(clear=1, power=80.0, work=400_000, obs=400_000),
                _run(clear=0, power=50.0, work=400_000, obs=400_000),
            ]
        )
        assert solo.value == pytest.approx(2.0)
        assert with_fail.value == pytest.approx(1.0)

    def test_na_propagates_with_observable_dual_column(self) -> None:
        runs = [
            _run(clear=1, power=100.0, work=None, obs=500_000),
            _run(clear=1, power=100.0, work=500_000, obs=500_000),
        ]
        r = qaty(runs)
        assert r.value is None
        assert r.observable == pytest.approx(2.0)
        assert r.disclosure_cohort == COHORT_OBSERVABLE

    def test_zero_token_denominator_rejected(self) -> None:
        with pytest.raises(EfficiencyError):
            qaty([_run(clear=1, work=0, obs=0)])

    def test_human_run_rejected(self) -> None:
        with pytest.raises(HumanRunExcluded):
            qaty([_run(human=True)])


# ---------------------------------------------------------------------------
# EuTB（報告 §5.6.4、§19.9；registered fail-closed）
# ---------------------------------------------------------------------------


class TestEutb:
    def test_hand_computed_small_example(self) -> None:
        # 2 run、B=1000、網格 256：clear run T^work=500 →
        # R(b)=0.5 於 b≥500；grid b_k=1000·k/256（k=1..256）中 k≥128 共 129 點
        # EuTB = 129/256 × 0.5 = 129/512 = 0.251953125
        runs = [
            _run(clear=1, work=500, obs=500),
            _run(clear=0, work=300, obs=300),
        ]
        r = eutb(runs, BUDGET)
        assert r.value == pytest.approx(129 / 512)
        assert r.observable == pytest.approx(129 / 512)
        assert r.disclosure_cohort == COHORT_FULL

    def test_over_budget_clear_scores_zero(self) -> None:
        runs = [_run(clear=1, work=2000, obs=2000)]
        r = eutb(runs, BUDGET)
        assert r.value == pytest.approx(0.0)

    def test_missing_registered_file_rejected(self, tmp_path) -> None:
        # registered 檔缺失 → EuTB 輸出拒絕（fail-closed，報告 §19.9）
        runs = [_run(clear=1, work=500, obs=500)]
        with pytest.raises(NotRegisteredError):
            eutb(runs, tmp_path / "missing" / "eutb_budget.yaml")

    def test_loads_registered_budget_from_path(self, tmp_path) -> None:
        path = _write_budget(
            tmp_path,
            "schema_version: 1\nbudget_tokens: 1000\ngrid_points: 256\n",
        )
        runs = [
            _run(clear=1, work=500, obs=500),
            _run(clear=0, work=300, obs=300),
        ]
        assert eutb(runs, path).value == pytest.approx(129 / 512)

    def test_invalid_registered_content_rejected(self, tmp_path) -> None:
        path = _write_budget(
            tmp_path, "schema_version: 99\nbudget_tokens: 1000\ngrid_points: 256\n"
        )
        with pytest.raises(NotRegisteredError):
            load_eutb_budget(path)

    def test_nonpositive_budget_rejected(self, tmp_path) -> None:
        path = _write_budget(
            tmp_path, "schema_version: 1\nbudget_tokens: 0\ngrid_points: 256\n"
        )
        with pytest.raises(NotRegisteredError):
            load_eutb_budget(path)

    def test_na_propagates_with_observable_dual_column(self) -> None:
        runs = [
            _run(clear=1, work=None, obs=500),
            _run(clear=0, work=300, obs=300),
        ]
        r = eutb(runs, BUDGET)
        assert r.value is None
        assert r.observable == pytest.approx(129 / 512)
        assert r.disclosure_cohort == COHORT_OBSERVABLE

    def test_human_run_rejected(self) -> None:
        with pytest.raises(HumanRunExcluded):
            eutb([_run(human=True)], BUDGET)


# ---------------------------------------------------------------------------
# MTY（報告 §5.6.5）
# ---------------------------------------------------------------------------


class TestMty:
    def test_hand_computed_series(self) -> None:
        # (70−50)/(2000/1000)=10；(60−70)/(500/1000)=−20
        scores = [(50.0, 0), (70.0, 2000), (60.0, 500)]
        assert mty(scores) == (pytest.approx(10.0), pytest.approx(-20.0))

    def test_na_delta_yields_none_element(self) -> None:
        scores = [(50.0, 0), (70.0, None), (80.0, 1000)]
        result = mty(scores)
        assert result[0] is None
        assert result[1] == pytest.approx(10.0)

    def test_nonpositive_delta_rejected(self) -> None:
        with pytest.raises(EfficiencyError):
            mty([(50.0, 0), (70.0, 0)])

    def test_baseline_only_returns_empty(self) -> None:
        assert mty([(50.0, 0)]) == ()

    def test_empty_rejected(self) -> None:
        with pytest.raises(EfficiencyError):
            mty([])


# ---------------------------------------------------------------------------
# Disclosure cohort 排名（F17）
# ---------------------------------------------------------------------------


def _result(metric: str, value, observable: float, cohort: str) -> EfficiencyResult:
    return EfficiencyResult(
        metric=metric, value=value, observable=observable, disclosure_cohort=cohort
    )


class TestRankEfficiency:
    def test_cross_cohort_ranking_rejected(self) -> None:
        # 跨 cohort 排名 → 「少揭露 reasoning」的模型憑空得利（F17）
        results = {
            "model-a": _result("qaty", 1.0, 1.2, COHORT_FULL),
            "model-b": _result("qaty", None, 2.0, COHORT_OBSERVABLE),
        }
        with pytest.raises(CohortMismatchError):
            rank_efficiency(results)

    def test_ranks_lower_better_metric(self) -> None:
        results = {
            "model-a": _result("tokens_per_clear", 900.0, 800.0, COHORT_FULL),
            "model-b": _result("tokens_per_clear", 500.0, 450.0, COHORT_FULL),
        }
        assert rank_efficiency(results) == ("model-b", "model-a")

    def test_ranks_higher_better_metric(self) -> None:
        results = {
            "model-a": _result("qaty", 0.5, 0.6, COHORT_FULL),
            "model-b": _result("qaty", 2.0, 2.1, COHORT_FULL),
        }
        assert rank_efficiency(results) == ("model-b", "model-a")

    def test_observable_cohort_ranks_by_observable_column(self) -> None:
        results = {
            "model-a": _result("qaty", None, 0.5, COHORT_OBSERVABLE),
            "model-b": _result("qaty", None, 2.0, COHORT_OBSERVABLE),
        }
        assert rank_efficiency(results) == ("model-b", "model-a")

    def test_mixed_metric_rejected(self) -> None:
        results = {
            "model-a": _result("qaty", 1.0, 1.0, COHORT_FULL),
            "model-b": _result("eutb", 0.5, 0.5, COHORT_FULL),
        }
        with pytest.raises(EfficiencyError):
            rank_efficiency(results)


# ---------------------------------------------------------------------------
# Bootstrap CI（報告 §5.6.7：全部效率指標附 encounter-level CI；B=10,000）
# ---------------------------------------------------------------------------


class TestBootstrapCi:
    VALUES = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]

    def test_fixed_seed_reproducible(self) -> None:
        a = bootstrap_ci(self.VALUES, b=500, seed=42)
        b = bootstrap_ci(self.VALUES, b=500, seed=42)
        assert a == b

    def test_different_seed_differs(self) -> None:
        a = bootstrap_ci(self.VALUES, b=500, seed=1)
        b = bootstrap_ci(self.VALUES, b=500, seed=2)
        assert (a.low, a.high) != (b.low, b.high)

    def test_default_b_is_10000_and_seed_recorded(self) -> None:
        ci = bootstrap_ci(self.VALUES, seed=7)
        assert ci.b == 10_000 and ci.seed == 7

    def test_ci_brackets_point_estimate(self) -> None:
        ci = bootstrap_ci(self.VALUES, b=500, seed=42)
        assert ci.low <= ci.point <= ci.high
        assert ci.point == pytest.approx(4.5)

    def test_degenerate_constant_values(self) -> None:
        ci = bootstrap_ci([3.0, 3.0, 3.0], b=200, seed=1)
        assert ci.low == ci.high == 3.0

    def test_empty_values_rejected(self) -> None:
        with pytest.raises(BootstrapError):
            bootstrap_ci([], seed=1)
