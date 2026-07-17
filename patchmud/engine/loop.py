"""Turn loop：author turns、probe 排程、終局與 reviewer subcall（spec §5、§6）。

每 author turn 的固定序（spec §5.2、報告 §9.4）：
render → adapter.complete → parse → enforcer.check → 執行 → probe 排程
→ queue update → checkpoint → 恰一筆 turn event（plan invariant 5）。

- **每一次作者呼叫恰好消耗一個 author turn**，不論動作合法與否；
  ReviewerSubcall 嵌在所屬 turn（不耗 turn、findings 同 turn 結束進 render，F9）。
- 連續 3 次 invalid（parse error）／illegal → ``failed:protocol`` 終局；
  error result（apply 失敗、INSPECT 遭拒、空 stack ROLLBACK）是合法動作的
  失敗結果，重置連續計數。
- 終局四觸發（COMMIT／max_turns／wall_clock／failed:protocol）一律執行
  全部 public probes ＋ hidden evaluator 全套；`Clear` 只用 §5.2 唯一公式。
- 執行面全部走注入 seam（:class:`RunConfig`）：probe suite、evaluator、
  reviewer adapter、時鐘——unit tests 注入 fake，不啟真 namespace、不打真
  API（plan invariant 3）；真佈線在 cli（`patchmud run`）。
- 敘事文字一律出自 zh-TW render pack（§5.4）；命令關鍵字、probe id、
  artifact 格式維持英文。
"""

from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

import yaml

from patchmud.adapters.base import ModelAdapter
from patchmud.deck.model import IssueCard
from patchmud.engine import render_zh_tw as zh
from patchmud.engine.plan_schema import PlanError, validate_plan
from patchmud.engine.prompts import build_system_prompt
from patchmud.engine.protocol import (
    Action,
    Commit,
    Inspect,
    Look,
    ParseFailure,
    Patch,
    PlayPlan,
    Rollback,
    RunTest,
    SummonReviewer,
    Triage,
    WriteTest,
    parse_reply,
)
from patchmud.engine.queue import DiffGeometry, IssueQueue
from patchmud.engine.render import IssueView, RunState, render_state
from patchmud.engine.reviewer import (
    build_reviewer_messages,
    parse_findings,
    render_findings,
)
from patchmud.engine.strategy import EnforcementState, Loadout, StrategyEnforcer
from patchmud.evaluator.evaluate import FinalEvaluation
from patchmud.evaluator.gates import compute_clear
from patchmud.evaluator.power import FileChange, PowerReport
from patchmud.ledger.tokens import (
    LedgerEntry,
    aggregate_billed_totals,
    aggregate_work_tokens,
    map_usage,
)
from patchmud.sandbox.isolate import Execution
from patchmud.sandbox.probes import (
    DEFAULT_PYTEST_ARGV,
    ProbeOutcome,
    ProbeResults,
)
from patchmud.sandbox.workspace import Workspace
from patchmud.store.run_store import RunStore

__all__ = [
    "END_COMMIT",
    "END_MAX_TURNS",
    "END_PROTOCOL",
    "END_WALL_CLOCK",
    "RunConfig",
    "RunResult",
    "build_agent_test_runner",
    "run_encounter",
]

END_COMMIT = "commit"
END_MAX_TURNS = "max_turns"
END_WALL_CLOCK = "wall_clock"
END_PROTOCOL = "failed:protocol"

#: 互斥資源欄位不可得記 NA、不記 0（§10.1）；result.yaml 序列化用。
_NA = "NA"

_INSPECT_LIMIT_BYTES = 64 * 1024
_LOOK_DEPTH = 3
_LEDGER_FILE = "ledger.jsonl"
_LEDGER_SCHEMA_VERSION = 1
_ARTIFACTS_DIR = "artifacts"
_AGENT_TEST_DIR = "tests/agent"
_AGENT_REPORT_RELPATH = ".patchmud-agent-report.xml"
_PYTEST_FLAGS: tuple[str, ...] = ("-q", "-p", "no:cacheprovider")
_DEFAULT_TIMEOUT_S = 120.0


class _ProbeSuite(Protocol):
    """公開 probe 套件 seam（真實現：`ProbeSuite.from_card`＋IsolationRunner）。"""

    @property
    def probe_ids(self) -> tuple[str, ...]: ...

    def run(self, workspace, subset=None) -> ProbeResults: ...


