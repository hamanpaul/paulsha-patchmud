"""兩級 replay：L1 位元一致重算、L2 pinned 重執行比對（spec §12.2、F2）。

- **L1 重算**（`replay_l1`）：只讀 run 目錄封存（`run.yaml`、`events.jsonl`、
  `ledger.jsonl`、`result.yaml`）與 deck card，重算 queue 軌跡一致性、flood
  計量、Power 分項與 Clear 等全部可導出欄位，**不執行任何 probe**；
  runtime_efficiency 一律引用封存 `perf_judgments`（量測當下定案，F2），
  位元一致宣稱因此不被 wall-clock 非確定性破壞。重算值與封存 result.yaml
  逐欄比對，任何不一致落入 `ReplayReport.diffs`。
- **L2 重執行**（`replay_l2`）：經注入的 reexecutor 於 pinned 環境重新執行
  全部 probes（真佈線見 cli `patchmud replay --l2`；unit tests 注入 fake）。
  functional / compat / robustness 等非 perf probe 結果**必須相等**；
  perf-only probe（只出現在 runtime_efficiency rubric 的 probe）差異落
  `perf_deviations` 報告、不算 fail（timing 本質非確定）。之後走 L1。
- 重算路徑絕不寫回 run 目錄（plan invariant 4）；一切讀取 fail-closed：
  schema 不符、欄位缺漏、封存內部矛盾一律 raise `ReplayError`。
- `gates.run_invalid`（hidden 資產存取稽核）是稽核層輸入、非 probe 導出值，
  L1 照封存值透傳，不參與重算比對。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from patchmud.deck.loader import load_card
from patchmud.deck.model import IssueCard
from patchmud.evaluator.gates import apply_power_cap, compute_clear, evaluate_gates
from patchmud.evaluator.power import (
    PerfJudgment,
    PowerReport,
    maintainability_from_observations,
    runtime_efficiency_from_judgments,
    score_compatibility,
    score_functional,
    score_robustness,
)
from patchmud.ledger.tokens import LedgerEntry, LedgerError, aggregate_work_tokens
from patchmud.metrics.flood import (
    FloodError,
    FloodMetrics,
    flood_metrics,
    load_flood_coeffs,
)
from patchmud.sandbox.probes import ProbeOutcome, smoke_probe_id
from patchmud.store.run_store import RunStore
from patchmud.store.schemas import RESULT_SCHEMA_VERSION

__all__ = [
    "PerfDeviation",
    "ProbeReexecutor",
    "ReexecutedProbes",
    "ReplayDiff",
    "ReplayError",
    "ReplayReport",
    "load_ledger",
    "replay_l1",
    "replay_l2",
]

#: 終局協定關鍵字（spec §5.2 artifact 格式）；不 import engine 以維持分層。
_END_PROTOCOL = "failed:protocol"
#: 互斥資源欄位不可得記 NA、不記 0（§10.1）；result.yaml 序列化值。
_NA = "NA"

_RESULT_FILE = "result.yaml"
_RUN_FILE = "run.yaml"
_LEDGER_FILE = "ledger.jsonl"
_LEDGER_SCHEMA_VERSION = 1

_PROBE_STATUSES = ("passed", "failed", "error")
_OUTCOME_KEYS = (
    "status",
    "cases_total",
    "cases_passed",
    "failure_fingerprints",
    "wall_ms",
    "cpu_ms",
)
_JUDGMENT_KEYS = ("probe_id", "wall_ms", "budget_ms", "passed", "within_budget")


class ReplayError(Exception):
    """replay 契約違反：封存缺漏、schema 不符、重算輸入非法（fail-closed）。"""


@dataclass(frozen=True)
class ReplayDiff:
    """重算值與封存值的單欄不一致。"""

    field: str
    archived: object
    recomputed: object


@dataclass(frozen=True)
class PerfDeviation:
    """L2 perf-only probe 的重執行偏差：落 report、不算 fail（spec §12.2）。"""

    probe_id: str
    section: str
    archived_status: str
    reexecuted_status: str
    archived_wall_ms: int
    reexecuted_wall_ms: int


@dataclass(frozen=True)
class ReplayReport:
    """一次 replay 的比對結果；`identical` ⟺ `diffs` 為空。"""

    level: str  # "L1" | "L2"
    identical: bool
    diffs: tuple[ReplayDiff, ...]
    perf_deviations: tuple[PerfDeviation, ...] = ()
    #: L1 重算的 flood 計量（run 模式；score-diff 無 events 軌跡 → None）。
    flood: FloodMetrics | None = None


@dataclass(frozen=True)
class ReexecutedProbes:
    """L2 pinned 重執行的全部 probe outcomes（public／evaluator 兩節）。"""

    public: Mapping[str, ProbeOutcome]
    evaluator: Mapping[str, ProbeOutcome]


#: L2 重執行 seam：真佈線在 cli（IsolationRunner）；unit tests 注入 fake。
ProbeReexecutor = Callable[[Path], ReexecutedProbes]


# ---------------------------------------------------------------------------
# L1 重算
# ---------------------------------------------------------------------------


def replay_l1(run_dir: Path) -> ReplayReport:
    """位元一致 L1 重算：不執行任何 probe，重算值逐欄比對封存 result.yaml。"""
    run_dir = Path(run_dir)
    store = RunStore.open(run_dir)  # fail-closed：run.yaml schema、event seq
    events = store.load_events()
    card = _load_card_for(_load_run_record(run_dir))
    result = _load_result(run_dir)

    mode = result.get("mode")
    if mode not in ("run", "score-diff"):
        raise ReplayError(f"result.yaml mode 不支援 replay：{mode!r}")

    evaluator_outcomes = _outcomes_section(result, "evaluator")
    public_outcomes = _outcomes_section(result, "public")

    diffs: list[ReplayDiff] = []

    # gates：critical_pass / power_cap 由封存 evaluator outcomes 重算；
    # run_invalid 是稽核輸入（hidden 資產存取），照封存值透傳。
    archived_gates = _require_mapping(result, "gates", context="result.yaml")
    gates = evaluate_gates(
        card,
        evaluator_outcomes,
        hidden_access_detected=bool(archived_gates.get("run_invalid", False)),
    )
    _compare(diffs, "gates.critical_pass", archived_gates.get("critical_pass"), gates.critical_pass)
    _compare(diffs, "gates.power_cap", archived_gates.get("power_cap"), gates.power_cap)

    # Power：probe 分項自封存 outcomes 重算；maintainability 自封存觀測值、
    # runtime_efficiency 自封存 PerfJudgment（F2）重算。
    archived_power = _require_mapping(result, "power", context="result.yaml")
    power = _recompute_power(card, evaluator_outcomes, archived_power, gates)
    for field in (
        "functional",
        "robustness",
        "compatibility",
        "maintainability",
        "runtime_efficiency",
        "total",
    ):
        _compare(diffs, f"power.{field}", archived_power.get(field), getattr(power, field))
    archived_breakdown = _require_mapping(
        archived_power, "maintainability_breakdown", context="result.yaml power"
    )
    breakdown = power.maintainability_breakdown
    for field in ("diff_size", "scope", "lint", "total"):
        _compare(
            diffs,
            f"power.maintainability_breakdown.{field}",
            archived_breakdown.get(field),
            getattr(breakdown, field),
        )

    main_public_green = all(
        _is_green(public_outcomes, req.probe) for req in card.public_requirements
    )
    _compare(diffs, "main_public_green", result.get("main_public_green"), main_public_green)

    flood: FloodMetrics | None = None
    if mode == "run":
        final_event = _final_event(events)
        end_reason = final_event.get("end_reason")
        protocol_failed = end_reason == _END_PROTOCOL
        _compare(diffs, "end_reason", result.get("end_reason"), end_reason)
        _compare(diffs, "turns", result.get("turns"), final_event.get("turns"))
        diffs.extend(_queue_trajectory_diffs(events))
        flood = _recompute_flood(events, card)
        diffs.extend(
            _ledger_diffs(run_dir, _require_mapping(result, "ledger", context="result.yaml"))
        )
    else:
        # 離線評分不經回合協定（spec §5.2）
        protocol_failed = False
        final_event = None

    _compare(diffs, "protocol_failed", result.get("protocol_failed"), protocol_failed)
    clear = compute_clear(
        critical_pass=gates.critical_pass,
        main_public_green=main_public_green,
        protocol_failed=protocol_failed,
    )
    _compare(diffs, "clear", result.get("clear"), clear)
    if final_event is not None:
        _compare(diffs, "events.final.clear", final_event.get("clear"), clear)

    return ReplayReport(level="L1", identical=not diffs, diffs=tuple(diffs), flood=flood)


# ---------------------------------------------------------------------------
# L2 重執行比對
# ---------------------------------------------------------------------------


def replay_l2(run_dir: Path, runner: ProbeReexecutor) -> ReplayReport:
    """pinned 環境重執行全部 probes 後走 L1（spec §12.2）。

    非 perf probe（functional／compat／robustness／regression／requirement）
    的 status 與 case 級結果必須與封存相等；perf-only probe 差異落
    `perf_deviations`、不算 fail。重執行缺 probe／多出未封存 probe 一律
    fail-closed 記 diff。
    """
    run_dir = Path(run_dir)
    l1 = replay_l1(run_dir)
    card = _load_card_for(_load_run_record(run_dir))
    result = _load_result(run_dir)
    reexecuted = runner(run_dir)

    perf_only = _perf_only_ids(card)
    diffs: list[ReplayDiff] = list(l1.diffs)
    deviations: list[PerfDeviation] = []

    for section, fresh in (
        ("public", reexecuted.public),
        ("evaluator", reexecuted.evaluator),
    ):
        archived = _outcomes_section(result, section)
        for probe_id, arch in archived.items():
            prefix = f"probes.{section}.{probe_id}"
            fresh_outcome = fresh.get(probe_id)
            if fresh_outcome is None:
                # 重執行沒有產出封存過的 probe → fail-closed（perf 亦然：
                # 容忍帶只涵蓋 timing 差異，不涵蓋重執行不完整）
                diffs.append(ReplayDiff(field=prefix, archived=arch.status, recomputed=None))
                continue
            if probe_id in perf_only:
                if (
                    fresh_outcome.status != arch.status
                    or fresh_outcome.wall_ms != arch.wall_ms
                ):
                    deviations.append(
                        PerfDeviation(
                            probe_id=probe_id,
                            section=section,
                            archived_status=arch.status,
                            reexecuted_status=fresh_outcome.status,
                            archived_wall_ms=arch.wall_ms,
                            reexecuted_wall_ms=fresh_outcome.wall_ms,
                        )
                    )
                continue
            for attr in ("status", "cases_total", "cases_passed"):
                _compare(
                    diffs, f"{prefix}.{attr}", getattr(arch, attr), getattr(fresh_outcome, attr)
                )
        for probe_id in sorted(set(fresh) - set(archived)):
            diffs.append(
                ReplayDiff(
                    field=f"probes.{section}.{probe_id}",
                    archived=None,
                    recomputed=fresh[probe_id].status,
                )
            )

    return ReplayReport(
        level="L2",
        identical=not diffs,
        diffs=tuple(diffs),
        perf_deviations=tuple(deviations),
        flood=l1.flood,
    )


# ---------------------------------------------------------------------------
# 重算 helpers
# ---------------------------------------------------------------------------


def _recompute_power(
    card: IssueCard,
    outcomes: Mapping[str, ProbeOutcome],
    archived_power: Mapping,
    gates,
) -> PowerReport:
    """自封存 outcomes／觀測值／PerfJudgment 重算 PowerReport（含 cap）。"""
    rubric = card.power_rubric
    breakdown = _require_mapping(
        archived_power, "maintainability_breakdown", context="result.yaml power"
    )
    lint_new = breakdown.get("lint_new_diagnostics")
    if lint_new is not None and (isinstance(lint_new, bool) or not isinstance(lint_new, int)):
        raise ReplayError(f"lint_new_diagnostics 必須是 int 或 null：{lint_new!r}")
    maintainability = maintainability_from_observations(
        card,
        production_loc=_require_int(breakdown, "production_loc", context="maintainability_breakdown"),
        scope_hard_files=tuple(
            str(p) for p in _require_list(breakdown, "scope_hard_files", context="maintainability_breakdown")
        ),
        scope_soft_loc=_require_int(breakdown, "scope_soft_loc", context="maintainability_breakdown"),
        lint_new_diagnostics=lint_new,
    )
    judgments = _judgments_from(archived_power)
    runtime_efficiency = runtime_efficiency_from_judgments(
        rubric.runtime_efficiency.points, judgments
    )
    functional = score_functional(rubric.functional, outcomes)
    robustness = score_robustness(rubric.robustness, outcomes)
    compatibility = score_compatibility(rubric.compatibility, outcomes)
    report = PowerReport(
        functional=functional,
        robustness=robustness,
        compatibility=compatibility,
        maintainability=maintainability.total,
        runtime_efficiency=runtime_efficiency,
        total=(
            functional
            + robustness
            + compatibility
            + maintainability.total
            + runtime_efficiency
        ),
        maintainability_breakdown=maintainability,
        perf_judgments=judgments,
    )
    return apply_power_cap(report, gates)


def _judgments_from(archived_power: Mapping) -> tuple[PerfJudgment, ...]:
    raw = archived_power.get("perf_judgments")
    if not isinstance(raw, list):
        raise ReplayError("result.yaml power 缺 perf_judgments 列表")
    judgments: list[PerfJudgment] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ReplayError(f"perf_judgment 必須是 mapping：{item!r}")
        missing = [key for key in _JUDGMENT_KEYS if key not in item]
        if missing:
            raise ReplayError(f"perf_judgment 缺欄位 {missing}：{item!r}")
        judgments.append(
            PerfJudgment(
                probe_id=str(item["probe_id"]),
                wall_ms=item["wall_ms"],
                budget_ms=item["budget_ms"],
                passed=bool(item["passed"]),
                within_budget=bool(item["within_budget"]),
            )
        )
    return tuple(judgments)


def _queue_trajectory_diffs(events: Sequence[Mapping]) -> list[ReplayDiff]:
    """queue 軌跡重算：每個 snapshot 的 b_t／m_t 必須與 open_items 一致。"""
    diffs: list[ReplayDiff] = []
    for event in events:
        queue = event.get("queue")
        if queue is None:
            continue
        context = f"events[{event.get('seq')}].queue"
        if not isinstance(queue, Mapping):
            raise ReplayError(f"{context} 非 mapping")
        open_items = queue.get("open_items")
        if not isinstance(open_items, list):
            raise ReplayError(f"{context} 缺 open_items")
        b_t = len(open_items)
        m_t = sum(
            1
            for item in open_items
            if isinstance(item, Mapping) and item.get("type") == "MAIN"
        )
        if queue.get("b_t") != b_t:
            diffs.append(ReplayDiff(f"{context}.b_t", queue.get("b_t"), b_t))
        if queue.get("m_t") != m_t:
            diffs.append(ReplayDiff(f"{context}.m_t", queue.get("m_t"), m_t))
    return diffs


def _recompute_flood(events: Sequence[Mapping], card: IssueCard) -> FloodMetrics:
    """flood 計量重算（τ 未校準 → 1.0 標記 uncalibrated，spec §8.2）。"""
    try:
        return flood_metrics(events, card, load_flood_coeffs())
    except FloodError as exc:
        raise ReplayError(f"flood 重算失敗：{exc}") from exc


def _ledger_diffs(run_dir: Path, archived: Mapping) -> list[ReplayDiff]:
    entries = load_ledger(run_dir)
    work = aggregate_work_tokens(entries)
    recomputed = {
        "entries": len(entries),
        "billed_input_total": sum(e.billed_input_total for e in entries),
        "billed_output_total": sum(e.billed_output_total for e in entries),
        "work_tokens": _NA if work is None else work,
        "reviewer_calls": sum(1 for e in entries if e.role == "reviewer"),
    }
    diffs: list[ReplayDiff] = []
    for field, value in recomputed.items():
        _compare(diffs, f"ledger.{field}", archived.get(field), value)
    return diffs


def load_ledger(run_dir: Path) -> list[LedgerEntry]:
    """讀入 run 目錄封存的 ``ledger.jsonl``（fail-closed；replay 與 report 共用）。"""
    path = run_dir / _LEDGER_FILE
    if not path.is_file():
        raise ReplayError(f"ledger.jsonl 不存在：{path}")
    entries: list[LedgerEntry] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"ledger.jsonl 第 {lineno} 行非合法 JSON：{exc}") from exc
        if not isinstance(record, dict) or record.get("schema_version") != _LEDGER_SCHEMA_VERSION:
            raise ReplayError(f"ledger.jsonl 第 {lineno} 行 schema_version 不符")
        fields = {key: value for key, value in record.items() if key != "schema_version"}
        try:
            entries.append(LedgerEntry(**fields))
        except (TypeError, LedgerError) as exc:
            raise ReplayError(f"ledger.jsonl 第 {lineno} 行欄位非法：{exc}") from exc
    return entries


def _perf_only_ids(card: IssueCard) -> frozenset[str]:
    """只出現在 runtime_efficiency rubric 的 probe（L2 容忍帶適用範圍）。"""
    rubric = card.power_rubric
    non_perf: set[str] = set()
    non_perf.update(cr.hidden_probe for cr in card.critical_requirements)
    non_perf.update(group.probe for group in rubric.functional.groups)
    non_perf.update(rubric.robustness.probes)
    non_perf.update(rubric.compatibility.probes)
    non_perf.update(cp.probe for cp in card.compat_probes)
    non_perf.update(req.probe for req in card.public_requirements)
    for rp in card.regression_probes:
        if rp.path is not None:
            non_perf.add(rp.path)
        elif rp.smoke is not None:
            non_perf.add(smoke_probe_id(rp.smoke))
    return frozenset(set(rubric.runtime_efficiency.probes) - non_perf)


# ---------------------------------------------------------------------------
# 封存讀取（fail-closed）
# ---------------------------------------------------------------------------


def _load_result(run_dir: Path) -> Mapping:
    path = run_dir / _RESULT_FILE
    if not path.is_file():
        raise ReplayError(f"result.yaml 不存在：{path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ReplayError("result.yaml 內容必須是 mapping")
    if data.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ReplayError(
            f"result.yaml schema_version 不符：{data.get('schema_version')!r}"
        )
    return data


def _load_run_record(run_dir: Path) -> Mapping:
    # RunStore.open 已於 replay 入口 fail-closed 驗證 run.yaml schema
    record = yaml.safe_load((run_dir / _RUN_FILE).read_text(encoding="utf-8"))
    if not isinstance(record, Mapping):
        raise ReplayError("run.yaml 內容必須是 mapping")
    return record


def _load_card_for(record: Mapping) -> IssueCard:
    encounter_dir = Path(str(record.get("encounter_dir", "")))
    card_path = encounter_dir / "card.yaml"
    if not card_path.is_file():
        raise ReplayError(f"encounter card.yaml 不存在：{card_path}")
    return load_card(card_path)


def _outcomes_section(result: Mapping, section: str) -> dict[str, ProbeOutcome]:
    probes = _require_mapping(result, "probes", context="result.yaml")
    data = _require_mapping(probes, section, context="result.yaml probes")
    return {str(pid): _outcome_from(str(pid), item) for pid, item in data.items()}


def _outcome_from(probe_id: str, item: object) -> ProbeOutcome:
    if not isinstance(item, Mapping):
        raise ReplayError(f"probe outcome 非 mapping：{probe_id}")
    missing = [key for key in _OUTCOME_KEYS if key not in item]
    if missing:
        raise ReplayError(f"probe outcome 缺欄位 {missing}：{probe_id}")
    status = item["status"]
    if status not in _PROBE_STATUSES:
        raise ReplayError(f"probe status 非法：{probe_id}={status!r}")
    fingerprints = item["failure_fingerprints"]
    if not isinstance(fingerprints, list):
        raise ReplayError(f"failure_fingerprints 必須是列表：{probe_id}")
    return ProbeOutcome(
        status=status,
        cases_total=_require_int(item, "cases_total", context=probe_id),
        cases_passed=_require_int(item, "cases_passed", context=probe_id),
        failure_fingerprints=tuple(str(f) for f in fingerprints),
        wall_ms=_require_int(item, "wall_ms", context=probe_id),
        cpu_ms=_require_int(item, "cpu_ms", context=probe_id),
    )


def _final_event(events: Sequence[Mapping]) -> Mapping:
    finals = [event for event in events if event.get("type") == "final"]
    if len(finals) != 1:
        raise ReplayError(f"events 應恰有一筆 final event，實得 {len(finals)}")
    return finals[0]


def _require_mapping(container: Mapping, key: str, *, context: str) -> Mapping:
    value = container.get(key)
    if not isinstance(value, Mapping):
        raise ReplayError(f"{context} 缺 {key} mapping")
    return value


def _require_list(container: Mapping, key: str, *, context: str) -> list:
    value = container.get(key)
    if not isinstance(value, list):
        raise ReplayError(f"{context} 缺 {key} 列表")
    return value


def _require_int(container: Mapping, key: str, *, context: str) -> int:
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReplayError(f"{context} 缺欄位或值非整數：{key}={value!r}")
    return value


def _is_green(outcomes: Mapping[str, ProbeOutcome], probe_id: str) -> bool:
    return probe_id in outcomes and outcomes[probe_id].status == "passed"


def _compare(diffs: list[ReplayDiff], field: str, archived, recomputed) -> None:
    if archived != recomputed:
        diffs.append(ReplayDiff(field=field, archived=archived, recomputed=recomputed))
