"""Task 4 RED：三態 probe runner 與 turn-0 baseline（spec §5.2、§7、§8.1）。

unit tests 以 fake runner 餵 pytest junitxml 輸出 fixture，不啟真 namespace：
- assertion fail → `failed`＋fingerprint（異常類型＋斷言訊息首行）。
- collection/import error → `error`（永不等同 `failed`，plan invariant 1）。
- case 計數正確；`transitions` 相對前次結果產生正確 green_to_red。
- 任何執行前必呼叫 `restore_protected()`（spy 驗證，§7）。

integration（真 bwrap；無能力時 skip）：mini_encounter turn-0 baseline
——materialize 後 run 全套，MAIN probe 紅、starter 綠。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from patchmud.deck.loader import load_card
from patchmud.sandbox.isolate import Execution, IsolationRunner
from patchmud.sandbox.probes import (
    ProbeError,
    ProbeOutcome,
    ProbeResults,
    ProbeSuite,
    Transition,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"

MAIN_PROBE = "tests/public/test_remove_missing.py"
STARTER_PROBE = "tests/starter/"
COMPAT_PROBE = "tests/starter/test_inventory_basics.py"


# ---------------------------------------------------------------------------
# junitxml fixtures（pytest xunit2 形態）
# ---------------------------------------------------------------------------

XML_ALL_GREEN_3 = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="3" time="0.03">
        <testcase classname="tests.starter.test_inventory_basics" name="test_add_and_total" time="0.001"/>
        <testcase classname="tests.starter.test_inventory_basics" name="test_add_negative_rejected" time="0.001"/>
        <testcase classname="tests.starter.test_inventory_basics" name="test_remove_reduces_total" time="0.001"/>
      </testsuite>
    </testsuites>
    """
)

# assertion 失敗：message 多行，fingerprint 只取首行（異常類型＋斷言訊息首行）
XML_ONE_ASSERTION_FAIL_OF_3 = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="3" time="0.04">
        <testcase classname="tests.public.test_remove_missing" name="test_a" time="0.001"/>
        <testcase classname="tests.public.test_remove_missing" name="test_remove_missing_item_raises_keyerror" time="0.002">
          <failure message="AssertionError: assert inv.total() == 2&#10; +  where 5 = total()">def test_remove...
    full traceback body</failure>
        </testcase>
        <testcase classname="tests.public.test_remove_missing" name="test_b" time="0.001"/>
      </testsuite>
    </testsuites>
    """
)

# collection/import error：pytest 對整個模組收集失敗的 junitxml 形態
XML_COLLECTION_ERROR = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="pytest" errors="1" failures="0" skipped="0" tests="1" time="0.01">
        <testcase classname="" name="tests/public/test_remove_missing.py" time="0.0">
          <error message="collection failure">ImportError while importing test module
    ModuleNotFoundError: No module named 'inventory'</error>
        </testcase>
      </testsuite>
    </testsuites>
    """
)


# ---------------------------------------------------------------------------
# fakes：runner 與 workspace（unit tests 不啟真 namespace）
# ---------------------------------------------------------------------------


class FakeRunner:
    """依 argv 內出現的 pytest target / smoke 命令回放 canned 結果。

    - pytest probe：把 canned junitxml 寫到 argv 內 `--junitxml=` 指定的路徑。
    - smoke probe：直接回 canned Execution。
    """

    def __init__(
        self,
        pytest_results: dict[str, tuple[int, str | None]] | None = None,
        smoke_results: dict[tuple[str, ...], Execution] | None = None,
        events: list | None = None,
        timed_out_targets: set[str] | None = None,
    ) -> None:
        self.pytest_results = pytest_results or {}
        self.smoke_results = smoke_results or {}
        self.events = events if events is not None else []
        self.timed_out_targets = timed_out_targets or set()
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
        argv = list(argv)
        self.calls.append(argv)
        self.events.append(("run", argv))

        report_rel = next(
            (a.split("=", 1)[1] for a in argv if a.startswith("--junitxml=")), None
        )
        if report_rel is None:
            return self.smoke_results[tuple(argv)]

        target = next(t for t in self.pytest_results if t in argv)
        if target in self.timed_out_targets:
            return Execution(
                exit_code=-9, stdout="", stderr="", wall_ms=999, cpu_ms=1, timed_out=True
            )
        exit_code, xml = self.pytest_results[target]
        if xml is not None:
            report_path = Path(report_rel)
            if not report_path.is_absolute():
                report_path = Path(cwd) / report_path
            report_path.write_text(xml, encoding="utf-8")
        return Execution(
            exit_code=exit_code, stdout="", stderr="", wall_ms=7, cpu_ms=4, timed_out=False
        )