class _Runner(Protocol):
    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution: ...


#: agent 自建測試 runner：workspace → nodeid 級 ProbeResults（§6.1 red 判定）。
AgentTestRunner = Callable[[Workspace], ProbeResults]

#: 終局 evaluator seam：final cumulative diff → FinalEvaluation（§9）。
EvaluateFn = Callable[[str], FinalEvaluation]


@dataclass
class RunConfig:
    """一場 run 的執行面佈線（unit tests 全 fake；真佈線見 cli `patchmud run`）。"""

    workspace: Workspace
    probe_suite: _ProbeSuite
    evaluate: EvaluateFn
    #: fresh-context reviewer adapter；None → 與作者同一 adapter（同 model snapshot）。
    reviewer_adapter: ModelAdapter | None = None
    #: agent 測試 nodeid runner；None → 不執行（T1 真佈線必須提供）。
    run_agent_tests: AgentTestRunner | None = None
    clock: Callable[[], float] = field(default=time.monotonic)
    #: 測試用 max_turns 覆寫；None → card.max_turns。
    max_turns: int | None = None
    #: 連續 invalid / illegal 幾次觸發 failed:protocol（spec §5.2 = 3）。
    max_consecutive_invalid: int = 3
    #: 旁觀者 callback（`patchmud run --live`）；每 append 一個 event 即推送，
    #: 純視圖、不影響評分。None → 不推送。
    spectator: Callable[[dict], None] | None = None


@dataclass(frozen=True)
class RunResult:
    """run_encounter 的回傳摘要；完整落盤在 store（result.yaml、events）。"""

    clear: int
    end_reason: str
    turns_used: int
    protocol_failed: bool
    evaluation: FinalEvaluation
    ledger_entries: tuple[LedgerEntry, ...]
    result: dict


# ---------------------------------------------------------------------------
# run loop
# ---------------------------------------------------------------------------


def run_encounter(
    card: IssueCard,
    adapter: ModelAdapter,
    loadout: Loadout,
    config: RunConfig,
    store: RunStore,
    *,
    human: bool = False,
) -> RunResult:
    """打完一場 encounter：turn 0 baseline → author turns → 終局評分落盤。

    ``human=True``（``patchmud play``，spec §5.4）：引擎、probe、queue、
    評分完全同構，僅 result.yaml 標記 ``human: true``——human run 永不進
    ranked 資料與任何聚合指標（metrics 層 ``HumanRunExcluded``）。
    """
    session = _Session(card, adapter, loadout, config, store, human=human)
    session.setup()
    while session.next_turn():
        pass
    return session.finalize()


