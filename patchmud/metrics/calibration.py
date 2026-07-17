"""校準 estimators（spec §10.4、F4）——pre-registration 與凍結語意。

pilot 跑完後，以本模組的 estimator 從全體封存 run 產出 `reference_cost`、
`difficulty_scale`、`τ` 與 EuTB 預算，寫回 deck card 並凍結進 registered 目錄。
一經凍結不得依正式結果調整；重複凍結或 deck 已含校準值一律 fail-closed，且
全有全無（不留半套輸出）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import median

import yaml

from patchmud.deck.loader import load_card

__all__ = [
    "EUTB_GRID_POINTS",
    "CalibrationError",
    "CalibrationResult",
    "CalibrationRun",
    "FrozenCalibrationError",
    "calibrate",
    "freeze_calibration",
]

#: EuTB 積分網格點數（§10.4 凍結常數）。
EUTB_GRID_POINTS = 256

#: C_ref / D estimator 的最小成功樣本數；不足則該 encounter 不校準。
_MIN_SUCCESSES = 3

#: EuTB 預算向上取整的 token 粒度。
_BUDGET_ROUNDING = 10_000

_D_DIVISOR = 40.0
_D_LOWER = 0.5
_D_UPPER = 4.0


class CalibrationError(Exception):
    """校準輸入不合法或無法產出（fail-closed）。"""


class FrozenCalibrationError(Exception):
    """registered 檔或 deck card 已凍結，拒絕覆寫。"""


@dataclass(frozen=True)
class CalibrationRun:
    """單一封存 run 供校準使用的最小投影；建構即 fail-closed 驗證。"""

    run_id: str
    encounter: str
    clear: int
    cost: Decimal
    final_diff_loc: int
    flood_index: float
    work_tokens: int

    def __post_init__(self) -> None:
        if self.clear not in (0, 1):
            raise CalibrationError(f"clear 必須是 0/1：{self.clear!r}")
        if isinstance(self.cost, bool) or not isinstance(self.cost, Decimal):
            raise CalibrationError(f"cost 必須是 Decimal（禁 float）：{self.cost!r}")
        if self.cost <= 0:
            raise CalibrationError(f"cost 必須 > 0（C_run=0 為設定錯誤）：{self.cost!r}")
        if isinstance(self.work_tokens, bool) or not isinstance(self.work_tokens, int):
            raise CalibrationError(f"work_tokens 必須是整數：{self.work_tokens!r}")
        if self.work_tokens <= 0:
            raise CalibrationError(
                f"work_tokens 必須 > 0（NA 記 None 不記 0）：{self.work_tokens!r}"
            )
        if self.final_diff_loc < 0:
            raise CalibrationError(f"final_diff_loc 不得為負：{self.final_diff_loc!r}")
        if self.flood_index < 0:
            raise CalibrationError(f"flood_index 不得為負：{self.flood_index!r}")


@dataclass(frozen=True)
class CalibrationResult:
    reference_costs: dict[str, Decimal | None]
    difficulty_scales: dict[str, float]
    uncalibrated: list[str]
    tau: float
    tau_uncalibrated: bool
    tau_sensitivity: tuple[float, float, float]
    eutb_budget: int
    grid: int = field(default=EUTB_GRID_POINTS)


def calibrate(runs: list[CalibrationRun]) -> CalibrationResult:
    """依 §10.4 estimator 表，從全體 run 產出校準結果。"""
    if not runs:
        raise CalibrationError("calibrate 需要至少一筆 run")
    seen: set[str] = set()
    for run in runs:
        if run.run_id in seen:
            raise CalibrationError(f"重複 run_id：{run.run_id}")
        seen.add(run.run_id)

    by_encounter: dict[str, list[CalibrationRun]] = {}
    for run in runs:
        by_encounter.setdefault(run.encounter, []).append(run)

    reference_costs: dict[str, Decimal | None] = {}
    difficulty_scales: dict[str, float] = {}
    uncalibrated: list[str] = []

    for enc, enc_runs in by_encounter.items():
        successes = [r for r in enc_runs if r.clear == 1]
        if len(successes) >= _MIN_SUCCESSES:
            reference_costs[enc] = _decimal_median([r.cost for r in successes])
            loc_median = median(r.final_diff_loc for r in successes)
            difficulty_scales[enc] = _clamp(
                loc_median / _D_DIVISOR, _D_LOWER, _D_UPPER
            )
        else:
            reference_costs[enc] = None
            difficulty_scales[enc] = 1.0
            uncalibrated.append(enc)

    tau, tau_uncalibrated = _tau(runs)
    tau_sensitivity = (tau * 0.5, tau, tau * 2.0)

    eutb_budget = _eutb_budget(runs)

    return CalibrationResult(
        reference_costs=reference_costs,
        difficulty_scales=difficulty_scales,
        uncalibrated=sorted(uncalibrated),
        tau=tau,
        tau_uncalibrated=tau_uncalibrated,
        tau_sensitivity=tau_sensitivity,
        eutb_budget=eutb_budget,
        grid=EUTB_GRID_POINTS,
    )


def freeze_calibration(
    result: CalibrationResult,
    registered_dir: Path,
    deck: dict[str, Path],
) -> tuple[Path, Path]:
    """凍結：寫 registered 檔（含 sha256）並把 C_ref/D 寫回校準過的 deck card。

    全有全無：任一前置條件失敗即不產生任何輸出。
    """
    registered_dir = Path(registered_dir)

    # 1. 每個 encounter 必須在 deck 中有對應目錄
    for enc in result.reference_costs:
        if enc not in deck or not Path(deck[enc], "card.yaml").is_file():
            raise CalibrationError(f"encounter 缺 deck 目錄或 card.yaml：{enc}")

    calibration_path = registered_dir / "calibration.yaml"
    budget_path = registered_dir / "eutb_budget.yaml"

    # 2. registered 檔已存在 → 拒絕（凍結後不得覆寫）
    for path in (calibration_path, budget_path):
        if path.exists():
            raise FrozenCalibrationError(f"registered 檔已存在，拒絕覆寫：{path}")

    # 3. 待校準 card 已含 reference_cost → 拒絕（deck 已凍結過）
    calibrated = [enc for enc, rc in result.reference_costs.items() if rc is not None]
    for enc in calibrated:
        card = load_card(deck[enc] / "card.yaml")
        if card.reference_cost is not None:
            raise FrozenCalibrationError(
                f"deck card 已含 reference_cost，拒絕重複凍結：{enc}"
            )

    # 4. 全部前置條件通過，開始寫入
    registered_dir.mkdir(parents=True, exist_ok=True)
    _write_with_hash(calibration_path, _calibration_payload(result))
    _write_with_hash(
        budget_path,
        {
            "schema_version": 1,
            "budget_tokens": result.eutb_budget,
            "grid_points": result.grid,
        },
    )

    # 5. 寫回校準過的 card（C_ref、D）
    for enc in calibrated:
        _update_card(
            deck[enc] / "card.yaml",
            reference_cost=result.reference_costs[enc],
            difficulty_scale=result.difficulty_scales[enc],
        )

    return calibration_path, budget_path


# ---------------------------------------------------------------------------
# estimator 內部
# ---------------------------------------------------------------------------


def _decimal_median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _tau(runs: list[CalibrationRun]) -> tuple[float, bool]:
    positive = [r.flood_index for r in runs if r.flood_index > 0]
    if not positive:
        return 1.0, True
    return float(median(positive)), False


def _eutb_budget(runs: list[CalibrationRun]) -> int:
    tokens = sorted(r.work_tokens for r in runs if r.clear == 1)
    if not tokens:
        raise CalibrationError("無 successful clear，EuTB 預算無法註冊（fail-closed）")
    # nearest-rank P95
    rank = -(-95 * len(tokens) // 100)  # ceil(0.95 * n)
    p95 = tokens[rank - 1]
    return -(-p95 // _BUDGET_ROUNDING) * _BUDGET_ROUNDING


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------


def _calibration_payload(result: CalibrationResult) -> dict:
    return {
        "reference_costs": {
            enc: ("NA" if rc is None else str(rc))
            for enc, rc in result.reference_costs.items()
        },
        "difficulty_scales": dict(result.difficulty_scales),
        "uncalibrated": list(result.uncalibrated),
        "tau": result.tau,
        "tau_uncalibrated": result.tau_uncalibrated,
        "tau_sensitivity": list(result.tau_sensitivity),
        "grid": result.grid,
    }


def _canonical_sha256(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _write_with_hash(path: Path, payload: dict) -> None:
    body = dict(payload)
    body["sha256"] = _canonical_sha256(payload)
    path.write_text(
        yaml.safe_dump(body, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )


def _update_card(card_path: Path, *, reference_cost: Decimal, difficulty_scale: float) -> None:
    data = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    data["reference_cost"] = str(reference_cost)
    data["difficulty_scale"] = float(difficulty_scale)
    card_path.write_text(
        yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
