"""Run 成本計算（spec §10.2、報告 §5.2、F16/F18）。

- 計費一律以 billed totals：cached 已揭露時拆 cached 價，其餘 billed input
  以 uncached 價計；billed output（含不可拆的 reasoning）以 output 價計，
  帳單永遠不會錯（F17）。
- 含 ``per_request × api_calls``、``per_tool_call × tool_calls`` 與
  每次呼叫最低消費（F16）。
- ``C_run = 0``（有呼叫但總價為零）是設定錯誤，fail-closed（F18 除零防線）。
- 金額全程 ``decimal.Decimal``，禁止 float 累加。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from patchmud.ledger.pricing import PricingSnapshot
from patchmud.ledger.tokens import LedgerEntry, LedgerError

_MTOK = Decimal(10**6)
_ZERO = Decimal("0")


@dataclass(frozen=True)
class RunCost:
    """MVP run 成本：C_run = C_model + C_reviewer（§10.2）。"""

    c_model: Decimal
    c_reviewer: Decimal

    @property
    def total(self) -> Decimal:
        return self.c_model + self.c_reviewer


def entry_cost(entry: LedgerEntry, snapshot: PricingSnapshot) -> Decimal:
    """單筆呼叫成本（billed totals 計費；每次呼叫套最低消費下限）。"""
    if entry.billed_input_total is None or entry.billed_output_total is None:
        # human run（spec §5.4）無計費事實：billed totals NA → 不可計價
        raise LedgerError(
            f"billed totals 為 NA（human run），不可計價：turn={entry.turn}"
        )
    if entry.input_cached is None:
        input_cost = Decimal(entry.billed_input_total) * \
            snapshot.input_uncached_per_mtok / _MTOK
    else:
        if entry.input_cached > entry.billed_input_total:
            raise LedgerError(
                f"input_cached 超過 billed_input_total："
                f"{entry.input_cached} > {entry.billed_input_total}"
            )
        uncached_billed = entry.billed_input_total - entry.input_cached
        input_cost = (
            Decimal(entry.input_cached) * snapshot.input_cached_per_mtok / _MTOK
            + Decimal(uncached_billed) * snapshot.input_uncached_per_mtok / _MTOK
        )
    output_cost = Decimal(entry.billed_output_total) * \
        snapshot.output_per_mtok / _MTOK
    request_cost = (
        snapshot.per_request * entry.api_calls
        + snapshot.per_tool_call * entry.tool_calls
    )
    subtotal = input_cost + output_cost + request_cost
    return max(subtotal, snapshot.minimum_charge_per_call)


def compute_run_cost(
    entries: list[LedgerEntry], snapshot: PricingSnapshot
) -> RunCost:
    """ledger entries → RunCost；空 ledger（離線評分）合法回零成本。"""
    c_model = _ZERO
    c_reviewer = _ZERO
    for entry in entries:
        cost = entry_cost(entry, snapshot)
        if entry.role == "reviewer":
            c_reviewer += cost
        else:
            c_model += cost
    if entries and c_model + c_reviewer == _ZERO:
        raise LedgerError("C_run = 0 為計價設定錯誤（F18）：檢查 pricing snapshot")
    return RunCost(c_model=c_model, c_reviewer=c_reviewer)