class SpyWorkspace:
    """只提供 ProbeSuite 需要的兩個介面：worktree 與 restore_protected。"""

    def __init__(self, worktree: Path, events: list | None = None) -> None:
        self.worktree = worktree
        self.events = events if events is not None else []
        self.restore_calls = 0

    def restore_protected(self) -> None:
        self.restore_calls += 1
        self.events.append(("restore_protected",))


@pytest.fixture()
def card():
    return load_card(FIXTURE / "card.yaml")


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


def _smoke_argv(card) -> tuple[str, ...]:
    smoke = next(p.smoke for p in card.regression_probes if p.smoke is not None)
    return tuple(smoke)


def _smoke_id(card) -> str:
    return "smoke:" + " ".join(_smoke_argv(card))


def _suite(card, runner) -> ProbeSuite:
    return ProbeSuite.from_card(card, runner)


def _all_green_runner(card, events=None, **overrides) -> FakeRunner:
    pytest_results = {
        MAIN_PROBE: (0, XML_ALL_GREEN_3),
        STARTER_PROBE: (0, XML_ALL_GREEN_3),
        COMPAT_PROBE: (0, XML_ALL_GREEN_3),
    }
    pytest_results.update(overrides)
    return FakeRunner(
        pytest_results=pytest_results,
        smoke_results={
            _smoke_argv(card): Execution(
                exit_code=0, stdout="", stderr="", wall_ms=3, cpu_ms=2, timed_out=False
            )
        },
        events=events,
    )


# ---------------------------------------------------------------------------
# from_card：全套 public probes（requirements、regression、compat、starter）
# ---------------------------------------------------------------------------


class TestFromCard:
    def test_collects_all_public_probe_ids(self, card):
        suite = _suite(card, FakeRunner())
        assert list(suite.probe_ids) == [
            MAIN_PROBE,
            STARTER_PROBE,
            _smoke_id(card),
            COMPAT_PROBE,
        ]

    def test_hidden_probes_never_in_suite(self, card):
        suite = _suite(card, FakeRunner())
        assert not [p for p in suite.probe_ids if p.startswith("hidden/")]


# ---------------------------------------------------------------------------
# 三態判定：passed / failed / error 不可混同
# ---------------------------------------------------------------------------


