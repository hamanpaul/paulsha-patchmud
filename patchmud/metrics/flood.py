"""Flood 計量：dual area、start-of-turn FTR、Control（spec §8.2、報告 §7.3–7.4）。

- 只讀 store 落盤 events（queue snapshots + per-turn ledger），不執行任何
  candidate code、不寫回 run 目錄（plan invariant 3、4）。
- **Dual area（F13）**：``FloodArea_total = Σ_t B_t``（報告 §7.3 原式，
  Δt = 1 turn）；``FloodArea_excess = Σ_t (B_t − M_t)`` 只計自生債務
  （REOPENED／REGRESSION／SCOPE／CHURN／DUPLICATE）的存續面積。
- **Control 使用 excess**（spec §8.2 對報告 §7.3 的明文偏離）：Flood Index
  依報告 §7.4 公式以 ``FloodArea_excess`` 代入、review-debt 項權重 0
  （§6.2）、``D_issue = card.difficulty_scale``；``Control = 100·exp(−F/τ)``，
  τ 未校準（None）時使用 1.0 並以 ``tau_uncalibrated`` 標記——校準值只能
  來自 §10.4 estimator，不得假造。
- **FTR（F14）**：token 歸屬以 **turn 開始時** 的 backlog 狀態判定：
  ``FTR = Σ_t ΔT_t·1(B_{t−1} > B_0) / Σ_t ΔT_t``；ΔT_t 取該 turn ledger
  的 billed totals（author＋同 turn reviewer；§10.1 billed 永遠可得，無
  NA 傳染問題）。另平行發布 ``flood_create_tokens``（B 上升的 turn）與
  ``flood_repair_tokens``（B 自高處——高於 B_0——下降的 turn），避免單一
  ratio 掩蓋 create/repair 差異。
- 係數檔 ``flood_coeffs.yaml`` 版本化；schema 不符、events 欄位缺漏一律
  fail-closed ``FloodError``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from patchmud.deck.model import IssueCard

__all__ = [
    "COEFFS_SCHEMA_VERSION",
    "DEFAULT_COEFFS_PATH",
    "FloodCoeffs",
    "FloodError",
    "FloodMetrics",
    "flood_metrics",
    "load_flood_coeffs",
]

COEFFS_SCHEMA_VERSION = 1

#: 版本化係數檔（deck 級常數；報告 §7.4 初始值、review-debt 權重 0）。
DEFAULT_COEFFS_PATH = Path(__file__).with_name("flood_coeffs.yaml")

#: Flood Index 權重鍵（報告 §7.4；S_scope 為直接加項、權重 1 由公式 pin）。
_WEIGHT_KEYS = ("regression", "reopen", "review_debt", "duplicate", "reverted_loc")

#: flood 計量消耗的 queue snapshot counters 鍵（缺漏 fail-closed）。
_COUNTER_KEYS = ("regression", "reopen", "duplicate", "s_scope_loc", "reverted_loc")


class FloodError(ValueError):
    """flood 計量契約違反：係數檔 schema 不符、events 欄位缺漏或值非法。"""


@dataclass(frozen=True)
class FloodCoeffs:
    """報告 §7.4 Flood Index 權重（版本化；review_debt 恆 0，§6.2）。"""

    schema_version: int
    regression: float
    reopen: float
    review_debt: float
    duplicate: float
    reverted_loc: float


@dataclass(frozen=True)
class FloodMetrics:
    """spec §8.2 的 flood 計量輸出（單場 run）。"""

    #: FloodArea_total = Σ_t B_t（報告 §7.3 原式；照報告發布）。
    area_total: int
    #: FloodArea_excess = Σ_t (B_t − M_t)：自生債務存續面積（F13）。
    area_excess: int
    #: 報告 §7.4 Flood Index F（excess 代入、review-debt 權重 0）。
    flood_index: float
    #: Control = 100·exp(−F/τ)。
    control: float
    #: FTR = Σ ΔT_t·1(B_{t−1} > B_0) / Σ ΔT_t（F14；無 token → 0.0）。
    ftr: float
    #: B 上升的 turn 之 billed tokens。
    flood_create_tokens: int
    #: B 自高處（B_{t−1} > B_0）下降的 turn 之 billed tokens。
    flood_repair_tokens: int
    #: 實際使用的 τ（未校準時 1.0）。
    tau: float
    #: τ 未經 §10.4 estimator 校準（使用預設 1.0）→ True。
    tau_uncalibrated: bool


# ---------------------------------------------------------------------------
# 係數檔載入（fail-closed）
# ---------------------------------------------------------------------------


def _require_weight(weights: Mapping, key: str) -> float:
    value = weights.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FloodError(f"flood 係數缺漏或值非數字：weights.{key}={value!r}")
    return float(value)


def load_flood_coeffs(path: Path | None = None) -> FloodCoeffs:
    """載入版本化 Flood Index 係數檔；schema 不符 raise ``FloodError``。"""
    coeffs_path = DEFAULT_COEFFS_PATH if path is None else Path(path)
    try:
        data = yaml.safe_load(coeffs_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise FloodError(f"flood 係數檔無法讀取：{coeffs_path}（{exc}）") from exc
    if not isinstance(data, dict):
        raise FloodError("flood 係數檔內容必須是 mapping")
    if data.get("schema_version") != COEFFS_SCHEMA_VERSION:
        raise FloodError(
            f"flood 係數檔 schema_version 不符：{data.get('schema_version')!r}"
        )
    weights = data.get("weights")
    if not isinstance(weights, dict):
        raise FloodError("flood 係數檔缺 weights mapping")
    values = {key: _require_weight(weights, key) for key in _WEIGHT_KEYS}
    if values["review_debt"] != 0.0:
        # §6.2：reviewer findings 不進 queue / Control；非 0 即契約違反
        raise FloodError(
            f"review_debt 權重必須為 0（§6.2）：{values['review_debt']!r}"
        )
    return FloodCoeffs(schema_version=COEFFS_SCHEMA_VERSION, **values)


# ---------------------------------------------------------------------------
# events 解析（fail-closed）
# ---------------------------------------------------------------------------


def _require_count(container: Mapping, key: str, *, context: str) -> int:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FloodError(f"{context} 缺欄位或值非非負整數：{key}={value!r}")
    return value


def _snapshot_of(event: Mapping, *, context: str) -> Mapping:
    queue = event.get("queue")
    if not isinstance(queue, Mapping):
        raise FloodError(f"{context} 缺 queue snapshot")
    return queue


def _b_m(queue: Mapping, *, context: str) -> tuple[int, int]:
    b_t = _require_count(queue, "b_t", context=context)
    m_t = _require_count(queue, "m_t", context=context)
    if m_t > b_t:
        raise FloodError(f"{context} m_t > b_t：{m_t} > {b_t}")
    return b_t, m_t


def _turn_tokens(event: Mapping, *, context: str) -> int:
    """該 turn 的 ΔT_t：billed totals（author＋同 turn reviewer，§10.1）。"""
    ledger = event.get("ledger")
    if not isinstance(ledger, Mapping):
        raise FloodError(f"{context} 缺 ledger")
    author = ledger.get("author")
    if not isinstance(author, Mapping):
        raise FloodError(f"{context} 缺 ledger.author")
    total = _require_count(
        author, "billed_input_total", context=f"{context} ledger.author"
    ) + _require_count(
        author, "billed_output_total", context=f"{context} ledger.author"
    )
    reviewer = ledger.get("reviewer")
    if reviewer is not None:
        if not isinstance(reviewer, Mapping):
            raise FloodError(f"{context} ledger.reviewer 非 mapping")
        total += _require_count(
            reviewer, "billed_input_total", context=f"{context} ledger.reviewer"
        ) + _require_count(
            reviewer, "billed_output_total", context=f"{context} ledger.reviewer"
        )
    return total


# ---------------------------------------------------------------------------
# flood_metrics
# ---------------------------------------------------------------------------


def flood_metrics(
    events: Sequence[Mapping],
    card: IssueCard,
    coeffs: FloodCoeffs,
    *,
    tau: float | None = None,
) -> FloodMetrics:
    """自 store events 計算單場 run 的 flood 計量（spec §8.2）。

    ``tau=None`` 表示 §10.4 estimator 尚未凍結 τ：使用 1.0 並標記
    ``tau_uncalibrated``（不得以假校準值產生看似正式的 Control）。
    """
    d_issue = card.difficulty_scale
    if not isinstance(d_issue, (int, float)) or d_issue <= 0:
        raise FloodError(f"difficulty_scale 必須為正數：{d_issue!r}")
    if tau is not None and (isinstance(tau, bool) or tau <= 0):
        raise FloodError(f"τ 必須為正數：{tau!r}")

    baseline_queue: Mapping | None = None
    last_queue: Mapping | None = None
    turn_events: list[Mapping] = []
    for event in events:
        event_type = event.get("type")
        if event_type == "baseline":
            if baseline_queue is not None:
                raise FloodError("events 含多個 baseline event")
            baseline_queue = _snapshot_of(event, context="baseline event")
        elif event_type == "turn":
            turn_events.append(event)
        if isinstance(event, Mapping) and isinstance(event.get("queue"), Mapping):
            last_queue = event["queue"]
    if baseline_queue is None:
        raise FloodError("events 缺 baseline event（turn-0 queue snapshot）")
    assert last_queue is not None

    b_0, _ = _b_m(baseline_queue, context="baseline event")

    area_total = 0
    area_excess = 0
    total_tokens = 0
    flood_tokens = 0
    create_tokens = 0
    repair_tokens = 0
    b_prev = b_0
    for event in turn_events:
        context = f"turn event（turn={event.get('turn')!r}）"
        b_t, m_t = _b_m(_snapshot_of(event, context=context), context=context)
        delta_t = _turn_tokens(event, context=context)

        area_total += b_t
        area_excess += b_t - m_t
        total_tokens += delta_t
        if b_prev > b_0:  # F14：以 turn 開始時的 backlog 狀態歸屬
            flood_tokens += delta_t
        if b_t > b_prev:
            create_tokens += delta_t
        elif b_prev > b_0 and b_t < b_prev:
            repair_tokens += delta_t
        b_prev = b_t

    counters = last_queue.get("counters")
    if not isinstance(counters, Mapping):
        raise FloodError("最末 queue snapshot 缺 counters")
    counts = {
        key: _require_count(counters, key, context="queue counters")
        for key in _COUNTER_KEYS
    }
    n_review_debt = 0  # §6.2：reviewer findings 不進 queue；權重亦為 0

    flood_index = (
        area_excess
        + coeffs.regression * counts["regression"]
        + coeffs.reopen * counts["reopen"]
        + coeffs.review_debt * n_review_debt
        + coeffs.duplicate * counts["duplicate"]
        + coeffs.reverted_loc * counts["reverted_loc"]
        + counts["s_scope_loc"]
    ) / float(d_issue)

    effective_tau = 1.0 if tau is None else float(tau)
    control = 100.0 * math.exp(-flood_index / effective_tau)
    ftr = flood_tokens / total_tokens if total_tokens > 0 else 0.0

    return FloodMetrics(
        area_total=area_total,
        area_excess=area_excess,
        flood_index=flood_index,
        control=control,
        ftr=ftr,
        flood_create_tokens=create_tokens,
        flood_repair_tokens=repair_tokens,
        tau=effective_tau,
        tau_uncalibrated=tau is None,
    )
