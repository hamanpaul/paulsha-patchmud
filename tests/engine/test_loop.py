"""Task 13 RED：turn loop 語意（spec §5.2、§6.2；plan Task 13 Step 1）。

以 `ScriptedAdapter` 與 fake（probe suite／evaluator／reviewer adapter）鎖定：
- 連續 3 次亂文 → `failed:protocol` 終局，且 evaluator 仍執行、Clear=0。
- `max_turns=2` 用盡 → 強制終局。
- agent 從不 RUN_TEST 直接 COMMIT → 引擎自動全套判定 Clear（F3）。
- SUMMON REVIEWER → reviewer subcall 不耗 turn、下一 turn 仍屬作者（F9）；
  findings 只落盤與 render，不進 queue（F10）。
- reviewer 輸入不含作者 transcript 與 hidden 路徑（§6.2 隔離）。
- 每 author turn 恰一 checkpoint 與一筆 turn event（plan invariant 5）。

unit tests 不啟真 namespace、不打真模型 API（plan invariant 3）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from patchmud.adapters.base import AdapterResponse, ModelAdapter
from patchmud.adapters.human import HumanAdapter
from patchmud.adapters.scripted import ScriptedAdapter
from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.engine import render_zh_tw as zh
from patchmud.engine.loop import RunConfig, run_encounter
from patchmud.engine.prompts import HARNESS_PROMPT_VERSION
from patchmud.engine.strategy import SOLO, Loadout
from patchmud.evaluator.evaluate import FinalEvaluation
from patchmud.evaluator.gates import GateResult
from patchmud.evaluator.power import MaintainabilityReport, PowerReport
from patchmud.sandbox.probes import ProbeResults, smoke_probe_id
from patchmud.sandbox.workspace import Workspace
from patchmud.store.run_store import RunStore
from tests.evaluator.helpers import make_outcome

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"
CARD = load_card(FIXTURE / "card.yaml")
MAIN = CARD.public_requirements[0].probe
STARTER = CARD.regression_probes[0].path
SMOKE = smoke_probe_id(CARD.regression_probes[1].smoke)
COMPAT = CARD.compat_probes[0].probe
HIDDEN = CARD.critical_requirements[0].hidden_probe
REFERENCE_DIFF = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")

BASE = {MAIN: "failed", STARTER: "passed", SMOKE: "passed", COMPAT: "passed"}
GREEN = {**BASE, MAIN: "passed"}

GARBAGE = "這一段完全不是合法回覆，沒有任何動作宣告。"
LOOK = "ACTION: LOOK"
COMMIT = "ACTION: COMMIT"
SUMMON = "ACTION: SUMMON REVIEWER"

FINDINGS_YAML = (
    "findings:\n"
    "  - category: correctness\n"
    "    severity: high\n"
    "    summary: 建議補上缺貨數量防護的測試\n"
    "    evidence:\n"
    "      - {path: src/inventory.py, line: 15}\n"
)


def patch_reply(*, preamble: str | None = None, claim: str | None = None) -> str:
    lines = []
    if preamble:
        lines.append(preamble)
    lines += ["ACTION: PATCH", "TARGET_ISSUES: MAIN-1"]
    if claim:
        lines.append(f"CLAIM: {claim}")
    lines.append("PATCH:")
    return "\n".join(lines) + "\n" + REFERENCE_DIFF


# ---------------------------------------------------------------------------
# fakes（不執行任何 candidate code、不打網路）
# ---------------------------------------------------------------------------


class FakeSuite:
    """公開 probe 套件 fake：依呼叫次序回放 scripted 結果；耗盡沿用最後一組。"""

    def __init__(self, results: list[dict[str, str]]) -> None:
        self._results = results
        self.calls = 0

    @property
    def probe_ids(self) -> tuple[str, ...]:
        return tuple(self._results[0])

    def run(self, workspace, subset=None) -> ProbeResults:
        statuses = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        if subset is not None:
            wanted = set(subset)
            statuses = {k: v for k, v in statuses.items() if k in wanted}
        return ProbeResults(
            {pid: make_outcome(status) for pid, status in statuses.items()}
        )


def make_power() -> PowerReport:
    breakdown = MaintainabilityReport(
        diff_size=4.0,
        scope=3,
        lint=3,
        total=10.0,
        production_loc=10,
        scope_hard_files=(),
        scope_soft_loc=0,
        lint_new_diagnostics=0,
    )
    return PowerReport(
        functional=60,
        robustness=15.0,
        compatibility=10,
        maintainability=10.0,
        runtime_efficiency=5.0,
        total=100.0,
        maintainability_breakdown=breakdown,
        perf_judgments=(),
    )


class FakeEvaluate:
    """終局 evaluator fake：記錄呼叫並回合成 FinalEvaluation。"""

    def __init__(self, critical: bool = True) -> None:
        self.critical = critical
        self.calls: list[str] = []

    def __call__(self, final_diff: str) -> FinalEvaluation:
        self.calls.append(final_diff)
        status = "passed" if self.critical else "failed"
        return FinalEvaluation(
            probe_outcomes=ProbeResults({HIDDEN: make_outcome(status)}),
            power=make_power(),
            gates=GateResult(
                critical_pass=self.critical, power_cap=None, run_invalid=False
            ),
        )


class RecordingAdapter(ScriptedAdapter):
    """作者 adapter：回放 replies 並記錄每次收到的 messages。"""

    def __init__(self, replies) -> None:
        super().__init__(replies)
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict]) -> AdapterResponse:
        self.calls.append([dict(m) for m in messages])
        return super().complete(messages)


class FakeReviewerAdapter(ModelAdapter):
    """reviewer adapter fake：固定回覆、記錄輸入 messages。"""

    usage_provider = "openai"

    def __init__(self, reply: str = FINDINGS_YAML) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    def complete(self, messages: list[dict]) -> AdapterResponse:
        self.calls.append([dict(m) for m in messages])
        return AdapterResponse(
            text=self.reply,
            usage_raw={"prompt_tokens": 10, "completion_tokens": 5},
            wall_ms=0,
        )


class Env:
    """一場 run 的測試佈線（真 Workspace＋fake 執行面）。"""

    def __init__(
        self,
        tmp_path: Path,
        *,
        suite_results: list[dict[str, str]] | None = None,
        critical: bool = True,
        reviewer: FakeReviewerAdapter | None = None,
        max_turns: int | None = None,
        spectator=None,
    ) -> None:
        frozen = materialize_repo(FIXTURE, tmp_path / "worktree")
        self.workspace = Workspace(
            frozen=frozen, encounter_dir=FIXTURE, shadow_dir=tmp_path / "shadow"
        )
        self.store = RunStore.create(
            {
                "run_id": "loop-test",
                "frozen_sha": frozen.sha,
                "pricing_hash": "NA",
                "harness_prompt_version": HARNESS_PROMPT_VERSION,
                "schedule_ref": "NA",
                "encounter_dir": str(FIXTURE),
            },
            tmp_path / "runs",
        )
        self.suite = FakeSuite(suite_results or [BASE])
        self.evaluate = FakeEvaluate(critical=critical)
        self.reviewer = reviewer
        self.config = RunConfig(
            workspace=self.workspace,
            probe_suite=self.suite,
            evaluate=self.evaluate,
            reviewer_adapter=reviewer,
            max_turns=max_turns,
            spectator=spectator,
        )

    def run(self, adapter, loadout: Loadout = SOLO):
        return run_encounter(CARD, adapter, loadout, self.config, self.store)

    def turn_events(self) -> list[dict]:
        return [e for e in self.store.load_events() if e["type"] == "turn"]


# ---------------------------------------------------------------------------
# loop 語意
# ---------------------------------------------------------------------------


class TestLiveSpectator:
    def test_spectator_called_once_per_event(self, tmp_path) -> None:
        """spectator（run --live）每 append 一個 event 被推送一次：
        baseline + 每個 author turn；純視圖不改變評分。"""
        seen: list[dict] = []
        env = Env(
            tmp_path,
            suite_results=[BASE, GREEN],
            critical=True,
            spectator=seen.append,
        )
        result = env.run(ScriptedAdapter([LOOK, COMMIT]))

        types = [e["type"] for e in seen]
        assert types[0] == "baseline"
        assert types.count("turn") == result.turns_used
        # 推送的 event 與封存 events 一致（同一資料面、純視圖）
        stored = [e for e in env.store.load_events() if e["type"] in ("baseline", "turn")]
        assert [e["type"] for e in seen] == [e["type"] for e in stored]

    def test_no_spectator_is_noop(self, tmp_path) -> None:
        env = Env(tmp_path, suite_results=[BASE, GREEN], critical=True)  # spectator=None
        result = env.run(ScriptedAdapter([LOOK, COMMIT]))
        assert result.end_reason == "commit"


class TestProtocolFailure:
    def test_three_garbage_replies_end_failed_protocol(self, tmp_path) -> None:
        # 終局全綠＋critical 綠：只有 failed:protocol 這一條會把 Clear 壓成 0
        env = Env(tmp_path, suite_results=[BASE, GREEN], critical=True)
        result = env.run(ScriptedAdapter([GARBAGE, GARBAGE, GARBAGE]))

        assert result.end_reason == "failed:protocol"
        assert result.protocol_failed is True
        # evaluator 仍執行（終局一律全套，§5.2）
        assert env.evaluate.calls
        assert result.clear == 0
        assert len(env.turn_events()) == 3

    def test_streak_resets_on_legal_action(self, tmp_path) -> None:
        env = Env(tmp_path, suite_results=[BASE], critical=False, max_turns=5)
        result = env.run(
            ScriptedAdapter([GARBAGE, GARBAGE, LOOK, GARBAGE, GARBAGE])
        )
        # 亂文×2 → LOOK 重置 → 亂文×2：未達連續 3 次，max_turns 終局
        assert result.end_reason == "max_turns"
        assert result.protocol_failed is False


class TestMaxTurns:
    def test_max_turns_exhausted_forces_endgame(self, tmp_path) -> None:
        env = Env(tmp_path, critical=False, max_turns=2)
        # replies 恰好 2 則：loop 若多要一回合，ScriptedAdapter 會 raise
        result = env.run(ScriptedAdapter([LOOK, LOOK]))
        assert result.end_reason == "max_turns"
        assert result.turns_used == 2
        assert env.evaluate.calls  # 強制終局仍跑 evaluator


class TestCommitWithoutRunTest:
    def test_engine_runs_full_suite_for_clear(self, tmp_path) -> None:
        env = Env(tmp_path, suite_results=[BASE, GREEN, GREEN], critical=True)
        result = env.run(ScriptedAdapter([patch_reply(), COMMIT]))

        assert result.end_reason == "commit"
        # baseline + PATCH 後自動全套 + 終局全套 = 3 次（F3：不靠 agent RUN_TEST）
        assert env.suite.calls == 3
        assert result.result["main_public_green"] is True
        assert result.clear == 1


class TestReviewerSubcall:
    def _run(self, tmp_path):
        reviewer = FakeReviewerAdapter()
        env = Env(
            tmp_path,
            suite_results=[BASE, GREEN, GREEN],
            critical=True,
            reviewer=reviewer,
        )
        author = RecordingAdapter(
            [patch_reply(preamble="AUTHOR-SECRET-PREAMBLE"), SUMMON, COMMIT]
        )
        result = env.run(author, loadout=Loadout.from_string("P0T0R1"))
        return env, author, reviewer, result

    def test_subcall_does_not_consume_turn(self, tmp_path) -> None:
        env, _author, reviewer, result = self._run(tmp_path)

        # PATCH／SUMMON／COMMIT 各一 turn；subcall 不另耗 turn（F9）
        assert result.turns_used == 3
        assert len(env.turn_events()) == 3
        assert len(reviewer.calls) == 1

        # reviewer ledger entry 嵌在 SUMMON 所屬 turn（turn 2）、role=reviewer
        roles = [(e.turn, e.role) for e in result.ledger_entries]
        assert roles.count((2, "reviewer")) == 1
        assert [r for r in roles if r[1] == "author"] == [
            (1, "author"), (2, "author"), (3, "author")
        ]

        # findings 只落盤與 render：不生成 queue item（F10）
        summon_event = env.turn_events()[1]
        assert summon_event["queue"]["b_t"] == 0
        artifact = env.store.run_dir / "artifacts" / "review_1.yaml"
        assert artifact.is_file()
        data = yaml.safe_load(artifact.read_text(encoding="utf-8"))
        assert data["valid"] is True

        # R1 gate 滿足 → COMMIT 合法收場
        assert result.end_reason == "commit"
        assert result.clear == 1

    def test_findings_rendered_to_author_same_turn(self, tmp_path) -> None:
        _env, author, _reviewer, _result = self._run(tmp_path)
        # F9：findings 在 SUMMON turn 結束進 render → 下一次作者呼叫可見
        third_call = "\n".join(str(m.get("content", "")) for m in author.calls[2])
        assert "缺貨數量防護" in third_call

    def test_reviewer_input_isolation(self, tmp_path) -> None:
        _env, _author, reviewer, _result = self._run(tmp_path)
        joined = "\n".join(
            str(m.get("content", "")) for m in reviewer.calls[0]
        )
        # 不含作者 transcript（preamble 只存在於作者回覆）與 hidden 路徑
        assert "AUTHOR-SECRET-PREAMBLE" not in joined
        assert "hidden/" not in joined
        # 白名單成分在：cumulative diff 觸及的檔案
        assert "src/inventory.py" in joined


class TestHumanReviewerLoadout:
    """review finding：R1 人類對局必須看得到 reviewer schema（spec §5.4）。

    play_cli 不設 `reviewer_adapter` → reviewer subcall 重用同一
    `HumanAdapter`（loop `config.reviewer_adapter or self.adapter`）。
    `reviewer.system_rules` 是全 codebase 唯一陳述 review YAML schema 之處，
    必須輸出給人類，人類才可能產出 schema-valid review → R1 gate 滿足 →
    COMMIT 合法——與模型對局完全同構。
    """

    def _run_human(self, tmp_path):
        replies = iter([patch_reply(), SUMMON, FINDINGS_YAML, COMMIT])
        events: list[tuple[str, ...]] = []

        def input_fn() -> str:
            events.append(("input",))
            return next(replies)

        def output_fn(text: object) -> None:
            events.append(("output", str(text)))

        adapter = HumanAdapter(input_fn=input_fn, output_fn=output_fn)
        env = Env(tmp_path, suite_results=[BASE, GREEN, GREEN], critical=True)
        result = env.run(adapter, loadout=Loadout.from_string("P0T0R1"))
        return env, events, result

    def test_reviewer_system_rules_shown_before_review_input(self, tmp_path) -> None:
        _env, events, _result = self._run_human(tmp_path)
        rules = zh.text("reviewer.system_rules")
        input_indexes = [i for i, e in enumerate(events) if e[0] == "input"]
        rules_indexes = [
            i
            for i, e in enumerate(events)
            if e[0] == "output" and rules in e[1]
        ]
        assert rules_indexes, "reviewer system rules 必須輸出給人類"
        # 第三次輸入是 review 回覆（PATCH → SUMMON → review → COMMIT）：
        # schema 說明必須在它之前出現
        assert rules_indexes[0] < input_indexes[2]

    def test_human_r1_commit_legal(self, tmp_path) -> None:
        env, _events, result = self._run_human(tmp_path)
        # 人類在場內產出 schema-valid review → R1 gate 滿足 → COMMIT 合法收場
        assert result.end_reason == "commit"
        assert result.clear == 1
        artifact = env.store.run_dir / "artifacts" / "review_1.yaml"
        assert yaml.safe_load(artifact.read_text(encoding="utf-8"))["valid"] is True
        # reviewer subcall 的 ledger entry：role=reviewer、billed totals NA
        reviewer_entries = [e for e in result.ledger_entries if e.role == "reviewer"]
        assert len(reviewer_entries) == 1
        assert reviewer_entries[0].billed_input_total is None
        assert reviewer_entries[0].billed_output_total is None


class TestTurnBookkeeping:
    def test_each_turn_one_checkpoint_and_one_event(self, tmp_path) -> None:
        env = Env(tmp_path, suite_results=[BASE, GREEN, GREEN], critical=True)
        checkpoint_calls: list[int] = []
        original = env.workspace.checkpoint

        def counting_checkpoint() -> str:
            checkpoint_calls.append(1)
            return original()

        env.workspace.checkpoint = counting_checkpoint  # type: ignore[method-assign]
        result = env.run(ScriptedAdapter([patch_reply(), COMMIT]))

        events = env.turn_events()
        assert len(events) == result.turns_used == 2
        assert all(e["checkpoint"] for e in events)
        assert all("b_t" in e["queue"] for e in events)
        # baseline 一次 + 每 author turn 恰一次（plan invariant 5）
        assert len(checkpoint_calls) == result.turns_used + 1

        # ledger.jsonl 落盤：兩次作者呼叫兩筆
        ledger_path = env.store.run_dir / "ledger.jsonl"
        assert ledger_path.is_file()
        assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 2

    def test_result_yaml_written_with_run_fields(self, tmp_path) -> None:
        env = Env(tmp_path, suite_results=[BASE, GREEN, GREEN], critical=True)
        result = env.run(ScriptedAdapter([patch_reply(), COMMIT]))

        record = yaml.safe_load(
            (env.store.run_dir / "result.yaml").read_text(encoding="utf-8")
        )
        assert record["clear"] == 1
        assert record["end_reason"] == "commit"
        assert record["turns"] == 2
        assert record["loadout"] == "P0T0R0"
        assert record["economy"] == "NA"
        assert record["ledger"]["entries"] == len(result.ledger_entries) == 2
