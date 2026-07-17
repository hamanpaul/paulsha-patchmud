"""Task 16 RED：兩級 replay（spec §12.2、F2；plan Task 16 Step 1）。

- **L1 重算**：scripted e2e run（真 Workspace＋fake 執行面）落盤後
  `replay_l1(run_dir)` 必須 identical=True；手動竄改 result.yaml 一個分數
  → identical=False、CLI `patchmud replay <run_dir>` exit non-zero。
- **L2 重執行**：注入 fake reexecutor——perf-only probe（只出現在
  runtime_efficiency rubric）的差異落 `perf_deviations` 報告、不算 fail；
  functional probe 差異必須 fail（容忍帶只給 perf，spec §12.2）。
- fake evaluator 以真 rubric 純函數（`score_power`／`evaluate_gates`）在
  合成 outcomes 上評分，保證封存值與 L1 重算公式內部一致；unit tests 不啟
  真 namespace、不打真模型 API（plan invariant 3）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from patchmud.adapters.human import HumanAdapter
from patchmud.adapters.scripted import ScriptedAdapter
from patchmud.cli import main
from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.engine.loop import RunConfig, run_encounter
from patchmud.engine.prompts import HARNESS_PROMPT_VERSION
from patchmud.engine.strategy import SOLO
from patchmud.evaluator.evaluate import FinalEvaluation
from patchmud.evaluator.gates import apply_power_cap, evaluate_gates
from patchmud.evaluator.power import FileChange, score_power
from patchmud.sandbox.probes import ProbeOutcome, ProbeResults, smoke_probe_id
from patchmud.sandbox.workspace import Workspace
from patchmud.store.replay import ReexecutedProbes, replay_l1, replay_l2
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

#: 只出現在 runtime_efficiency rubric 的 perf-only probe（L2 容忍帶測試用）。
PERF_ONLY = "hidden/test_perf_only.py"

BASE = {MAIN: "failed", STARTER: "passed", SMOKE: "passed", COMPAT: "passed"}
GREEN = {**BASE, MAIN: "passed"}

PATCH_REPLY = (
    "ACTION: PATCH\n"
    "TARGET_ISSUES: MAIN-1\n"
    "PATCH:\n" + REFERENCE_DIFF
)
COMMIT_REPLY = "ACTION: COMMIT"


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


class ConsistentEvaluate:
    """終局 evaluator fake：以真 rubric 純函數在合成 outcomes 上評分。

    封存的 FinalEvaluation 與 L1 重算公式完全同源（`score_power`／
    `evaluate_gates`／`apply_power_cap`），identical 斷言才有意義。
    """

    def __init__(
        self,
        card,
        statuses: dict[str, str],
        *,
        file_changes: tuple[FileChange, ...],
        reference_timings_ms: dict[str, float],
    ) -> None:
        self.card = card
        self.statuses = statuses
        self.file_changes = file_changes
        self.reference_timings_ms = reference_timings_ms

    def __call__(self, final_diff: str) -> FinalEvaluation:
        outcomes = ProbeResults(
            {pid: make_outcome(status) for pid, status in self.statuses.items()}
        )
        gates = evaluate_gates(self.card, outcomes)
        power = apply_power_cap(
            score_power(
                self.card,
                outcomes,
                self.file_changes,
                lint_new_diagnostics=0,
                reference_timings_ms=self.reference_timings_ms,
            ),
            gates,
        )
        return FinalEvaluation(probe_outcomes=outcomes, power=power, gates=gates)


# ---------------------------------------------------------------------------
# 佈線 helpers
# ---------------------------------------------------------------------------


def make_run(
    tmp_path: Path,
    encounter_dir: Path = FIXTURE,
    *,
    evaluator_statuses: dict[str, str] | None = None,
    reference_timings_ms: dict[str, float] | None = None,
    adapter=None,
    human: bool = False,
) -> Path:
    """scripted「兩回合修好」run（turn 1 PATCH → turn 2 COMMIT）→ run 目錄。"""
    card = load_card(encounter_dir / "card.yaml")
    frozen = materialize_repo(encounter_dir, tmp_path / "worktree")
    workspace = Workspace(
        frozen=frozen, encounter_dir=encounter_dir, shadow_dir=tmp_path / "shadow"
    )
    store = RunStore.create(
        {
            "run_id": "replay-run",
            "frozen_sha": frozen.sha,
            "pricing_hash": "NA",
            "harness_prompt_version": HARNESS_PROMPT_VERSION,
            "schedule_ref": "NA",
            "encounter_dir": str(encounter_dir),
        },
        tmp_path / "runs",
    )
    evaluate = ConsistentEvaluate(
        card,
        evaluator_statuses or {HIDDEN: "passed", COMPAT: "passed"},
        file_changes=(FileChange(path="src/inventory.py", added=4, deleted=1),),
        reference_timings_ms=reference_timings_ms or {HIDDEN: 100.0},
    )
    config = RunConfig(
        workspace=workspace,
        probe_suite=FakeSuite([BASE, GREEN, GREEN]),
        evaluate=evaluate,
    )
    result = run_encounter(
        card,
        adapter or ScriptedAdapter([PATCH_REPLY, COMMIT_REPLY]),
        SOLO,
        config,
        store,
        human=human,
    )
    assert result.clear == 1  # 前提：兩回合修好
    return store.run_dir


def make_human_run(tmp_path: Path) -> Path:
    """human 對局版 make_run：`HumanAdapter`＋scripted input_fn（Task 22）。"""
    replies = iter([PATCH_REPLY, COMMIT_REPLY])
    adapter = HumanAdapter(
        input_fn=lambda: next(replies), output_fn=lambda _text: None
    )
    return make_run(tmp_path, adapter=adapter, human=True)


def tamper(run_dir: Path, mutate) -> None:
    """手動竄改封存 result.yaml（模擬封存被改動）。"""
    path = run_dir / "result.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(
        yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )


def make_perf_encounter(tmp_path: Path) -> Path:
    """mini_encounter 變體：runtime_efficiency rubric 只綁 perf-only probe。"""
    enc = tmp_path / "encounter"
    shutil.copytree(FIXTURE, enc)
    data = yaml.safe_load((enc / "card.yaml").read_text(encoding="utf-8"))
    data["power_rubric"]["runtime_efficiency"]["probes"] = [PERF_ONLY]
    (enc / "card.yaml").write_text(
        yaml.safe_dump(data, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    (enc / PERF_ONLY).write_text(
        "def test_perf_only():\n    assert True\n", encoding="utf-8"
    )
    return enc


def outcome_from_dict(data: dict) -> ProbeOutcome:
    return ProbeOutcome(
        status=data["status"],
        cases_total=data["cases_total"],
        cases_passed=data["cases_passed"],
        failure_fingerprints=tuple(data["failure_fingerprints"]),
        wall_ms=data["wall_ms"],
        cpu_ms=data["cpu_ms"],
    )


def reexecutor_from_archive(run_dir: Path, *, mutate_evaluator=None):
    """fake reexecutor：以封存 outcomes 回放「pinned 重執行」，可注入變異。"""
    result = yaml.safe_load((run_dir / "result.yaml").read_text(encoding="utf-8"))
    public = {
        pid: outcome_from_dict(d) for pid, d in result["probes"]["public"].items()
    }
    evaluator = {
        pid: outcome_from_dict(d) for pid, d in result["probes"]["evaluator"].items()
    }
    if mutate_evaluator is not None:
        mutate_evaluator(evaluator)

    def _run(_run_dir: Path) -> ReexecutedProbes:
        return ReexecutedProbes(public=public, evaluator=evaluator)

    return _run


# ---------------------------------------------------------------------------
# L1：位元一致重算（不執行 probe）
# ---------------------------------------------------------------------------


class TestReplayL1:
    def test_scripted_run_replays_identical(self, tmp_path) -> None:
        run_dir = make_run(tmp_path)
        report = replay_l1(run_dir)
        assert report.identical is True
        assert report.diffs == ()

    def test_tampered_power_score_detected(self, tmp_path) -> None:
        run_dir = make_run(tmp_path)
        tamper(run_dir, lambda d: d["power"].__setitem__("functional", 59))
        report = replay_l1(run_dir)
        assert report.identical is False
        assert any(d.field == "power.functional" for d in report.diffs)

    def test_tampered_clear_detected(self, tmp_path) -> None:
        run_dir = make_run(tmp_path)
        tamper(run_dir, lambda d: d.__setitem__("clear", 0))
        report = replay_l1(run_dir)
        assert report.identical is False
        assert any(d.field == "clear" for d in report.diffs)


class TestReplayHumanFlag:
    """human 標記必須重算驗證（review finding：L1 fail-open 修補）。

    `human` 由 ledger billed totals 重算（human ⟺ 全 NA，§5.4/§10.1）——
    model run 竄改標成 `human: true` 會被 report 靜默剔出 ranked（灌榜），
    replay 必須偵測（Task 16 稽核契約：竄改 result.yaml → exit 1）。
    """

    def test_genuine_human_run_replays_identical(self, tmp_path) -> None:
        run_dir = make_human_run(tmp_path)
        report = replay_l1(run_dir)
        assert report.identical is True
        assert report.diffs == ()
        # human run：ΔT 不存在 → flood 計量不適用
        assert report.flood is None

    def test_model_run_tampered_to_human_detected(self, tmp_path) -> None:
        # 灌榜方向：最差 model run 標成 human → 剔出 ranked 而 replay 仍
        # 認證位元一致。必須以 ledger billed totals 重算戳破。
        run_dir = make_run(tmp_path)
        tamper(run_dir, lambda d: d.__setitem__("human", True))
        report = replay_l1(run_dir)
        assert report.identical is False
        assert any(d.field == "human" for d in report.diffs)

    def test_human_run_tampered_to_model_detected(self, tmp_path) -> None:
        # 反向竄改：human run 改標 model → 同樣以重算值戳破（fail-closed）
        run_dir = make_human_run(tmp_path)
        tamper(run_dir, lambda d: d.__setitem__("human", False))
        report = replay_l1(run_dir)
        assert report.identical is False
        assert any(d.field == "human" for d in report.diffs)

    def test_cli_exit_nonzero_on_human_flag_tampered(self, tmp_path) -> None:
        run_dir = make_run(tmp_path)
        tamper(run_dir, lambda d: d.__setitem__("human", True))
        assert main(["replay", str(run_dir)]) != 0


class TestReplayCli:
    def test_exit_zero_on_identical(self, tmp_path) -> None:
        run_dir = make_run(tmp_path)
        assert main(["replay", str(run_dir)]) == 0

    def test_exit_nonzero_on_tampered_result(self, tmp_path) -> None:
        run_dir = make_run(tmp_path)
        tamper(run_dir, lambda d: d["power"].__setitem__("functional", 59))
        assert main(["replay", str(run_dir)]) != 0


# ---------------------------------------------------------------------------
# L2：pinned 重執行——functional/compat/robustness 必須相等、perf 容忍帶
# ---------------------------------------------------------------------------


class TestReplayL2:
    def _perf_run(self, tmp_path) -> Path:
        enc = make_perf_encounter(tmp_path)
        return make_run(
            tmp_path,
            enc,
            evaluator_statuses={
                HIDDEN: "passed",
                COMPAT: "passed",
                PERF_ONLY: "passed",
            },
            reference_timings_ms={PERF_ONLY: 100.0},
        )

    def test_equal_reexecution_is_identical(self, tmp_path) -> None:
        run_dir = self._perf_run(tmp_path)
        report = replay_l2(run_dir, reexecutor_from_archive(run_dir))
        assert report.identical is True
        assert report.diffs == ()
        assert report.perf_deviations == ()

    def test_perf_difference_tolerated_but_reported(self, tmp_path) -> None:
        run_dir = self._perf_run(tmp_path)
        runner = reexecutor_from_archive(
            run_dir,
            mutate_evaluator=lambda ev: ev.__setitem__(
                PERF_ONLY, make_outcome("failed", wall_ms=2600)
            ),
        )
        report = replay_l2(run_dir, runner)
        # perf probe 差異不 fail（timing 本質非確定），但必須落 report
        assert report.identical is True
        assert report.diffs == ()
        assert [dev.probe_id for dev in report.perf_deviations] == [PERF_ONLY]

    def test_functional_difference_fails(self, tmp_path) -> None:
        run_dir = self._perf_run(tmp_path)
        runner = reexecutor_from_archive(
            run_dir,
            mutate_evaluator=lambda ev: ev.__setitem__(HIDDEN, make_outcome("failed")),
        )
        report = replay_l2(run_dir, runner)
        assert report.identical is False
        assert any(HIDDEN in d.field for d in report.diffs)
        assert report.perf_deviations == ()

    def test_missing_probe_fails_closed(self, tmp_path) -> None:
        run_dir = self._perf_run(tmp_path)
        runner = reexecutor_from_archive(
            run_dir, mutate_evaluator=lambda ev: ev.pop(HIDDEN)
        )
        report = replay_l2(run_dir, runner)
        assert report.identical is False
