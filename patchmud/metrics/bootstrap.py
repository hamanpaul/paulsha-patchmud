"""Encounter-level bootstrap 信賴區間（spec §10.3、報告 §5.6.7）。

- 所有效率指標（TokensPerClear／QATY／EuTB／MTY／FTR）發布時另附
  encounter-level bootstrap CI：B=10,000、seed 記錄在輸出（可重現）。
- percentile 法：以 ``random.Random(seed)`` 對 values 重抽 B 次樣本均值，
  取 2.5／97.5 百分位——deterministic、零外部依賴。
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass
from typing import Sequence

__all__ = [
    "DEFAULT_BOOTSTRAP_B",
    "BootstrapCI",
    "BootstrapError",
    "bootstrap_ci",
]

#: 報告 §10.3：B = 10,000。
DEFAULT_BOOTSTRAP_B = 10_000


class BootstrapError(ValueError):
    """bootstrap 契約違反：空樣本、B 非正、值非數字。"""


@dataclass(frozen=True)
class BootstrapCI:
    """95% percentile bootstrap CI（seed 記錄供重現）。"""

    point: float
    low: float
    high: float
    b: int
    seed: int


def bootstrap_ci(
    values: Sequence[float],
    *,
    b: int = DEFAULT_BOOTSTRAP_B,
    seed: int,
) -> BootstrapCI:
    """encounter-level 值序列 → 樣本均值的 95% percentile bootstrap CI。

    固定 ``seed`` 完全重現；``b`` 預設 10,000（§10.3），seed 與 b 記錄在
    輸出。
    """
    if not values:
        raise BootstrapError("bootstrap_ci：values 不可為空")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BootstrapError(f"bootstrap_ci：值必須是數字：{value!r}")
    if isinstance(b, bool) or not isinstance(b, int) or b < 2:
        raise BootstrapError(f"bootstrap_ci：B 必須是 ≥ 2 的整數：{b!r}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise BootstrapError(f"bootstrap_ci：seed 必須是整數：{seed!r}")

    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        statistics.fmean(rng.choices(values, k=n)) for _ in range(b)
    )
    # quantiles(n=40, inclusive)：cut points 為 2.5%, 5%, …, 97.5%
    cuts = statistics.quantiles(means, n=40, method="inclusive")
    return BootstrapCI(
        point=statistics.fmean(values),
        low=cuts[0],
        high=cuts[-1],
        b=b,
        seed=seed,
    )
