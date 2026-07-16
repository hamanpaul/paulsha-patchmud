"""Task 5：`evaluate_final` 獨立 checkout 語意與 mini_encounter 整合（spec §9.1）。

unit tests 以 fake runner 注入（不啟真 namespace）鎖定：
- evaluator 使用獨立 checkout（frozen + final diff），永不觸碰 agent worktree；
  hidden probes 只 overlay 進 checkout。
- probe_outcomes 覆蓋 rubric + critical 全部 probes。
- 無法套用的 final diff → `EvaluatorError`（fail-closed）。
- ruff 無法執行 → lint 0 分（fail-closed），評分照常完成。

integration（真 bwrap；無能力時 skip，plan Task 5 Step 4）：
- 手工正確 diff（reference.patch）→ critical 綠、Power ≥ 60。
- 手工破壞 API diff → compat gate cap → Power ≤ 50。
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.evaluator.evaluate import EvaluatorError, FinalEvaluation, evaluate_final
from patchmud.evaluator.gates import COMPAT_BREAK_CAP, CRITICAL_FAIL_UTILITY_CAP
from patchmud.sandbox.isolate import Execution, IsolationRunner

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"

HIDDEN_PROBE = "hidden/test_cr1_no_negative_stock.py"
COMPAT_PROBE = "tests/starter/test_inventory_basics.py"

XML_GREEN_3 = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="3" time="0.03">
        <testcase classname="t" name="a" time="0.001"/>
        <testcase classname="t" name="b" time="0.001"/>
        <testcase classname="t" name="c" time="0.001"/>
      </testsuite>
    </testsuites>
    """
)

XML_ONE_FAIL_3 = textwrap.dedent(
    """\
    <?xml version="1.0" encoding="utf-8"?>
    <testsuites>
      <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="3" time="0.03">
        <testcase classname="t" name="a" time="0.001"/>
        <testcase classname="t" name="b" time="0.001">
          <failure message="AssertionError: broken api">body</failure>
        </testcase>
        <testcase classname="t" name="c" time="0.001"/>
      </testsuite>
    </testsuites>
    """
)

RUFF_CLEAN = Execution(
    exit_code=0, stdout="[]", stderr="", wall_ms=5, cpu_ms=3, timed_out=False
)

#: pytest hook 將任何 `failed` case 竄改為 `passed`——hidden probe 偽綠的核心攻擊。
FORGE_HOOK = textwrap.dedent(
    '''

    import pytest


    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(item, call):
        outcome = yield
        rep = outcome.get_result()
        if rep.when == "call" and rep.outcome == "failed":
            rep.outcome = "passed"
    '''
)

_GIT_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _clone(frozen, dest: Path) -> Path:
    subprocess.run(
        ["git", "clone", "--quiet", str(frozen.path), str(dest)],
        env=_GIT_ENV,
        check=True,
        capture_output=True,
    )
    return dest


