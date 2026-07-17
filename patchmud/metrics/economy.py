"""經濟指標：CostPerClear 與 Economy 分數（spec §10.2、報告 §5.4–§5.5、F16–F18）。

- 只讀 run 落盤資料的聚合視圖（result.yaml + ledger），不執行 candidate
  code、不寫回 run 目錄（plan invariant 3、4）。
- ``CostPerClear = Σ C_run / N_clears``（報告 §5.4）：失敗 run 的成本保留在
  分子、不增加分母；零 clear → ``Decimal("Infinity")``（不是 error、不是 0）。
- ``Economy_i = 100·min(1, sqrt(C_ref/C_run))``（報告 §5.5）；
  ``reference_cost`` 未校準（None，只能由 §10.4 estimator 產出）→ 輸出
  ``None``（NA），只報原始成本——pilot 前不產 0–100 Economy 分數（§10.2）。
- ``C_run = 0`` 是計價設定錯誤，fail-closed（F18 除零防線）——**無條件**：
  ``economy_score`` 對失敗 run（clear=0）一樣先驗 cost，與 ``cost_per_clear``
  一致，不得以 Economy=0 靜默放行計價設定錯誤（spec §10.2）。
- 零 token 樣本 fail-closed（§10.1）：真實 model run 的 billed/observable
  tokens 永遠可得且為正；``observable_tokens = 0``（含漏填預設）與
  ``work_tokens = 0``（NA 必須記 None，不得記 0）皆為資料錯誤，
  ``RunSample`` 構造即拒絕——這是 tokens_per_clear／qaty／eutb 共用的
  單一防線，否則零 token run 以最佳值直接奪 ranked 榜首（fail-open）。
- 金額全程 ``decimal.Decimal``，禁止 float（plan invariant 6）。
- human run（``patchmud play``）不進 ranked 聚合：含 human run 的集合一律
  raise ``HumanRunExcluded``。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from patchmud.deck.model import IssueCard

__all__ = [
    "EconomyError",
    "HumanRunExcluded",
    "RunSample",
    "cost_per_clear",
    "economy_score",
]

_INFINITY = Decimal("Infinity")
_ONE = Decimal(1)
_HUNDRED = Decimal(100)


class EconomyError(ValueError):
    """經濟指標契約違反：欄位缺漏、金額型別錯誤、C_run=0（F18）等。"""


class HumanRunExcluded(EconomyError):
    """聚合集合含 human run（``human: true``）：human run 不進 ranked 指標。"""


@dataclass(frozen=True)
class RunSample:
    """單場 run 的指標聚合視圖（result.yaml + ledger 聚合；NA 以 None 表示）。

    - ``cost``：C_run（§10.2）；只允許 ``decimal.Decimal``（invariant 6），
      human run 或尚無 pricing snapshot 時為 None。
    - ``work_tokens``：T^work（報告 §5.6.1）；任一互斥欄位 NA → None（傳染）。
      0 不是合法量測值（NA 不得記 0，§10.1）→ 拒絕。
    - ``observable_tokens``：common-observable（input + output_visible，
      §10.1）；跨 cohort 只發布此欄。model run 必為正——預設 0 讓漏填
      立即 fail-closed；只有 human run（ledger 全 NA）允許 0。
    """

    clear: int
    power: float
    cost: Decimal | None = None
    work_tokens: int | None = None
    observable_tokens: int = 0
    human: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.clear, bool) or self.clear not in (0, 1):
            raise EconomyError(f"clear 必須是 0 或 1：{self.clear!r}")
        if isinstance(self.power, bool) or not isinstance(self.power, (int, float)):
            raise EconomyError(f"power 必須是數字：{self.power!r}")
        if not 0 <= self.power <= 100:
            raise EconomyError(f"power 必須在 0–100：{self.power!r}")
        if self.cost is not None:
            if not isinstance(self.cost, Decimal):
                # 金額禁止 float／int 混入（invariant 6）
                raise EconomyError(f"cost 必須是 Decimal 或 None：{self.cost!r}")
            if not self.cost.is_finite() or self.cost < 0:
                raise EconomyError(f"cost 必須是非負有限值：{self.cost!r}")
        if self.work_tokens is not None and (
            isinstance(self.work_tokens, bool)
            or not isinstance(self.work_tokens, int)
            or self.work_tokens <= 0
        ):
            # 0 不是合法量測值：NA 必須記 None、不得記 0（§10.1）
            raise EconomyError(
                f"work_tokens 必須是正整數或 None（NA 記 None 不記 0，"
                f"§10.1）：{self.work_tokens!r}"
            )
        if (
            isinstance(self.observable_tokens, bool)
            or not isinstance(self.observable_tokens, int)
            or self.observable_tokens < 0
        ):
            raise EconomyError(
                f"observable_tokens 必須是非負整數：{self.observable_tokens!r}"
            )
        if not isinstance(self.human, bool):
            raise EconomyError(f"human 必須是 bool：{self.human!r}")
        if not self.human and self.observable_tokens == 0:
            # 真實 model run 的 billed/observable tokens 永遠可得且為正
            # （§10.1）；0 只可能是漏填或聚合錯誤——fail-closed，否則零
            # token 樣本以最佳值奪 ranked 榜首（review finding：fail-open）
            raise EconomyError(
                "observable_tokens = 0：model run 的 billed/observable tokens "
                "必為正（§10.1）；0 只可能是漏填或聚合錯誤（fail-closed）"
            )


def require_model_runs(runs: Sequence[RunSample], *, context: str) -> None:
    """ranked 聚合共用前置檢查：非空且不含 human run。"""
    if not runs:
        raise EconomyError(f"{context}：runs 不可為空")
    if any(run.human for run in runs):
        raise HumanRunExcluded(
            f"{context}：集合含 human run，不進 ranked 指標（請先剔除）"
        )


def _require_cost(run: RunSample, *, context: str) -> Decimal:
    if run.cost is None:
        raise EconomyError(f"{context}：run 缺 C_run（cost=None）")
    if run.cost == 0:
        # C_run = 0 是計價設定錯誤（F18 除零防線；spec §10.2）
        raise EconomyError(f"{context}：C_run = 0 為計價設定錯誤（F18）")
    return run.cost


def cost_per_clear(runs: Sequence[RunSample]) -> Decimal:
    """``CostPerClear = Σ C_run / N_clears``（報告 §5.4）。

    失敗 run 成本不剔除；零 clear → ``Decimal("Infinity")``。
    """
    require_model_runs(runs, context="cost_per_clear")
    total = Decimal(0)
    clears = 0
    for run in runs:
        total += _require_cost(run, context="cost_per_clear")
        clears += run.clear
    if clears == 0:
        return _INFINITY
    return total / Decimal(clears)


def economy_score(run: RunSample, card: IssueCard) -> int | None:
    """``Economy = 100·min(1, sqrt(C_ref/C_run))``（報告 §5.5）。

    - cost 先驗、**無條件**（F18：``C_run = 0``／缺漏是設定錯誤，engine
      拒絕，spec §10.2）——失敗 run 不得以 Economy=0 靜默放行，與
      ``cost_per_clear`` 對每一 run（含失敗）驗 cost 的行為一致。
    - ``card.reference_cost`` 未校準（None）→ ``None``（NA，只報原始成本）。
    - ``clear = 0`` → 0（報告 §5.4）。
    """
    if run.human:
        raise HumanRunExcluded("economy_score：human run 不進 ranked 指標")
    cost = _require_cost(run, context="economy_score")
    if card.reference_cost is None:
        return None
    if run.clear == 0:
        return 0
    ratio = card.reference_cost / cost
    capped = min(_ONE, ratio.sqrt())
    return int((_HUNDRED * capped).to_integral_value(rounding=ROUND_HALF_UP))