class TestThreeStates:
    def test_assertion_failure_maps_to_failed_with_fingerprint(self, card, worktree):
        runner = _all_green_runner(card, **{MAIN_PROBE: (1, XML_ONE_ASSERTION_FAIL_OF_3)})
        results = _suite(card, runner).run(SpyWorkspace(worktree))

        outcome = results[MAIN_PROBE]
        assert isinstance(outcome, ProbeOutcome)
        assert outcome.status == "failed"
        assert outcome.cases_total == 3
        assert outcome.cases_passed == 2
        # fingerprint = 異常類型＋斷言訊息首行（多行 message 只取首行）
        assert list(outcome.failure_fingerprints) == [
            "AssertionError: assert inv.total() == 2"
        ]

    def test_collection_error_maps_to_error_not_failed(self, card, worktree):
        runner = _all_green_runner(card, **{MAIN_PROBE: (2, XML_COLLECTION_ERROR)})
        results = _suite(card, runner).run(SpyWorkspace(worktree))

        outcome = results[MAIN_PROBE]
        assert outcome.status == "error"
        assert outcome.status != "failed"  # error 永不等同 failed（invariant 1）
        assert outcome.cases_passed == 0

    def test_all_green_maps_to_passed_with_case_counts(self, card, worktree):
        runner = _all_green_runner(card)
        results = _suite(card, runner).run(SpyWorkspace(worktree))

        outcome = results[STARTER_PROBE]
        assert outcome.status == "passed"
        assert outcome.cases_total == 3
        assert outcome.cases_passed == 3
        assert list(outcome.failure_fingerprints) == []
        # 資源觀測值透傳自 Execution
        assert outcome.wall_ms == 7
        assert outcome.cpu_ms == 4

    def test_timeout_maps_to_error(self, card, worktree):
        runner = _all_green_runner(card, **{MAIN_PROBE: (1, None)})
        runner.timed_out_targets = {MAIN_PROBE}
        results = _suite(card, runner).run(SpyWorkspace(worktree))
        assert results[MAIN_PROBE].status == "error"

    def test_missing_report_maps_to_error(self, card, worktree):
        # pytest 沒跑起來（usage error 等）→ 無 junitxml → fail-closed error
        runner = _all_green_runner(card, **{MAIN_PROBE: (4, None)})
        results = _suite(card, runner).run(SpyWorkspace(worktree))
        assert results[MAIN_PROBE].status == "error"

    def test_smoke_exit0_passed_nonzero_failed(self, card, worktree):
        smoke_argv = _smoke_argv(card)
        runner = _all_green_runner(card)
        results = _suite(card, runner).run(SpyWorkspace(worktree))
        assert results[_smoke_id(card)].status == "passed"

        runner.smoke_results[smoke_argv] = Execution(
            exit_code=1,
            stdout="",
            stderr="Traceback (most recent call last):\nModuleNotFoundError: No module named 'inventory'",
            wall_ms=3,
            cpu_ms=2,
            timed_out=False,
        )
        results = _suite(card, runner).run(SpyWorkspace(worktree))
        outcome = results[_smoke_id(card)]
        assert outcome.status == "failed"
        assert outcome.failure_fingerprints[0] == (
            "ModuleNotFoundError: No module named 'inventory'"
        )


# ---------------------------------------------------------------------------
# transitions：相對前次結果的 green_to_red / red_to_green
# ---------------------------------------------------------------------------


class TestTransitions:
    def _results(self, card, worktree, main_xml: tuple[int, str]) -> ProbeResults:
        runner = _all_green_runner(card, **{MAIN_PROBE: main_xml})
        return _suite(card, runner).run(SpyWorkspace(worktree))

    def test_green_to_red_detected(self, card, worktree):
        prev = self._results(card, worktree, (0, XML_ALL_GREEN_3))
        cur = self._results(card, worktree, (1, XML_ONE_ASSERTION_FAIL_OF_3))
        assert cur.transitions(prev) == [Transition(MAIN_PROBE, "green_to_red")]

    def test_red_to_green_detected(self, card, worktree):
        prev = self._results(card, worktree, (1, XML_ONE_ASSERTION_FAIL_OF_3))
        cur = self._results(card, worktree, (0, XML_ALL_GREEN_3))
        assert cur.transitions(prev) == [Transition(MAIN_PROBE, "red_to_green")]

    def test_no_change_no_transitions(self, card, worktree):
        prev = self._results(card, worktree, (1, XML_ONE_ASSERTION_FAIL_OF_3))
        cur = self._results(card, worktree, (1, XML_ONE_ASSERTION_FAIL_OF_3))
        assert cur.transitions(prev) == []

    def test_green_to_error_counts_as_green_to_red(self, card, worktree):
        # 對 queue 而言 green=passed；error 也是「不再綠」（failed≠error 仍由 status 保存）
        prev = self._results(card, worktree, (0, XML_ALL_GREEN_3))
        cur = self._results(card, worktree, (2, XML_COLLECTION_ERROR))
        assert cur.transitions(prev) == [Transition(MAIN_PROBE, "green_to_red")]
        assert cur[MAIN_PROBE].status == "error"