def _forge_conftest_diff(frozen, tmp_path: Path) -> str:
    """只竄改 root `conftest.py`（注入 forge hook），SUT 保持未修。"""
    clone = _clone(frozen, tmp_path / "forge")
    conftest = clone / "conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + FORGE_HOOK, encoding="utf-8")
    diff = subprocess.run(
        ["git", "-C", str(clone), "diff"],
        env=_GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "pytest_runtest_makereport" in diff
    return diff


def _harness_tamper_diff(frozen, tmp_path: Path) -> str:
    """竄改 root conftest.py + 新增 agent 控制的 harness 設定檔（pytest.ini、子目錄 conftest）。"""
    clone = _clone(frozen, tmp_path / "tamper")
    conftest = clone / "conftest.py"
    conftest.write_text(conftest.read_text(encoding="utf-8") + FORGE_HOOK, encoding="utf-8")
    (clone / "pytest.ini").write_text("[pytest]\naddopts = -p evil\n", encoding="utf-8")
    (clone / "src" / "conftest.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(clone), "add", "-A"],
        env=_GIT_ENV,
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(clone), "diff", "--cached"],
        env=_GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class FakeEvalRunner:
    """回放 canned pytest junitxml 與 ruff JSON；記錄所有呼叫。"""

    def __init__(
        self,
        pytest_results: dict[str, tuple[int, str]] | None = None,
        ruff_results: list[Execution] | None = None,
    ) -> None:
        self.pytest_results = pytest_results or {
            HIDDEN_PROBE: (0, XML_GREEN_3),
            COMPAT_PROBE: (0, XML_GREEN_3),
        }
        self.ruff_results = ruff_results if ruff_results is not None else []
        self.calls: list[list[str]] = []
        self.cwds: list[Path] = []

    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
        argv = list(argv)
        self.calls.append(argv)
        self.cwds.append(Path(cwd))

        report_rel = next(
            (a.split("=", 1)[1] for a in argv if a.startswith("--junitxml=")), None
        )
        if report_rel is None:  # ruff 呼叫
            if self.ruff_results:
                return self.ruff_results.pop(0)
            return RUFF_CLEAN

        target = next(t for t in self.pytest_results if t in argv)
        exit_code, xml = self.pytest_results[target]
        (Path(cwd) / report_rel).write_text(xml, encoding="utf-8")
        return Execution(
            exit_code=exit_code, stdout="", stderr="", wall_ms=7, cpu_ms=4, timed_out=False
        )


@pytest.fixture()
def card():
    return load_card(FIXTURE / "card.yaml")


@pytest.fixture()
def frozen(tmp_path: Path):
    return materialize_repo(FIXTURE, tmp_path / "agent_wt")


REFERENCE_DIFF = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")


def _evaluate(card, frozen, diff, runner, tmp_path: Path, **kwargs) -> FinalEvaluation:
    captured: dict[str, Path] = {}

    def factory(checkout: Path):
        captured["checkout"] = Path(checkout)
        return runner

    result = evaluate_final(
        card,
        frozen,
        diff,
        factory,
        encounter_dir=FIXTURE,
        checkout_dir=tmp_path / "eval_checkout",
        **kwargs,
    )
    result_checkout = captured["checkout"]
    assert result_checkout == tmp_path / "eval_checkout"
    return result


# ---------------------------------------------------------------------------
# unit：獨立 checkout 語意（fake runner）
# ---------------------------------------------------------------------------


class TestIndependentCheckout:
    def test_never_touches_agent_worktree(self, card, frozen, tmp_path):
        before = (frozen.path / "src" / "inventory.py").read_bytes()
        runner = FakeEvalRunner()
        _evaluate(card, frozen, REFERENCE_DIFF, runner, tmp_path)

        # agent worktree：檔案未變、hidden 資產永不進入
        assert (frozen.path / "src" / "inventory.py").read_bytes() == before
        assert not (frozen.path / "hidden").exists()
        # 全部執行都發生在獨立 checkout
        checkout = tmp_path / "eval_checkout"
        assert all(cwd == checkout for cwd in runner.cwds)

    def test_checkout_has_diff_applied_and_hidden_overlaid(
        self, card, frozen, tmp_path
    ):
        _evaluate(card, frozen, REFERENCE_DIFF, FakeEvalRunner(), tmp_path)
        checkout = tmp_path / "eval_checkout"
        content = (checkout / "src" / "inventory.py").read_text(encoding="utf-8")
        assert "raise KeyError(name)" in content  # final diff 已套
        assert (checkout / HIDDEN_PROBE).read_bytes() == (
            FIXTURE / HIDDEN_PROBE
        ).read_bytes()

    def test_probe_outcomes_cover_rubric_and_critical(self, card, frozen, tmp_path):
        result = _evaluate(card, frozen, REFERENCE_DIFF, FakeEvalRunner(), tmp_path)
        assert set(result.probe_outcomes) == {HIDDEN_PROBE, COMPAT_PROBE}

    def test_unappliable_diff_fail_closed(self, card, frozen, tmp_path):
        with pytest.raises(EvaluatorError):
            _evaluate(
                card, frozen, "not a diff at all\n", FakeEvalRunner(), tmp_path
            )


# ---------------------------------------------------------------------------
# unit：分數與 gates wiring（fake runner）
# ---------------------------------------------------------------------------


class TestScoringWiring:
    def test_all_green_reference_diff_scores_full(self, card, frozen, tmp_path):
        result = _evaluate(card, frozen, REFERENCE_DIFF, FakeEvalRunner(), tmp_path)

        assert result.gates.critical_pass is True
        assert result.gates.power_cap is None
        assert result.power.functional == 60
        assert result.power.robustness == 15.0
        assert result.power.compatibility == 10
        # reference diff：L=7 ≤ hi、無 scope 越界、ruff 零新增 → 滿分 10
        assert result.power.maintainability == 10.0
        # fake wall_ms=7 ≤ 3.0×500 → 5；封存判定同步輸出
        assert result.power.runtime_efficiency == 5.0
        assert result.power.perf_judgments[0].within_budget is True
        assert result.power.total == 100.0

    def test_empty_diff_evaluates_baseline(self, card, frozen, tmp_path):
        runner = FakeEvalRunner(
            pytest_results={
                HIDDEN_PROBE: (1, XML_ONE_FAIL_3),
                COMPAT_PROBE: (0, XML_GREEN_3),
            }
        )
        result = _evaluate(card, frozen, "", runner, tmp_path)
        assert result.gates.critical_pass is False
        assert result.power.functional == 0
        # 空 diff：不呼叫 ruff（零 changed files → 零新增 diagnostics）
        assert result.power.maintainability == 10.0
        ruff_calls = [c for c in runner.calls if not any("--junitxml=" in a for a in c)]
        assert ruff_calls == []

    def test_compat_red_caps_power_50(self, card, frozen, tmp_path):
        runner = FakeEvalRunner(
            pytest_results={
                HIDDEN_PROBE: (0, XML_GREEN_3),
                COMPAT_PROBE: (1, XML_ONE_FAIL_3),
            }
        )
        result = _evaluate(card, frozen, REFERENCE_DIFF, runner, tmp_path)
        assert result.gates.power_cap == COMPAT_BREAK_CAP
        assert result.power.total == 50.0

    def test_ruff_unavailable_fail_closed_lint_0(self, card, frozen, tmp_path):
        broken_ruff = Execution(
            exit_code=127,
            stdout="",
            stderr="ruff: command not found",
            wall_ms=1,
            cpu_ms=1,
            timed_out=False,
        )
        runner = FakeEvalRunner(ruff_results=[broken_ruff, broken_ruff])
        result = _evaluate(card, frozen, REFERENCE_DIFF, runner, tmp_path)
        breakdown = result.power.maintainability_breakdown
        assert breakdown.lint == 0
        assert breakdown.lint_new_diagnostics is None
        assert result.power.maintainability == 7.0  # 4 + 3 + 0

    def test_new_ruff_diagnostics_lose_lint_points(self, card, frozen, tmp_path):
        diag = json.dumps(
            [{"filename": "src/inventory.py", "code": "F841", "message": "unused"}]
        )
        with_diag = Execution(
            exit_code=1, stdout=diag, stderr="", wall_ms=5, cpu_ms=3, timed_out=False
        )
        runner = FakeEvalRunner(ruff_results=[RUFF_CLEAN, with_diag])
        result = _evaluate(card, frozen, REFERENCE_DIFF, runner, tmp_path)
        breakdown = result.power.maintainability_breakdown
        assert breakdown.lint_new_diagnostics == 1
        assert breakdown.lint == 0


# ---------------------------------------------------------------------------
# unit：harness 竄改中和（fake runner，確定性；無需真 namespace）
# ---------------------------------------------------------------------------


class TestHarnessTamperNeutralized:
    """evaluator 在跑 hidden/compat probes 前，必以 deck 原始 bytes 中和 harness 設定檔。

    agent 的 final diff 可觸及 checkout 內任何非保護區檔案（root conftest.py、
    pytest.ini、子目錄 conftest…）；這些都是 pytest 自動載入的 harness，能偽造
    probe 判定。evaluator 必須在探測前還原 deck 原始 harness、移除 agent 新增者。
    """

    def test_tampered_conftest_restored_before_probes(self, card, frozen, tmp_path):
        diff = _harness_tamper_diff(frozen, tmp_path)
        deck_conftest = (FIXTURE / "repo" / "conftest.py").read_bytes()
        seen: list[bytes | None] = []

        class CapturingRunner(FakeEvalRunner):
            def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
                if any(a.startswith("--junitxml=") for a in argv):  # 只在 probe 執行時取樣
                    p = Path(cwd) / "conftest.py"
                    seen.append(p.read_bytes() if p.exists() else None)
                return super().run(argv, cwd, timeout_s)

        _evaluate(card, frozen, diff, CapturingRunner(), tmp_path)

        # 每次 probe 執行時，root conftest 都已還原為 deck 原始 bytes
        assert seen and all(s == deck_conftest for s in seen)

    def test_agent_added_harness_config_removed(self, card, frozen, tmp_path):
        diff = _harness_tamper_diff(frozen, tmp_path)

        removed: dict[str, bool] = {}

        class CheckingRunner(FakeEvalRunner):
            def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
                if any(a.startswith("--junitxml=") for a in argv):
                    removed["pytest.ini"] = not (Path(cwd) / "pytest.ini").exists()
                    removed["src/conftest.py"] = not (
                        Path(cwd) / "src" / "conftest.py"
                    ).exists()
                return super().run(argv, cwd, timeout_s)

        _evaluate(card, frozen, diff, CheckingRunner(), tmp_path)

        # agent 新增的 harness 設定檔在探測時已不存在（deck 未宣告 → 移除）
        assert removed == {"pytest.ini": True, "src/conftest.py": True}


# ---------------------------------------------------------------------------
# integration：mini_encounter（真 bwrap；無能力時 skip）
# ---------------------------------------------------------------------------

_BWRAP = Path("/usr/bin/bwrap")


def _sandbox_pytest_argv() -> tuple[str, ...]:
    site_dir = Path(pytest.__file__).resolve().parents[1]
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(site_dir)!r}); "
        "from pytest import console_main; sys.exit(console_main())"
    )
    return ("python3", "-B", "-c", bootstrap)


