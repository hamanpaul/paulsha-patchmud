"""Task 15 RED：經濟指標（spec §10.2、報告 §5.4–§5.5、F16–F18；plan Task 15 Step 1）。

鎖定：
- `CostPerClear = Σ C_run / N_clears`：零 clear → 無限大且失敗 run 成本
  **不剔除**（留在分子）；金額全程 `decimal.Decimal`（invariant 6）。
- `Economy_i = 100·min(1, sqrt(C_ref/C_run))`（報告 §5.5）；`reference_cost`
  未校準（None）→ Economy 輸出 `None`（NA），只報原始成本（§10.2）。
- `C_run = 0` 是計價設定錯誤，fail-closed（F18 除零防線）。
- 含 human run 的聚合集合 raise `HumanRunExcluded`（human run 不進 ranked）。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from patchmud.metrics.economy import (
    EconomyError,
    HumanRunExcluded,
    RunSample,
    cost_per_clear,
    economy_score,
)
from tests.evaluator.helpers import make_card

CARD = make_card()  # reference_cost=None（未校準）


def _run(
    *,
    clear: int = 1,
    power: float = 100.0,
    cost: Decimal | None = None,
    work: int | None = None,
    obs: int = 0,
    human: bool = False,
) -> RunSample:
    return RunSample(
        clear=clear,
        power=power,
        cost=cost,
        work_tokens=work,
        observable_tokens=obs,
        human=human,
    )


# ---------------------------------------------------------------------------
# CostPerClear（報告 §5.4）
# ---------------------------------------------------------------------------


class TestCostPerClear:
    def test_zero_clear_is_infinite_and_costs_not_dropped(self) -> None:
        # 全敗：成本留在分子、分母 0 → 無限大（不是 error、不是 0）
        runs = [_run(clear=0, cost=Decimal("3")), _run(clear=0, cost=Decimal("2"))]
        result = cost_per_clear(runs)
        assert isinstance(result, Decimal)
        assert result.is_infinite() and result > 0

    def test_failed_run_costs_stay_in_numerator(self) -> None:
        # 1 clear + 1 fail：分子 2+3、分母 1 → 5（失敗 run 不剔除）
        runs = [_run(clear=1, cost=Decimal("2")), _run(clear=0, cost=Decimal("3"))]
        assert cost_per_clear(runs) == Decimal("5")

    def test_returns_decimal(self) -> None:
        runs = [_run(clear=1, cost=Decimal("2.50")), _run(clear=1, cost=Decimal("1.50"))]
        result = cost_per_clear(runs)
        assert isinstance(result, Decimal)
        assert result == Decimal("2")

    def test_human_run_in_set_rejected(self) -> None:
        runs = [_run(clear=1, cost=Decimal("2")), _run(clear=0, human=True)]
        with pytest.raises(HumanRunExcluded):
            cost_per_clear(runs)

    def test_empty_runs_rejected(self) -> None:
        with pytest.raises(EconomyError):
            cost_per_clear([])

    def test_missing_cost_rejected(self) -> None:
        with pytest.raises(EconomyError):
            cost_per_clear([_run(clear=1, cost=None)])

    def test_zero_cost_rejected_f18(self) -> None:
        # C_run = 0 是計價設定錯誤（F18 除零防線）
        with pytest.raises(EconomyError):
            cost_per_clear([_run(clear=1, cost=Decimal("0"))])


# ---------------------------------------------------------------------------
# RunSample 契約（fail-closed）
# ---------------------------------------------------------------------------


class TestRunSample:
    def test_float_cost_rejected(self) -> None:
        # 金額禁止 float（invariant 6）
        with pytest.raises(EconomyError):
            _run(clear=1, cost=2.5)  # type: ignore[arg-type]

    def test_invalid_clear_rejected(self) -> None:
        with pytest.raises(EconomyError):
            _run(clear=2)

    def test_negative_work_tokens_rejected(self) -> None:
        with pytest.raises(EconomyError):
            _run(work=-1)

    def test_power_out_of_range_rejected(self) -> None:
        with pytest.raises(EconomyError):
            _run(power=101.0)


# ---------------------------------------------------------------------------
# Economy 分數（報告 §5.5；reference_cost 未校準 → NA）
# ---------------------------------------------------------------------------


class TestEconomyScore:
    def test_uncalibrated_reference_cost_yields_none(self) -> None:
        # pilot 前不產 0–100 Economy 分數（§10.2）——輸出 NA，不是 0
        run = _run(clear=1, cost=Decimal("2"))
        assert economy_score(run, CARD) is None

    def test_hand_computed_example(self) -> None:
        # C_ref=4、C_run=16 → 100·min(1, sqrt(0.25)) = 50
        card = replace(CARD, reference_cost=Decimal("4"))
        run = _run(clear=1, cost=Decimal("16"))
        assert economy_score(run, card) == 50

    def test_cheap_run_capped_at_100(self) -> None:
        card = replace(CARD, reference_cost=Decimal("4"))
        run = _run(clear=1, cost=Decimal("1"))
        assert economy_score(run, card) == 100

    def test_rounding_half_up(self) -> None:
        # C_ref=4、C_run=9 → 100·sqrt(4/9) = 66.66… → 67
        card = replace(CARD, reference_cost=Decimal("4"))
        run = _run(clear=1, cost=Decimal("9"))
        assert economy_score(run, card) == 67

    def test_failed_run_scores_zero(self) -> None:
        # 報告 §5.4：沒有成功通關 → Economy 0
        card = replace(CARD, reference_cost=Decimal("4"))
        run = _run(clear=0, cost=Decimal("2"))
        assert economy_score(run, card) == 0

    def test_human_run_rejected(self) -> None:
        card = replace(CARD, reference_cost=Decimal("4"))
        with pytest.raises(HumanRunExcluded):
            economy_score(_run(clear=1, human=True), card)

    def test_zero_cost_rejected_f18(self) -> None:
        card = replace(CARD, reference_cost=Decimal("4"))
        with pytest.raises(EconomyError):
            economy_score(_run(clear=1, cost=Decimal("0")), card)

    def test_returns_int(self) -> None:
        card = replace(CARD, reference_cost=Decimal("4"))
        assert isinstance(economy_score(_run(clear=1, cost=Decimal("16")), card), int)
