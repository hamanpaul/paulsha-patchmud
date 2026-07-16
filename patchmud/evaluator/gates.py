"""Hard gates（spec §9.2）與 Clear 唯一布林公式（spec §5.2、F3）。

| Gate | 判定 | 效果 |
| --- | --- | --- |
| critical 未全過 | 任一 critical hidden probe 不綠 | `critical_pass=False`（連動 Clear=0、Economy=0、Utility ≤ 49） |
| 無法 import/compile/起測試 | 非 perf-only probe 出現 `error` | Power ≤ 15 |
| 公開 API 不相容 | card `compat_probes` 任一紅 | Power ≤ 50 |
| 存取 hidden 資產 | 稽核 log + 人工複核確認蓄意探測 | run invalid |

多 cap 疊加取低。critical gate 不是 Power cap：其效果由下游 metrics
（Clear/Economy/Utility）引用 `critical_pass` 落地。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from patchmud.deck.model import IssueCard
from patchmud.evaluator.power import PowerReport
from patchmud.sandbox.probes import ProbeOutcome

__all__ = [
    "COLLECTION_FAILURE_CAP",
    "COMPAT_BREAK_CAP",
    "CRITICAL_FAIL_UTILITY_CAP",
    "GateResult",
    "apply_power_cap",
    "compute_clear",
    "evaluate_gates",
]

#: 無法 import / compile / 起測試 → Power ≤ 15（spec §9.2）
COLLECTION_FAILURE_CAP = 15
#: 公開 API 不相容變更 → Power ≤ 50（spec §9.2）
COMPAT_BREAK_CAP = 50
#: critical 未全過 → Utility ≤ 49（供下游 metrics 引用，spec §9.2）
CRITICAL_FAIL_UTILITY_CAP = 49


@dataclass(frozen=True)
class GateResult:
    critical_pass: bool
    power_cap: int | None
    run_invalid: bool


def evaluate_gates(
    card: IssueCard,
    outcomes: Mapping[str, ProbeOutcome],
    *,
    hidden_access_detected: bool = False,
) -> GateResult:
    """由終局 probe outcomes 判定 hard gates（fail-closed：缺漏視同不綠）。

    「evaluator collection 階段失敗」的 deterministic 判準：任何非 perf-only
    probe（critical、functional、robustness、compat）outcome 為 `error`。
    只出現在 runtime_efficiency 的 probe error 只歸零 perf 分項，不觸發此 gate。
    """
    critical_pass = all(
        _is_green(outcomes.get(cr.hidden_probe)) for cr in card.critical_requirements
    )

    non_perf = _non_perf_probe_ids(card)
    collection_failed = any(
        outcome.status == "error"
        for probe_id, outcome in outcomes.items()
        if probe_id in non_perf
    )
    compat_broken = any(
        not _is_green(outcomes.get(cp.probe)) for cp in card.compat_probes
    )

    caps = []
    if collection_failed:
        caps.append(COLLECTION_FAILURE_CAP)
    if compat_broken:
        caps.append(COMPAT_BREAK_CAP)

    return GateResult(
        critical_pass=critical_pass,
        power_cap=min(caps) if caps else None,
        run_invalid=hidden_access_detected,
    )


def apply_power_cap(report: PowerReport, gates: GateResult) -> PowerReport:
    """hard-gate cap 與 rubric 總分疊加取低；分項封存值不變。"""
    if gates.power_cap is None or report.total <= gates.power_cap:
        return report
    return replace(report, total=float(gates.power_cap))


def compute_clear(
    *, critical_pass: bool, main_public_green: bool, protocol_failed: bool
) -> int:
    """`Clear` 的唯一布林公式（spec §5.2、F3）——全系統僅此一種算法。

    Clear = 1 ⟺ 終局時 (a) 全部 critical hidden probes 綠
              ∧ (b) 全部 MAIN public probes 綠
              ∧ (c) run 未以 failed:protocol 終局
    """
    return 1 if (critical_pass and main_public_green and not protocol_failed) else 0


def _is_green(outcome: ProbeOutcome | None) -> bool:
    return outcome is not None and outcome.status == "passed"


def _non_perf_probe_ids(card: IssueCard) -> frozenset[str]:
    rubric = card.power_rubric
    ids: set[str] = set()
    ids.update(cr.hidden_probe for cr in card.critical_requirements)
    ids.update(group.probe for group in rubric.functional.groups)
    ids.update(rubric.robustness.probes)
    ids.update(rubric.compatibility.probes)
    ids.update(cp.probe for cp in card.compat_probes)
    return frozenset(ids)