def _runner_factory(checkout: Path) -> IsolationRunner:
    site_dir = Path(pytest.__file__).resolve().parents[1]
    return IsolationRunner(checkout, [Path("/usr"), site_dir], bwrap_path=_BWRAP)


def _broken_api_diff(frozen, tmp_path: Path) -> str:
    """在暫存 clone 中把 public API `total()` 改名，產生破壞相容性的 diff。"""
    clone = tmp_path / "mutant"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }
    subprocess.run(
        ["git", "clone", "--quiet", str(frozen.path), str(clone)],
        env=env,
        check=True,
        capture_output=True,
    )
    target = clone / "src" / "inventory.py"
    target.write_text(
        target.read_text(encoding="utf-8").replace("def total(", "def total_count("),
        encoding="utf-8",
    )
    diff = subprocess.run(
        ["git", "-C", str(clone), "diff"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "def total_count(" in diff
    return diff


@pytest.fixture()
def real_capabilities():
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    probe = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


class TestMiniEncounterIntegration:
    def test_correct_diff_critical_green_power_ge_60(
        self, real_capabilities, card, frozen, tmp_path
    ):
        result = evaluate_final(
            card,
            frozen,
            REFERENCE_DIFF,
            _runner_factory,
            encounter_dir=FIXTURE,
            checkout_dir=tmp_path / "eval",
            pytest_argv=_sandbox_pytest_argv(),
        )
        assert result.gates.critical_pass is True
        assert result.gates.power_cap is None
        assert result.probe_outcomes[HIDDEN_PROBE].status == "passed"
        assert result.probe_outcomes[HIDDEN_PROBE].cases_total == 3
        assert result.power.total >= 60
        # perf 判定以量測當下值封存（供 L1 重算）
        assert result.power.perf_judgments[0].probe_id == HIDDEN_PROBE
        assert result.power.perf_judgments[0].wall_ms > 0

    def test_conftest_forge_cannot_fake_hidden_pass(
        self, real_capabilities, card, frozen, tmp_path
    ):
        """真 bwrap：SUT 未修、只用 forge conftest 想偽造 hidden 綠 → 必被中和。

        finding #1 的 PROOF 情境：`pytest_runtest_makereport` 把 failed 改 passed。
        修正後 evaluator 於探測前還原 deck 原始 conftest，hidden probe 真的執行、
        真的紅，critical_pass=False，Power 不被偽造抬高。
        """
        diff = _forge_conftest_diff(frozen, tmp_path)
        result = evaluate_final(
            card,
            frozen,
            diff,
            _runner_factory,
            encounter_dir=FIXTURE,
            checkout_dir=tmp_path / "eval",
            pytest_argv=_sandbox_pytest_argv(),
        )
        # hidden probe 是 assertion 級失敗（SUT 未修），forge hook 已失效
        assert result.probe_outcomes[HIDDEN_PROBE].status == "failed"
        assert result.gates.critical_pass is False
        assert result.power.functional == 0
        assert result.power.total <= CRITICAL_FAIL_UTILITY_CAP

    def test_broken_api_diff_power_le_50(
        self, real_capabilities, card, frozen, tmp_path
    ):
        diff = _broken_api_diff(frozen, tmp_path)
        result = evaluate_final(
            card,
            frozen,
            diff,
            _runner_factory,
            encounter_dir=FIXTURE,
            checkout_dir=tmp_path / "eval",
            pytest_argv=_sandbox_pytest_argv(),
        )
        assert result.gates.power_cap == COMPAT_BREAK_CAP
        assert result.gates.critical_pass is False
        assert result.power.total <= 50
        # compat probe 是 assertion 級失敗，非 error（三態不可混同）
        assert result.probe_outcomes[COMPAT_PROBE].status == "failed"
