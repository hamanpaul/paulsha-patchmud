"""Token-efficiency 稽核指標（spec §10.3、報告 §5.6.2–§5.6.5、F17、§19.9）。

- ``TokensPerClear = Σ T^work / Σ Clear``（報告 §5.6.2）：失敗 run token
  留在分子；零 clear → 無限大。
- ``QATY = 10^6·Σ Clear·(Power/100) / Σ T^work``（報告 §5.6.3）。
- ``EuTB(B) = (1/B)∫₀^B R(b) db``（報告 §5.6.4）：R(b) = 通關且 T^work ≤ b
  的 run 比例；預算上限 B 與積分網格必須來自 pre-registered
  ``eutb_budget.yaml``（§10.4），registered 檔缺失／不合法 →
  ``NotRegisteredError``（fail-closed，報告 §19.9）；積分以網格右端點在
  整數域精確判定（``tok·G ≤ B·k``），無浮點邊界不確定性。
- ``MTY_t = (Q_t − Q_{t−1}) / (ΔT_t/1000)``（報告 §5.6.5）；ΔT NA → 該
  元素 None（NA 傳染）。
- **NA 傳染＋observable 雙欄（§10.1）**：run 集含 T^work NA 的 entry →
  work 欄輸出 ``None``、common-observable 欄（input + output_visible）照算。
- **零 token fail-closed（§10.1）**：真實 model run 的 billed/observable
  tokens 永遠可得且為正；零 token 樣本在 ``RunSample`` 構造層即拒絕
  （``EconomyError``），tokens_per_clear／qaty／eutb 三者共用此單一防線
  ——零 token run 不可能以 0.0 TokensPerClear／1.0 EuTB 奪榜首。
- **Disclosure cohort（F17）**：所有效率排名輸出帶 ``disclosure_cohort``；
  ``rank_efficiency`` 只在同 cohort 內排名，跨 cohort 請求一律 raise——
  否則「少揭露 reasoning」的模型在固定 budget 指標上憑空得利。
- human run 不進 ranked 聚合（``HumanRunExcluded``，同 economy）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from patchmud.metrics.economy import (
    EconomyError,
    HumanRunExcluded,
    RunSample,
    require_model_runs,
)

__all__ = [
    "COHORT_FULL",
    "COHORT_OBSERVABLE",
    "EUTB_BUDGET_SCHEMA_VERSION",
    "CohortMismatchError",
    "EfficiencyError",
    "EfficiencyResult",
    "HumanRunExcluded",
    "NotRegisteredError",
    "RegisteredBudget",
    "eutb",
    "load_eutb_budget",
    "mty",
    "qaty",
    "rank_efficiency",
    "tokens_per_clear",
]

EUTB_BUDGET_SCHEMA_VERSION = 1

#: 全部互斥 token 欄位揭露（T^work 可得）。
COHORT_FULL = "full"
#: 至少一 run 的 T^work 為 NA（僅 observable 欄可比）。
COHORT_OBSERVABLE = "observable"

#: 排名方向由指標 pin（caller 不得自選方向）。
_HIGHER_IS_BETTER = {
    "tokens_per_clear": False,
    "qaty": True,
    "eutb": True,
}


class EfficiencyError(ValueError):
    """效率指標契約違反：欄位缺漏、分母為零、未知指標等。"""


class NotRegisteredError(EfficiencyError):
    """EuTB 預算未 pre-registered：檔案缺失或內容不合法（報告 §19.9）。"""


class CohortMismatchError(EfficiencyError):
    """跨 disclosure cohort 排名請求（F17）。"""


@dataclass(frozen=True)
class EfficiencyResult:
    """效率指標輸出（雙欄＋disclosure cohort，§10.1／F17）。

    - ``value``：以 T^work 計算；集合含 NA → None（傳染）。
    - ``observable``：以 common-observable tokens 計算（永遠可得）。
    """

    metric: str
    value: float | None
    observable: float
    disclosure_cohort: str


@dataclass(frozen=True)
class RegisteredBudget:
    """pre-registered EuTB 預算（§10.4：B 與積分網格凍結後不得調整）。"""

    budget_tokens: int
    grid_points: int

    def __post_init__(self) -> None:
        for name in ("budget_tokens", "grid_points"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise NotRegisteredError(f"{name} 必須是正整數：{value!r}")


def load_eutb_budget(path: Path) -> RegisteredBudget:
    """載入 pre-registered ``eutb_budget.yaml``；缺失／不合法 →
    ``NotRegisteredError``（fail-closed，報告 §19.9）。"""
    budget_path = Path(path)
    try:
        data = yaml.safe_load(budget_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise NotRegisteredError(
            f"EuTB registered 預算檔無法讀取：{budget_path}（{exc}）"
        ) from exc
    if not isinstance(data, dict):
        raise NotRegisteredError("EuTB registered 預算檔內容必須是 mapping")
    if data.get("schema_version") != EUTB_BUDGET_SCHEMA_VERSION:
        raise NotRegisteredError(
            f"EuTB registered 預算檔 schema_version 不符："
            f"{data.get('schema_version')!r}"
        )
    return RegisteredBudget(
        budget_tokens=data.get("budget_tokens"),
        grid_points=data.get("grid_points"),
    )


# ---------------------------------------------------------------------------
# 聚合 helpers（NA 傳染＋observable 雙欄）
# ---------------------------------------------------------------------------


def _cohort(runs: Sequence[RunSample]) -> str:
    if any(run.work_tokens is None for run in runs):
        return COHORT_OBSERVABLE
    return COHORT_FULL


def _work_total(runs: Sequence[RunSample]) -> int | None:
    """Σ T^work；任一 NA → None（NA 傳染，§10.1）。"""
    total = 0
    for run in runs:
        if run.work_tokens is None:
            return None
        total += run.work_tokens
    return total


def _observable_total(runs: Sequence[RunSample]) -> int:
    return sum(run.observable_tokens for run in runs)


# ---------------------------------------------------------------------------
# TokensPerClear（報告 §5.6.2）
# ---------------------------------------------------------------------------


def tokens_per_clear(runs: Sequence[RunSample]) -> EfficiencyResult:
    """``TokensPerClear = Σ T^work / Σ Clear``；零 clear → 無限大。"""
    _require_runs(runs, context="tokens_per_clear")
    clears = sum(run.clear for run in runs)
    work = _work_total(runs)
    if clears == 0:
        value = None if work is None else math.inf
        observable = math.inf
    else:
        value = None if work is None else work / clears
        observable = _observable_total(runs) / clears
    return EfficiencyResult(
        metric="tokens_per_clear",
        value=value,
        observable=observable,
        disclosure_cohort=_cohort(runs),
    )


# ---------------------------------------------------------------------------
# QATY（報告 §5.6.3）
# ---------------------------------------------------------------------------


def qaty(runs: Sequence[RunSample]) -> EfficiencyResult:
    """``QATY = 10^6·Σ Clear·(Power/100) / Σ T^work``；值愈高愈好。

    分母不可能為 0：零 token 樣本在 ``RunSample`` 構造層已 fail-closed
    （§10.1），非空 model run 集的 Σ tokens 恆為正。
    """
    _require_runs(runs, context="qaty")
    numerator = 1_000_000 * sum(run.clear * (run.power / 100.0) for run in runs)
    work = _work_total(runs)
    observable_total = _observable_total(runs)
    return EfficiencyResult(
        metric="qaty",
        value=None if work is None else numerator / work,
        observable=numerator / observable_total,
        disclosure_cohort=_cohort(runs),
    )


# ---------------------------------------------------------------------------
# EuTB（報告 §5.6.4；registered fail-closed，§19.9）
# ---------------------------------------------------------------------------


def eutb(
    runs: Sequence[RunSample],
    registered_budget: RegisteredBudget | Path | str,
) -> EfficiencyResult:
    """``EuTB(B) = (1/B)∫₀^B R(b) db`` 於 pre-registered 網格上積分。

    ``registered_budget`` 為路徑時即刻載入 registered 檔；缺失 →
    ``NotRegisteredError``。積分 = 網格右端點 b_k = B·k/G（k=1..G）上
    R(b_k) 的平均；``tok ≤ B·k/G`` 以整數域 ``tok·G ≤ B·k`` 精確判定。
    """
    if isinstance(registered_budget, (Path, str)):
        registered_budget = load_eutb_budget(Path(registered_budget))
    if not isinstance(registered_budget, RegisteredBudget):
        raise NotRegisteredError(
            f"registered_budget 型別非法：{registered_budget!r}"
        )
    _require_runs(runs, context="eutb")

    def _grid_average(tokens_of) -> float:
        budget = registered_budget.budget_tokens
        grid = registered_budget.grid_points
        hits = 0
        for run in runs:
            if run.clear != 1:
                continue
            tokens = tokens_of(run)
            # tok ≤ B·k/G ⟺ tok·G ≤ B·k → 首個計入的 k = ceil(tok·G/B)
            first_k = -(-tokens * grid // budget)  # ceil division
            if first_k <= grid:
                hits += grid - max(first_k, 1) + 1
        return hits / (grid * len(runs))

    # NA 傳染看整個 run 集（含失敗 run；§10.1），不只 clear runs——
    # 保持「value 為 NA ⟺ cohort 為 observable」的雙欄不變量。
    if _work_total(runs) is None:
        value: float | None = None
    else:
        value = _grid_average(lambda run: run.work_tokens)
    observable = _grid_average(lambda run: run.observable_tokens)
    return EfficiencyResult(
        metric="eutb",
        value=value,
        observable=observable,
        disclosure_cohort=_cohort(runs),
    )


# ---------------------------------------------------------------------------
# MTY（報告 §5.6.5）
# ---------------------------------------------------------------------------


def mty(
    checkpoint_scores: Sequence[tuple[float, int | None]],
) -> tuple[float | None, ...]:
    """``MTY_t = (Q_t − Q_{t−1}) / (ΔT_t/1000)``（報告 §5.6.5）。

    輸入 ``[(Q_0, 0), (Q_1, ΔT_1), …]``：首元素為 turn-0 baseline（ΔT
    必須為 0）；t ≥ 1 的 ΔT 為 NA（None）→ 該元素 None（NA 傳染）。
    hidden checkpoint score 只在賽後離線重播使用，不回饋同場 run。
    """
    if not checkpoint_scores:
        raise EfficiencyError("mty：checkpoint_scores 不可為空")
    for index, (score, _delta) in enumerate(checkpoint_scores):
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise EfficiencyError(f"mty：Q_{index} 必須是數字：{score!r}")
    baseline_delta = checkpoint_scores[0][1]
    if isinstance(baseline_delta, bool) or baseline_delta != 0:
        raise EfficiencyError(
            f"mty：baseline（turn 0）ΔT 必須為 0：{baseline_delta!r}"
        )
    series: list[float | None] = []
    for turn in range(1, len(checkpoint_scores)):
        q_now, delta = checkpoint_scores[turn]
        q_prev = checkpoint_scores[turn - 1][0]
        if delta is None:
            series.append(None)
            continue
        if isinstance(delta, bool) or not isinstance(delta, int) or delta <= 0:
            raise EfficiencyError(
                f"mty：ΔT_{turn} 必須是正整數或 None（NA）：{delta!r}"
            )
        series.append((q_now - q_prev) / (delta / 1000.0))
    return tuple(series)


# ---------------------------------------------------------------------------
# Disclosure cohort 排名（F17）
# ---------------------------------------------------------------------------


def rank_efficiency(results: Mapping[str, EfficiencyResult]) -> tuple[str, ...]:
    """同 disclosure cohort 內排名（最佳在前）；跨 cohort →
    ``CohortMismatchError``（F17）。排名方向由指標 pin。"""
    if not results:
        raise EfficiencyError("rank_efficiency：results 不可為空")
    metrics = {result.metric for result in results.values()}
    if len(metrics) > 1:
        raise EfficiencyError(f"rank_efficiency：混用不同指標：{sorted(metrics)}")
    metric = next(iter(metrics))
    if metric not in _HIGHER_IS_BETTER:
        raise EfficiencyError(f"rank_efficiency：未知指標：{metric!r}")
    cohorts = {result.disclosure_cohort for result in results.values()}
    if len(cohorts) > 1:
        raise CohortMismatchError(
            f"跨 disclosure cohort 排名（F17）：{sorted(cohorts)}；"
            "跨 cohort 只能發布 common-observable 描述性欄位（non-ranking）"
        )
    cohort = next(iter(cohorts))

    def _key(name: str) -> float:
        result = results[name]
        if cohort == COHORT_FULL:
            if result.value is None:
                raise EfficiencyError(
                    f"rank_efficiency：full cohort 內 value 為 NA：{name}"
                )
            return result.value
        return result.observable

    higher = _HIGHER_IS_BETTER[metric]
    return tuple(
        sorted(results, key=lambda name: (-_key(name) if higher else _key(name), name))
    )


# ---------------------------------------------------------------------------
# 內部
# ---------------------------------------------------------------------------


def _require_runs(runs: Sequence[RunSample], *, context: str) -> None:
    try:
        require_model_runs(runs, context=context)
    except HumanRunExcluded:
        raise
    except EconomyError as exc:
        raise EfficiencyError(str(exc)) from exc