class _Session:
    """單場 run 的可變狀態；`run_encounter` 是唯一入口。"""

    def __init__(
        self,
        card: IssueCard,
        adapter: ModelAdapter,
        loadout: Loadout,
        config: RunConfig,
        store: RunStore,
        *,
        human: bool = False,
    ) -> None:
        self.card = card
        self.adapter = adapter
        self.loadout = loadout
        self.config = config
        self.store = store
        self.human = human
        self.workspace = config.workspace
        self.enforcer = StrategyEnforcer(loadout)
        self.max_turns = config.max_turns or card.max_turns

        self.transcript: list[dict] = []
        self.ledger: list[LedgerEntry] = []
        self.feedback: list[str] = []
        self.claims: list[str] = []
        self.invalid_streak = 0
        self.production_applied = False
        self.turns_used = 0
        self.review_seq = 0
        self.end_reason: str | None = None

        self.queue: IssueQueue | None = None
        self.last_public: ProbeResults = ProbeResults({})
        self.repo_files: frozenset[str] = frozenset()
        self.started = 0.0

    # ---- setup（turn 0 baseline，spec §5.2／F11） -------------------------

    def setup(self) -> None:
        self.started = self.config.clock()
        self.system_prompt = build_system_prompt(self.card)
        self.repo_files = frozenset(_worktree_files(self.workspace.worktree))

        baseline = self.config.probe_suite.run(self.workspace)
        self.last_public = baseline
        self.queue = IssueQueue.from_card(self.card, baseline)
        checkpoint = self.workspace.checkpoint()
        self._emit(
            {
                "type": "baseline",
                "probes": _statuses(baseline),
                "queue": self.queue.snapshot(),
                "checkpoint": checkpoint,
            }
        )

    # ---- author turn -------------------------------------------------------

    def next_turn(self) -> bool:
        """執行一個 author turn；回傳 False = 終局觸發，停止 loop。"""
        assert self.queue is not None
        if self.turns_used >= self.max_turns:
            self.end_reason = END_MAX_TURNS
            return False
        if self._elapsed() >= self.card.wall_clock_seconds:
            self.end_reason = END_WALL_CLOCK
            return False

        turn = self.turns_used + 1
        user_content = "\n\n".join([*self.feedback, self._render_state(turn)])
        self.feedback = []
        self.transcript.append({"role": "user", "content": user_content})
        messages = [
            {"role": "system", "content": self.system_prompt},
            *self.transcript,
        ]
        response = self.adapter.complete(messages)
        self.turns_used = turn
        self.transcript.append({"role": "assistant", "content": response.text})
        author_entry = map_usage(
            self.adapter.usage_provider,
            response.usage_raw,
            turn=turn,
            role="author",
            wall_clock_ms=response.wall_ms,
            prompt_bytes=sum(
                len(str(m.get("content", "")).encode("utf-8")) for m in messages
            ),
            generated_bytes=len(response.text.encode("utf-8")),
        )
        self._record_ledger(author_entry)

        outcome = self._dispatch(turn, parse_reply(response.text))

        geometry = _diff_geometry(
            self.workspace.cumulative_diff(),
            self.workspace.diff_stats().reverted_loc,
        )
        self.queue.update(
            outcome.probe_results or ProbeResults({}), geometry, outcome.action
        )
        checkpoint = self.workspace.checkpoint()

        event = {
            "type": "turn",
            "turn": turn,
            "action": outcome.action.keyword if outcome.action else None,
            "outcome": outcome.kind,
            "detail": outcome.detail,
            "checkpoint": checkpoint,
            "probes": _statuses(outcome.probe_results or {}),
            "queue": self.queue.snapshot(),
            "ledger": {
                "author": _entry_dict(author_entry),
                "reviewer": outcome.reviewer_entry
                and _entry_dict(outcome.reviewer_entry),
            },
            "reviewer_subcall": outcome.reviewer_event,
        }
        if outcome.inspect_denied is not None:
            event["inspect_denied"] = outcome.inspect_denied
        self._emit(event)

        if outcome.kind in ("parse_error", "illegal"):
            self.invalid_streak += 1
        else:
            self.invalid_streak = 0

        if outcome.commit:
            self.end_reason = END_COMMIT
            return False
        if self.invalid_streak >= self.config.max_consecutive_invalid:
            self.end_reason = END_PROTOCOL
            return False
        return True

    # ---- 動作執行 -----------------------------------------------------------

    def _dispatch(self, turn: int, parsed: Action | ParseFailure) -> "_TurnOutcome":
        if isinstance(parsed, ParseFailure):
            self.feedback.append(parsed.hint)
            return _TurnOutcome(kind="parse_error", detail=parsed.hint)

        verdict = self.enforcer.check(
            parsed, EnforcementState(production_patch_applied=self.production_applied)
        )
        if not verdict.legal:
            assert verdict.reason is not None
            return self._illegal(parsed, verdict.reason)

        if isinstance(parsed, Look):
            self.feedback.append(_render_tree(self.workspace.worktree))
            return _TurnOutcome(action=parsed)
        if isinstance(parsed, Inspect):
            return self._do_inspect(parsed)
        if isinstance(parsed, PlayPlan):
            return self._do_plan(parsed)
        if isinstance(parsed, WriteTest):
            return self._do_write_test(parsed)
        if isinstance(parsed, Patch):
            return self._do_patch(turn, parsed)
        if isinstance(parsed, RunTest):
            return self._do_run_test(parsed)
        if isinstance(parsed, SummonReviewer):
            return self._do_summon(turn, parsed)
        if isinstance(parsed, Triage):
            self.feedback.append(zh.text("loop.triage_done"))
            return _TurnOutcome(action=parsed)
        if isinstance(parsed, Rollback):
            return self._do_rollback(parsed)
        assert isinstance(parsed, Commit)
        return _TurnOutcome(action=parsed, commit=True)

    def _illegal(self, action: Action, reason: str) -> "_TurnOutcome":
        text = zh.text("loop.illegal", reason=reason)
        self.feedback.append(text)
        return _TurnOutcome(kind="illegal", detail=reason, action=action)

    def _do_inspect(self, action: Inspect) -> "_TurnOutcome":
        content = _read_inspect(self.workspace.worktree, action.path)
        if content is None:
            text = zh.text("loop.inspect_denied", path=action.path)
            self.feedback.append(text)
            return _TurnOutcome(
                kind="error", detail=text, action=action, inspect_denied=action.path
            )
        self.feedback.append(content)
        return _TurnOutcome(action=action)

    def _do_plan(self, action: PlayPlan) -> "_TurnOutcome":
        validated = validate_plan(
            action.payload, self.card, repo_files=self.repo_files
        )
        if isinstance(validated, PlanError):
            # schema 不過 → illegal action（spec §5.1、§6.3；消耗 turn）
            return self._illegal(action, validated.reason)
        self.enforcer.record_plan(validated)
        _write_artifact(
            self.store.run_dir,
            "plan.yaml",
            {
                "schema_version": 1,
                "requirements": list(validated.requirements),
                "invariants": list(validated.invariants),
                "files_to_inspect": list(validated.files_to_inspect),
                "risks": list(validated.risks),
                "test_targets": list(validated.test_targets),
            },
        )
        self.feedback.append(zh.text("loop.plan_accepted"))
        return _TurnOutcome(action=action)

    def _do_write_test(self, action: WriteTest) -> "_TurnOutcome":
        applied = self.workspace.apply_patch(action.payload, kind="test")
        if applied.rejected:
            # 觸及 production／既有測試檔 → illegal（spec §5.1）
            return self._illegal(action, applied.reason or "")
        touched = _patch_paths(action.payload)
        file_hashes = _hash_files(self.workspace.worktree, touched)
        agent_results = self._run_agent_tests()
        nodeids = [
            nodeid
            for nodeid in agent_results
            if nodeid.split("::", 1)[0] in set(touched)
        ]
        self.enforcer.record_write_test(file_hashes, nodeids)
        self.enforcer.on_probe_results(agent_results)
        public = self._run_public()
        self.feedback.append(zh.text("loop.write_test_applied"))
        self.feedback.append(_probe_report(public))
        return _TurnOutcome(action=action, probe_results=public)

    def _do_patch(self, turn: int, action: Patch) -> "_TurnOutcome":
        applied = self.workspace.apply_patch(action.payload, kind="production")
        if applied.rejected:
            # apply 失敗 → error result，不改變 worktree（spec §5.1）
            text = zh.text("loop.patch_rejected", reason=applied.reason or "")
            self.feedback.append(text)
            return _TurnOutcome(kind="error", detail=text, action=action)
        self.production_applied = True
        self.enforcer.record_patch_applied()
        if action.claim:
            self.claims.append(f"turn {turn}: {action.claim}")
        agent_results = self._run_agent_tests()
        self.enforcer.on_probe_results(agent_results)
        public = self._run_public()
        self.feedback.append(zh.text("loop.patch_applied"))
        self.feedback.append(_probe_report(public))
        return _TurnOutcome(action=action, probe_results=public)

    def _do_run_test(self, action: RunTest) -> "_TurnOutcome":
        if action.target is None:
            agent_results = self._run_agent_tests()
            self.enforcer.on_probe_results(agent_results)
            public = self._run_public()
            self.feedback.append(_probe_report(public))
            return _TurnOutcome(action=action, probe_results=public)
        if action.target in self.config.probe_suite.probe_ids:
            public = self._run_public(subset=[action.target])
            self.feedback.append(_probe_report(public))
            return _TurnOutcome(action=action, probe_results=public)
        if action.target.startswith(_AGENT_TEST_DIR):
            agent_results = self._run_agent_tests()
            self.enforcer.on_probe_results(agent_results)
            self.feedback.append(_probe_report(agent_results))
            return _TurnOutcome(action=action)
        # 非白名單 target → illegal（spec §5.1）
        return self._illegal(
            action, zh.text("loop.run_test_bad_target", target=action.target)
        )

    def _do_summon(self, turn: int, action: SummonReviewer) -> "_TurnOutcome":
        self.review_seq += 1
        reviewer = self.config.reviewer_adapter or self.adapter
        messages = build_reviewer_messages(
            self.card,
            cumulative_diff=self.workspace.cumulative_diff(),
            probe_results=self.last_public,
            plan=self.enforcer.plan,
            claims=tuple(self.claims),
        )
        response = reviewer.complete(messages)
        entry = map_usage(
            reviewer.usage_provider,
            response.usage_raw,
            turn=turn,
            role="reviewer",
            wall_clock_ms=response.wall_ms,
            prompt_bytes=sum(
                len(str(m.get("content", "")).encode("utf-8")) for m in messages
            ),
            generated_bytes=len(response.text.encode("utf-8")),
        )
        self._record_ledger(entry)

        findings = parse_findings(response.text)
        valid = findings is not None
        artifact_name = f"review_{self.review_seq}.yaml"
        _write_artifact(
            self.store.run_dir,
            artifact_name,
            {
                "schema_version": 1,
                "turn": turn,
                "valid": valid,
                "findings": [
                    {
                        "category": f.category,
                        "severity": f.severity,
                        "summary": f.summary,
                        "evidence": [
                            {"path": e.path, "line": e.line} for e in f.evidence
                        ],
                    }
                    for f in (findings or ())
                ],
                "raw_reply": response.text,
            },
        )
        # F8：合格 review = schema-valid ＋ 輸入 diff 非空 ＋ findings 已 render
        # （findings 於本 turn 結束進 feedback → 下一次作者呼叫（COMMIT 前）可見）
        self.enforcer.record_review(
            schema_valid=valid,
            diff_nonempty=self.production_applied,
            findings_rendered=True,
        )
        self.feedback.append(
            render_findings(findings) if findings is not None else zh.text("reviewer.invalid")
        )
        return _TurnOutcome(
            action=action,
            reviewer_entry=entry,
            reviewer_event={
                "artifact": f"{_ARTIFACTS_DIR}/{artifact_name}",
                "valid": valid,
                "findings": len(findings or ()),
            },
        )

    def _do_rollback(self, action: Rollback) -> "_TurnOutcome":
        if not self.workspace.rollback():
            text = zh.text("loop.rollback_empty")
            self.feedback.append(text)
            return _TurnOutcome(kind="error", detail=text, action=action)
        public = self._run_public()
        self.feedback.append(zh.text("loop.rollback_done"))
        self.feedback.append(_probe_report(public))
        return _TurnOutcome(action=action, probe_results=public)

    # ---- 終局（四觸發一律全套 public + hidden evaluator，§5.2） -------------

    def finalize(self) -> RunResult:
        assert self.queue is not None and self.end_reason is not None
        final_public = self.config.probe_suite.run(self.workspace)
        self.last_public = final_public
        final_diff = self.workspace.cumulative_diff()
        evaluation = self.config.evaluate(final_diff)

        agent_results = self._run_agent_tests()
        agent_hashes = _hash_files(
            self.workspace.worktree,
            _agent_test_files(self.workspace.worktree),
        )
        self.enforcer.on_final(agent_results, file_hashes=agent_hashes)

        main_public_green = all(
            _is_green(final_public, req.probe)
            for req in self.card.public_requirements
        )
        protocol_failed = self.end_reason == END_PROTOCOL
        clear = compute_clear(
            critical_pass=evaluation.gates.critical_pass,
            main_public_green=main_public_green,
            protocol_failed=protocol_failed,
        )

        self.store.append_event(
            {
                "type": "final",
                "end_reason": self.end_reason,
                "turns": self.turns_used,
                "probes": _statuses(final_public),
                "queue": self.queue.snapshot(),
                "clear": clear,
            }
        )
        result = self._build_result(
            clear=clear,
            main_public_green=main_public_green,
            protocol_failed=protocol_failed,
            evaluation=evaluation,
            final_public=final_public,
        )
        self.store.write_result(result)
        return RunResult(
            clear=clear,
            end_reason=self.end_reason,
            turns_used=self.turns_used,
            protocol_failed=protocol_failed,
            evaluation=evaluation,
            ledger_entries=tuple(self.ledger),
            result=result,
        )

    def _build_result(
        self,
        *,
        clear: int,
        main_public_green: bool,
        protocol_failed: bool,
        evaluation: FinalEvaluation,
        final_public: ProbeResults,
    ) -> dict:
        tdd_state = self.enforcer.tdd_state
        work_tokens = aggregate_work_tokens(self.ledger)
        billed_input, billed_output = aggregate_billed_totals(self.ledger)
        return {
            "run_id": self.store.run_dir.name,
            "mode": "run",
            # human run（patchmud play）永不進 ranked 資料（spec §5.4）
            "human": self.human,
            "loadout": self.loadout.name,
            "clear": clear,
            "end_reason": self.end_reason,
            "protocol_failed": protocol_failed,
            "turns": self.turns_used,
            "main_public_green": main_public_green,
            "gates": {
                "critical_pass": evaluation.gates.critical_pass,
                "power_cap": evaluation.gates.power_cap,
                "run_invalid": evaluation.gates.run_invalid,
            },
            "power": _power_dict(evaluation.power),
            "probes": {
                "public": _probes_dict(final_public),
                "evaluator": _probes_dict(evaluation.probe_outcomes),
            },
            "strategy": {
                "observed_tdd_workflow": self.enforcer.observed_tdd_workflow,
                "observed_planning": self.enforcer.observed_planning,
                "tdd_compliant": tdd_state.compliant,
                "reviewer_gate_satisfied": self.enforcer.reviewer_gate_satisfied,
                # §6.1.6：T1 且終局 non-compliant → strategy_violation
                "strategy_violation": self.loadout.tdd and not tdd_state.compliant,
            },
            "ledger": {
                "entries": len(self.ledger),
                # billed totals：human run 全 NA（NA 傳染；不記 0，§10.1）
                "billed_input_total": billed_input if billed_input is not None else _NA,
                "billed_output_total": (
                    billed_output if billed_output is not None else _NA
                ),
                "work_tokens": work_tokens if work_tokens is not None else _NA,
                "reviewer_calls": sum(
                    1 for e in self.ledger if e.role == "reviewer"
                ),
            },
            # milestone B：無 pricing snapshot／reference_cost → Economy NA（§10.1）
            "economy": _NA,
            "economy_reason": "run 無 pricing snapshot 或 reference_cost，Economy 不適用",
        }

    # ---- 內部 helpers --------------------------------------------------------

    def _emit(self, event: dict) -> None:
        """append event 並推送給 spectator（live 觀戰）；spectator 純視圖。"""
        self.store.append_event(event)
        if self.config.spectator is not None:
            self.config.spectator(event)

    def _elapsed(self) -> float:
        return self.config.clock() - self.started

    def _render_state(self, turn: int) -> str:
        assert self.queue is not None
        open_issues = tuple(
            IssueView(item_id=i.item_id, type=i.type, summary=i.text)
            for i in self.queue.open_items()
        )
        seconds_left = max(
            0, int(self.card.wall_clock_seconds - self._elapsed())
        )
        tokens_spent = sum(
            (e.billed_input_total or 0) + (e.billed_output_total or 0)
            for e in self.ledger
        )
        return render_state(
            RunState(
                turn=turn,
                max_turns=self.max_turns,
                encounter_id=self.card.issue_id,
                open_issues=open_issues,
                seconds_left=seconds_left,
                tokens_spent=tokens_spent,
            )
        )

    def _run_public(self, subset: Sequence[str] | None = None) -> ProbeResults:
        results = self.config.probe_suite.run(self.workspace, subset=subset)
        if subset is None:
            self.last_public = results
        return results

    def _run_agent_tests(self) -> ProbeResults:
        if self.config.run_agent_tests is None:
            return ProbeResults({})
        return self.config.run_agent_tests(self.workspace)

    def _record_ledger(self, entry: LedgerEntry) -> None:
        self.ledger.append(entry)
        record = {"schema_version": _LEDGER_SCHEMA_VERSION, **_entry_dict(entry)}
        with (self.store.run_dir / _LEDGER_FILE).open(
            "a", encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


@dataclass(frozen=True)
class _TurnOutcome:
    """一個 author turn 的執行觀測（event 記帳與 loop 控制用）。"""

    kind: str = "executed"  # executed | parse_error | illegal | error
    detail: str | None = None
    action: Action | None = None
    probe_results: ProbeResults | None = None
    reviewer_entry: LedgerEntry | None = None
    reviewer_event: dict | None = None
    inspect_denied: str | None = None
    commit: bool = False


# ---------------------------------------------------------------------------
# agent 自建測試 runner（真佈線；經 IsolationRunner seam 執行）
# ---------------------------------------------------------------------------


def build_agent_test_runner(
    runner: _Runner,
    *,
    pytest_argv: Sequence[str] = DEFAULT_PYTEST_ARGV,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> AgentTestRunner:
    """`tests/agent/**` 的 nodeid 級 runner（§6.1 valid red／green 閉環判定）。

    junitxml 逐 testcase 展開為 nodeid → ProbeOutcome；timeout／報告缺失
    → 空結果（不構成 red evidence，保守不 fail-open）。
    """

    def _run(workspace: Workspace) -> ProbeResults:
        worktree = Path(workspace.worktree)
        if not (worktree / _AGENT_TEST_DIR).is_dir():
            return ProbeResults({})
        workspace.restore_protected()
        report_path = worktree / _AGENT_REPORT_RELPATH
        argv = [
            *pytest_argv,
            *_PYTEST_FLAGS,
            f"--junitxml={_AGENT_REPORT_RELPATH}",
            _AGENT_TEST_DIR,
        ]
        try:
            ex = runner.run(argv, cwd=worktree, timeout_s=timeout_s)
            xml_text = (
                report_path.read_text(encoding="utf-8")
                if report_path.exists()
                else None
            )
        finally:
            report_path.unlink(missing_ok=True)
        if ex.timed_out or xml_text is None:
            return ProbeResults({})
        return _nodeid_results(xml_text, ex)

    return _run


def _nodeid_results(xml_text: str, ex: Execution) -> ProbeResults:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ProbeResults({})
    outcomes: dict[str, ProbeOutcome] = {}
    for case in root.iter("testcase"):
        nodeid = _nodeid(case)
        if nodeid is None:
            continue
        if case.findall("error"):
            status = "error"
        elif case.findall("failure"):
            status = "failed"
        elif case.findall("skipped"):
            continue
        else:
            status = "passed"
        fingerprints: tuple[str, ...] = ()
        if status != "passed":
            elems = case.findall("error") or case.findall("failure")
            fingerprints = tuple(_first_line(e) for e in elems)
        outcomes[nodeid] = ProbeOutcome(
            status=status,  # type: ignore[arg-type]
            cases_total=1,
            cases_passed=1 if status == "passed" else 0,
            failure_fingerprints=fingerprints,
            wall_ms=ex.wall_ms,
            cpu_ms=ex.cpu_ms,
        )
    return ProbeResults(outcomes)


def _nodeid(case: ET.Element) -> str | None:
    """junitxml testcase → pytest nodeid（classname 點路徑 → 檔案路徑啟發式）。"""
    name = case.get("name")
    classname = case.get("classname")
    if not name:
        return None
    if not classname:
        return name
    return f"{classname.replace('.', '/')}.py::{name}"


def _first_line(elem: ET.Element) -> str:
    for source in (elem.get("message"), elem.text):
        if source and source.strip():
            return source.strip().splitlines()[0]
    return "unknown failure"


# ---------------------------------------------------------------------------
# 純資料 helpers
# ---------------------------------------------------------------------------


def _statuses(results) -> dict[str, str]:
    return {probe_id: results[probe_id].status for probe_id in results}


def _is_green(results: ProbeResults, probe_id: str) -> bool:
    return probe_id in results and results[probe_id].status == "passed"


def _entry_dict(entry: LedgerEntry) -> dict:
    return asdict(entry)


def _probe_report(results) -> str:
    lines = [zh.text("loop.probe_header")]
    lines.extend(
        zh.text("loop.probe_line", probe_id=pid, status=results[pid].status)
        for pid in results
    )
    return "\n".join(lines)


def _render_tree(worktree: Path, depth: int = _LOOK_DEPTH) -> str:
    lines = [zh.text("loop.look_header", depth=depth)]
    for path in sorted(worktree.rglob("*")):
        rel = path.relative_to(worktree)
        if ".git" in rel.parts or len(rel.parts) > depth:
            continue
        lines.append("- " + rel.as_posix() + ("/" if path.is_dir() else ""))
    return "\n".join(lines)


def _read_inspect(worktree: Path, path_str: str) -> str | None:
    """INSPECT：sandbox 內、非黑名單、存在 → 內容（64KB 截斷）；否則 None。"""
    pure = PurePosixPath(path_str)
    if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts:
        return None
    target = worktree / path_str
    if not target.is_file():
        return None
    data = target.read_bytes()
    text = data[:_INSPECT_LIMIT_BYTES].decode("utf-8", errors="replace")
    lines = [
        zh.text("loop.inspect_header", path=path_str, size=len(data)),
        text,
    ]
    if len(data) > _INSPECT_LIMIT_BYTES:
        lines.append(zh.text("loop.inspect_truncated", limit=_INSPECT_LIMIT_BYTES))
    return "\n".join(lines)


def _worktree_files(worktree: Path) -> list[str]:
    return [
        p.relative_to(worktree).as_posix()
        for p in worktree.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(worktree).parts
    ]


def _agent_test_files(worktree: Path) -> list[str]:
    root = worktree / _AGENT_TEST_DIR
    if not root.is_dir():
        return []
    return [
        p.relative_to(worktree).as_posix() for p in root.rglob("*") if p.is_file()
    ]


def _hash_files(worktree: Path, paths: Sequence[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for rel in paths:
        target = worktree / rel
        if target.is_file():
            hashes[rel] = hashlib.sha256(target.read_bytes()).hexdigest()
    return hashes


def _patch_paths(diff: str) -> list[str]:
    """unified diff 的目標路徑（文字解析；strip a/ b/ 前綴、忽略 /dev/null）。"""
    paths: list[str] = []
    old: str | None = None
    for line in diff.splitlines():
        if line.startswith("--- "):
            token = line[4:].strip()
            old = None if token == "/dev/null" else _strip_prefix(token, "a/")
        elif line.startswith("+++ "):
            token = line[4:].strip()
            path = old if token == "/dev/null" else _strip_prefix(token, "b/")
            if path is not None and path not in paths:
                paths.append(path)
    return paths


def _strip_prefix(token: str, prefix: str) -> str:
    return token[len(prefix) :] if token.startswith(prefix) else token


def _diff_geometry(diff_text: str, reverted_loc: int) -> DiffGeometry:
    """cumulative diff 文字 → 逐檔幾何視圖（queue SCOPE／CHURN 判定資料源）。"""
    added: dict[str, int] = {}
    deleted: dict[str, int] = {}
    current: str | None = None
    old: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            token = line[4:].strip()
            old = None if token == "/dev/null" else _strip_prefix(token, "a/")
        elif line.startswith("+++ "):
            token = line[4:].strip()
            current = old if token == "/dev/null" else _strip_prefix(token, "b/")
            if current is not None:
                added.setdefault(current, 0)
                deleted.setdefault(current, 0)
        elif current is None:
            continue
        elif line.startswith("+") and not line.startswith("+++"):
            added[current] += 1
        elif line.startswith("-") and not line.startswith("---"):
            deleted[current] += 1
    changes = tuple(
        FileChange(path=path, added=added[path], deleted=deleted[path])
        for path in added
    )
    return DiffGeometry(file_changes=changes, reverted_loc=reverted_loc)


def _power_dict(power: PowerReport) -> dict:
    breakdown = power.maintainability_breakdown
    return {
        "functional": power.functional,
        "robustness": power.robustness,
        "compatibility": power.compatibility,
        "maintainability": power.maintainability,
        "runtime_efficiency": power.runtime_efficiency,
        "total": power.total,
        "maintainability_breakdown": {
            "diff_size": breakdown.diff_size,
            "scope": breakdown.scope,
            "lint": breakdown.lint,
            "total": breakdown.total,
            "production_loc": breakdown.production_loc,
            "scope_hard_files": list(breakdown.scope_hard_files),
            "scope_soft_loc": breakdown.scope_soft_loc,
            "lint_new_diagnostics": breakdown.lint_new_diagnostics,
        },
        "perf_judgments": [
            {
                "probe_id": j.probe_id,
                "wall_ms": j.wall_ms,
                "budget_ms": j.budget_ms,
                "passed": j.passed,
                "within_budget": j.within_budget,
            }
            for j in power.perf_judgments
        ],
    }


def _probes_dict(results) -> dict:
    return {
        probe_id: {
            "status": results[probe_id].status,
            "cases_total": results[probe_id].cases_total,
            "cases_passed": results[probe_id].cases_passed,
            "failure_fingerprints": list(results[probe_id].failure_fingerprints),
            "wall_ms": results[probe_id].wall_ms,
            "cpu_ms": results[probe_id].cpu_ms,
        }
        for probe_id in results
    }


def _write_artifact(run_dir: Path, name: str, payload: dict) -> None:
    artifacts = run_dir / _ARTIFACTS_DIR
    artifacts.mkdir(exist_ok=True)
    (artifacts / name).write_text(
        yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