# ---------------------------------------------------------------------------
# 保護區還原與 subset
# ---------------------------------------------------------------------------


class TestRunSemantics:
    def test_restore_protected_called_before_any_execution(self, card, worktree):
        events: list = []
        runner = _all_green_runner(card, events=events)
        ws = SpyWorkspace(worktree, events=events)
        _suite(card, runner).run(ws)

        assert ws.restore_calls >= 1
        first_run = events.index(("run", runner.calls[0]))
        assert ("restore_protected",) in events[:first_run]

    def test_subset_runs_only_requested(self, card, worktree):
        runner = _all_green_runner(card)
        results = _suite(card, runner).run(SpyWorkspace(worktree), subset=[MAIN_PROBE])
        assert list(results.probe_ids) == [MAIN_PROBE]
        assert len(runner.calls) == 1

    def test_unknown_subset_id_fail_closed(self, card, worktree):
        runner = _all_green_runner(card)
        with pytest.raises(ProbeError):
            _suite(card, runner).run(SpyWorkspace(worktree), subset=["no/such/probe.py"])

    def test_report_file_not_left_in_worktree(self, card, worktree):
        runner = _all_green_runner(card)
        _suite(card, runner).run(SpyWorkspace(worktree))
        leftovers = [p for p in worktree.rglob("*") if "patchmud" in p.name]
        assert leftovers == []


# ---------------------------------------------------------------------------
# integration：mini_encounter turn-0 baseline（真 bwrap；無能力時 skip）
# ---------------------------------------------------------------------------

_BWRAP = Path("/usr/bin/bwrap")


def _sandbox_pytest_argv() -> tuple[str, ...]:
    """沙箱內只 bind /usr 與 pytest 所在 site dir；以 bootstrap 注入 sys.path。"""
    site_dir = Path(pytest.__file__).resolve().parents[1]
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(site_dir)!r}); "
        "from pytest import console_main; sys.exit(console_main())"
    )
    return ("python3", "-B", "-c", bootstrap)


@pytest.fixture()
def real_stack(tmp_path: Path):
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    from patchmud.deck.materialize import materialize_repo
    from patchmud.sandbox.workspace import Workspace

    frozen = materialize_repo(FIXTURE, tmp_path / "wt")
    site_dir = Path(pytest.__file__).resolve().parents[1]
    runner = IsolationRunner(frozen.path, [Path("/usr"), site_dir], bwrap_path=_BWRAP)
    caps = runner.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")
    ws = Workspace(frozen=frozen, encounter_dir=FIXTURE, shadow_dir=tmp_path / "shadow")
    return ws, runner


class TestTurn0BaselineIntegration:
    def test_baseline_main_red_starter_green(self, real_stack, card):
        ws, runner = real_stack
        suite = ProbeSuite.from_card(card, runner, pytest_argv=_sandbox_pytest_argv())
        baseline = suite.run(ws)

        # MAIN probe 紅（bug 尚未修）：assertion 級失敗，非 error
        main = baseline[MAIN_PROBE]
        assert main.status == "failed"
        assert main.cases_total == 1 and main.cases_passed == 0
        assert len(main.failure_fingerprints) == 1

        # starter / compat / smoke 綠
        assert baseline[STARTER_PROBE].status == "passed"
        assert baseline[STARTER_PROBE].cases_total == 3
        assert baseline[COMPAT_PROBE].status == "passed"
        assert baseline[_smoke_id(card)].status == "passed"

    def test_reference_patch_turns_main_green(self, real_stack, card):
        ws, runner = real_stack
        suite = ProbeSuite.from_card(card, runner, pytest_argv=_sandbox_pytest_argv())
        baseline = suite.run(ws)

        diff = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")
        assert ws.apply_patch(diff, kind="production").applied

        after = suite.run(ws)
        assert after[MAIN_PROBE].status == "passed"
        assert Transition(MAIN_PROBE, "red_to_green") in after.transitions(baseline)
        assert [t for t in after.transitions(baseline) if t.change == "green_to_red"] == []
