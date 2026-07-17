"""Task 23 RED：`patchmud watch` 逐回合 zh-TW 戰報 viewer（spec §5.4）。

對 e2e run（真 Workspace＋fake 執行面）的封存 events 鎖定：
- `render_battle_report(events, result)` 輸出含「回合 N」、行動敘述、
  queue 變化（新增／解決 issue 的中文敘事）、flood 壓力、終局結算段。
- `render_turn(events, n)` 只輸出該回合；找不到回合 → `WatchError`。
- 觀戰是純視圖層（spec §5.4）：執行期間 `IsolationRunner` 零呼叫（spy）、
  run 目錄零寫入（檔案集合＋mtime 驗證）、不新增事件。
- 文案全部經 zh-TW render pack 查表：watch.py 原始碼不得出現硬編中文
  （與 tests/engine/test_render.py 同一 AST 掃描契約）。

unit tests 不啟真 namespace、不打真模型 API（plan invariant 3）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
import yaml

from patchmud.adapters.scripted import ScriptedAdapter
from patchmud.cli import main
from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.engine.loop import RunConfig, run_encounter
from patchmud.engine.prompts import HARNESS_PROMPT_VERSION
from patchmud.engine.strategy import SOLO
from patchmud.evaluator.evaluate import FinalEvaluation
from patchmud.evaluator.gates import GateResult
from patchmud.evaluator.power import MaintainabilityReport, PowerReport
from patchmud.sandbox.isolate import IsolationRunner
from patchmud.sandbox.probes import ProbeResults, smoke_probe_id
from patchmud.sandbox.workspace import Workspace
from patchmud.store import watch as watch_module
from patchmud.store.run_store import RunStore
from patchmud.store.watch import WatchError, render_battle_report, render_turn
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
#: PATCH 後：MAIN 轉綠（MAIN-1 解決）、starter 轉紅（REG-1 新增）——
#: 同一回合同時產生「解決」與「新增」的 queue 變化敘事素材。
MIXED = {MAIN: "passed", STARTER: "failed", SMOKE: "passed", COMPAT: "passed"}
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


def fake_evaluate(final_diff: str) -> FinalEvaluation:
    return FinalEvaluation(
        probe_outcomes=ProbeResults({HIDDEN: make_outcome("passed")}),
        power=make_power(),
        gates=GateResult(critical_pass=True, power_cap=None, run_invalid=False),
    )


# ---------------------------------------------------------------------------
# fixture：打完一場 e2e run，之後只讀封存
# ---------------------------------------------------------------------------


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    frozen = materialize_repo(FIXTURE, tmp_path / "worktree")
    workspace = Workspace(
        frozen=frozen, encounter_dir=FIXTURE, shadow_dir=tmp_path / "shadow"
    )
    store = RunStore.create(
        {
            "run_id": "watch-test",
            "frozen_sha": frozen.sha,
            "pricing_hash": "NA",
            "harness_prompt_version": HARNESS_PROMPT_VERSION,
            "schedule_ref": "NA",
            "encounter_dir": str(FIXTURE),
        },
        tmp_path / "runs",
    )
    config = RunConfig(
        workspace=workspace,
        probe_suite=FakeSuite([BASE, MIXED, GREEN]),
        evaluate=fake_evaluate,
    )
    result = run_encounter(
        CARD, ScriptedAdapter([PATCH_REPLY, COMMIT_REPLY]), SOLO, config, store
    )
    assert result.clear == 1
    assert result.end_reason == "commit"
    return store.run_dir


def _events(run_dir: Path) -> list[dict]:
    return RunStore.open(run_dir).load_events()


def _result(run_dir: Path) -> dict:
    return yaml.safe_load((run_dir / "result.yaml").read_text(encoding="utf-8"))


def _tree_snapshot(run_dir: Path) -> dict[str, tuple[int, int]]:
    """run 目錄全檔案的 (mtime_ns, size) 快照（零寫入驗證）。"""
    return {
        p.relative_to(run_dir).as_posix(): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(run_dir.rglob("*"))
        if p.is_file()
    }


# ---------------------------------------------------------------------------
# 全場戰報
# ---------------------------------------------------------------------------


class TestBattleReport:
    def test_turn_sections_actions_flood_and_final(self, run_dir: Path) -> None:
        out = render_battle_report(_events(run_dir), _result(run_dir))
        # 逐回合段落（「回合 N」）
        assert "回合 1" in out
        assert "回合 2" in out
        # 行動敘述：中文敘事＋英文命令關鍵字
        assert "行動" in out
        assert "PATCH" in out
        assert "COMMIT" in out
        # flood 壓力
        assert "洪水壓力" in out
        # 終局結算段（end_reason 為 artifact 英文、Clear 布林值）
        assert "終局" in out
        assert "commit" in out
        assert "Clear = 1" in out

    def test_queue_delta_zh_narrative(self, run_dir: Path) -> None:
        out = render_battle_report(_events(run_dir), _result(run_dir))
        # PATCH 回合：MAIN-1 解決、REG-1（starter 轉紅）新增——中文敘事
        assert "MAIN-1" in out
        assert "REG-1" in out
        assert "新增" in out
        assert "解決" in out


# ---------------------------------------------------------------------------
# --turn N：只輸出該回合
# ---------------------------------------------------------------------------


class TestRenderTurn:
    def test_renders_only_requested_turn(self, run_dir: Path) -> None:
        out = render_turn(_events(run_dir), 1)
        assert "回合 1" in out
        assert "回合 2" not in out
        assert "終局" not in out
        # 該回合的 queue 變化敘事仍在
        assert "MAIN-1" in out
        assert "REG-1" in out

    def test_missing_turn_fails_closed(self, run_dir: Path) -> None:
        with pytest.raises(WatchError):
            render_turn(_events(run_dir), 99)


# ---------------------------------------------------------------------------
# CLI：patchmud watch <run_dir> [--turn N]
# ---------------------------------------------------------------------------


class TestWatchCli:
    def test_full_report(self, run_dir: Path, capsys) -> None:
        assert main(["watch", str(run_dir)]) == 0
        out = capsys.readouterr().out
        assert "回合 1" in out
        assert "終局" in out

    def test_turn_flag_renders_single_turn(self, run_dir: Path, capsys) -> None:
        assert main(["watch", str(run_dir), "--turn", "1"]) == 0
        out = capsys.readouterr().out
        assert "回合 1" in out
        assert "回合 2" not in out


# ---------------------------------------------------------------------------
# 純視圖層：IsolationRunner 零呼叫、run 目錄零寫入
# ---------------------------------------------------------------------------


class TestPureViewLayer:
    def test_zero_isolation_calls_and_zero_writes(
        self, run_dir: Path, capsys, monkeypatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(
            IsolationRunner, "run", lambda self, *a, **k: calls.append("run")
        )
        monkeypatch.setattr(
            IsolationRunner,
            "capabilities",
            lambda self: calls.append("capabilities"),
        )
        before = _tree_snapshot(run_dir)

        assert main(["watch", str(run_dir)]) == 0
        assert main(["watch", str(run_dir), "--turn", "2"]) == 0
        capsys.readouterr()

        assert calls == []  # 不重新執行任何 probe（spy）
        assert _tree_snapshot(run_dir) == before  # 零寫入、零新檔（mtime）


# ---------------------------------------------------------------------------
# 文案單一來源：watch.py 不得硬編中文（render pack 查表）
# ---------------------------------------------------------------------------


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _literal_strings(module) -> list[str]:
    """收集模組內所有非 docstring 的 string literal（硬編文案偵測用）。"""
    tree = ast.parse(inspect.getsource(module))
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_ids.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_ids
    ]


class TestLiveSpectator:
    def test_feeds_baseline_then_turns_zh_tw(self, run_dir: Path) -> None:
        from patchmud.store.watch import LiveSpectator

        out: list[str] = []
        spec = LiveSpectator(out=out.append)
        for event in _events(run_dir):
            spec.feed(event)

        assert len(out) >= 2
        assert "基線" in out[0]  # 開場基線段
        joined = "\n".join(out)
        assert "回合 1" in joined
        # queue delta：PATCH 解決 MAIN → 「解決」敘事（相鄰 snapshot 差集推導）
        assert "解決" in joined

    def test_unknown_event_ignored(self) -> None:
        from patchmud.store.watch import LiveSpectator

        out: list[str] = []
        LiveSpectator(out=out.append).feed({"type": "note", "x": 1})
        assert out == []


class TestRenderPackSingleSource:
    def test_no_hardcoded_chinese_in_watch_module(self) -> None:
        offenders = [s for s in _literal_strings(watch_module) if _has_cjk(s)]
        assert not offenders, (
            f"watch.py 內有硬編中文字串 {offenders!r}；"
            "文案必須進 render_zh_tw.py 查表"
        )
