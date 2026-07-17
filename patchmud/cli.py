"""patchmud CLI 進入點。

子命令（validate-deck / score-diff / run / pilot / replay / report）依
docs/superpowers/plans/2026-07-16-patchmud-mvp.md 逐 task 落地。

`score-diff`（Task 8，milestone A 收口）：給定 encounter + 手工 diff，離線
跑完整評分——全部 public probes（§5.2 終局語意）+ hidden evaluator（§9）——
落盤 `result.yaml`（PowerReport、gates、probe outcomes、Clear、Economy）並
產出私有封存（§12.1）。執行 candidate code 的唯一 seam 是 `IsolationRunner`；
namespace 能力不足一律 fail-closed 拒絕執行。

`run`（Task 13，milestone B 收口）：`patchmud run --encounter --model
--loadout`——真模型（或 scripted 劇本）打完一場 encounter：turn 0 baseline →
author turns（render → complete → parse → enforcer → 執行 → probe 排程 →
queue → checkpoint → event）→ 終局四觸發全套評分 → result.yaml。model spec：
`scripted:<file>`（回覆以 `-----` 行分隔；dry-run 用）、
`anthropic:<model>`（env `ANTHROPIC_API_KEY`）、
`openai:<model>[@<base_url>]`（env `OPENAI_API_KEY`，涵蓋地端 vllm/ollama）。

`replay`（Task 16，spec §12.2）：`patchmud replay <run_dir> [--l2]`——L1 位元
一致重算（不執行任何 probe；runtime_efficiency 引用封存 outcome），與封存
`result.yaml` 不一致 → exit non-zero；`--l2` 於 pinned 環境（deck 重物化 SHA
必須與封存一致）自 checkpoints shadow repo 重建 final diff 後重執行全部
probes——functional/compat/robustness 必須相等、perf-only 差異落 report 不算
fail——再走 L1。

`report`（Task 17，milestone C 收口；spec §10.3、§13、報告 §11.2）：
`patchmud report --runs <glob> [--out <dir>] [--pricing <snapshot>]
[--registered <dir>]`——只讀多場 run 的落盤封存（run.yaml／result.yaml／
events.jsonl／ledger.jsonl）＋ deck card，逐 (model, loadout) 群組輸出多榜
YAML/CSV：clear rate、cost per clear（run pin 的 pricing snapshot 計價；
未 pin／snapshot 不可得 → "NA" 不假 0）、tokens per clear／QATY／EuTB
（雙欄＋disclosure cohort，F17——排名委派 metrics 層 rank_efficiency；跨
cohort 整榜 non-ranking，rows 只發布 common-observable 描述性欄位且逐列
帶 non_ranking＋note 標註，CSV 檔案層即可與排名榜區分，§13；EuTB 缺
pre-registered 預算檔 → 標記 skipped 而非假值，§19.9）、Power／Control／
FTR（τ 未校準標記
tau_uncalibrated）。§11.2 其餘榜（MTY 曲線、one-shot、Pareto、composite）
待 pilot 資料齊備另行擴充。human run 不進 ranked 榜（列入 runs_skipped）。
"""

from __future__ import annotations

import argparse
import csv
import glob as globmod
import hashlib
import importlib.util
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from patchmud.adapters.anthropic import AnthropicAdapter
from patchmud.adapters.base import AdapterError, ModelAdapter
from patchmud.adapters.openai_compat import OpenAICompatAdapter
from patchmud.adapters.scripted import ScriptedAdapter
from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.deck.model import DeckError, IssueCard
from patchmud.engine.loop import RunConfig, build_agent_test_runner, run_encounter
from patchmud.engine.prompts import HARNESS_PROMPT_VERSION
from patchmud.engine.strategy import Loadout
from patchmud.evaluator.evaluate import EvaluatorError, FinalEvaluation, evaluate_final
from patchmud.evaluator.gates import compute_clear
from patchmud.evaluator.power import PowerReport
from patchmud.ledger.cost import compute_run_cost
from patchmud.ledger.pricing import PricingSnapshot
from patchmud.ledger.tokens import LedgerEntry, LedgerError
from patchmud.metrics.economy import EconomyError, RunSample, cost_per_clear
from patchmud.metrics.efficiency import (
    CohortMismatchError,
    EfficiencyError,
    EfficiencyResult,
    NotRegisteredError,
    eutb,
    load_eutb_budget,
    qaty,
    rank_efficiency,
    tokens_per_clear,
)
from patchmud.metrics.flood import FloodError, flood_metrics, load_flood_coeffs
from patchmud.sandbox.isolate import DEFAULT_BWRAP_PATH, IsolationRunner
from patchmud.sandbox.probes import (
    DEFAULT_PYTEST_ARGV,
    ProbeOutcome,
    ProbeResults,
    ProbeSuite,
)
from patchmud.sandbox.workspace import Workspace, WorkspaceError
from patchmud.store.replay import (
    ReexecutedProbes,
    ReplayError,
    load_ledger,
    replay_l1,
    replay_l2,
)
from patchmud.store.run_store import RunStore
from patchmud.store.schemas import RESULT_SCHEMA_VERSION, StoreError

__all__ = [
    "ReportError",
    "RunCliError",
    "ScoreDiffError",
    "ScoreDiffSummary",
    "build_report",
    "main",
    "score_diff",
]

#: 離線評分沒有 harness prompt / pricing / schedule；欄位以 NA 佔位（非 0，§10.1）。
_OFFLINE_NA = "NA"


class ScoreDiffError(Exception):
    """score-diff 操作性失敗（輸入缺漏、隔離能力不足、diff 遭拒等）。"""


class RunCliError(Exception):
    """run 子命令操作性失敗（model spec 非法、api key 缺席等）。"""


class ReportError(Exception):
    """report 子命令操作性失敗（glob 無 run、封存缺漏、欄位非法等）。"""


@dataclass(frozen=True)
class ScoreDiffSummary:
    """score-diff 完成後的摘要（CLI 輸出用）。"""

    run_dir: Path
    archive_path: Path
    clear: int
    critical_pass: bool
    power_total: float


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(
            "patchmud 0.0.0 — 子命令：score-diff / run / replay / report；其餘見 "
            "docs/superpowers/plans/2026-07-16-patchmud-mvp.md"
        )
        return 0
    if args[0] == "score-diff":
        return _cmd_score_diff(args[1:])
    if args[0] == "run":
        return _cmd_run(args[1:])
    if args[0] == "replay":
        return _cmd_replay(args[1:])
    if args[0] == "report":
        return _cmd_report(args[1:])
    print(f"patchmud: 子命令尚未實作：{args[0]}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# score-diff 子命令
# ---------------------------------------------------------------------------


def _cmd_score_diff(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud score-diff",
        description="離線評分：給定 encounter 與手工 diff，產出 result.yaml 與私有封存。",
    )
    parser.add_argument("--encounter", required=True, type=Path, help="encounter 目錄")
    parser.add_argument("--diff", required=True, type=Path, help="diff 檔（可為空檔 = baseline）")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"), help="run 目錄根")
    parser.add_argument("--run-id", default=None, help="run 識別字串（預設自動產生）")
    ns = parser.parse_args(argv)

    try:
        summary = score_diff(ns.encounter, ns.diff, ns.runs_root, run_id=ns.run_id)
    except (ScoreDiffError, DeckError, EvaluatorError, StoreError, WorkspaceError, LedgerError) as exc:
        print(f"score-diff 失敗：{exc}", file=sys.stderr)
        return 2

    print(f"run 目錄：{summary.run_dir}")
    print(f"私有封存：{summary.archive_path}")
    print(f"critical_pass={summary.critical_pass} clear={summary.clear} power_total={summary.power_total}")
    return 0


def score_diff(
    encounter_dir: Path,
    diff_path: Path,
    runs_root: Path,
    *,
    run_id: str | None = None,
    bwrap_path: str = DEFAULT_BWRAP_PATH,
) -> ScoreDiffSummary:
    """離線評分 walking skeleton（plan Task 8；spec §5.2、§9、§12.1）。

    流程：load card → materialize frozen repo → 套 diff（Workspace 嚴格模式）
    → 全部 public probes → hidden evaluator（獨立 checkout）→ Clear 唯一公式
    → result.yaml → archive_private。
    """
    encounter_dir = Path(encounter_dir).resolve()
    card = load_card(encounter_dir / "card.yaml")

    diff_path = Path(diff_path)
    if not diff_path.is_file():
        raise ScoreDiffError(f"diff 檔不存在：{diff_path}")
    diff_text = diff_path.read_text(encoding="utf-8")
    diff_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    toolchain = _toolchain_paths()
    pytest_argv = _sandbox_pytest_argv()
    ruff_argv = _ruff_argv(toolchain)
    _require_isolation(bwrap_path, toolchain)

    if run_id is None:
        run_id = f"score-diff-{card.issue_id}-{time.strftime('%Y%m%d%H%M%S')}"

    runs_root = Path(runs_root)
    with tempfile.TemporaryDirectory(prefix="patchmud-score-") as tmp:
        tmp_path = Path(tmp)
        frozen = materialize_repo(encounter_dir, tmp_path / "worktree")

        store = RunStore.create(
            {
                "run_id": run_id,
                "frozen_sha": frozen.sha,
                "pricing_hash": _OFFLINE_NA,
                "harness_prompt_version": _OFFLINE_NA,
                "schedule_ref": _OFFLINE_NA,
                "encounter_dir": str(encounter_dir),
            },
            runs_root,
        )

        # 套用手工 diff：走 Workspace 嚴格模式（保護區 / harness 設定檔拒收）
        workspace = Workspace(
            frozen=frozen, encounter_dir=encounter_dir, shadow_dir=tmp_path / "shadow"
        )
        if diff_text.strip():
            applied = workspace.apply_patch(diff_text, kind="production")
            if applied.rejected:
                raise ScoreDiffError(f"diff 遭拒：{applied.reason}")

        # 終局語意（§5.2）：全部 public probes ＋ hidden evaluator 全套
        public_runner = IsolationRunner(frozen.path, toolchain, bwrap_path=bwrap_path)
        suite = ProbeSuite.from_card(card, public_runner, pytest_argv=pytest_argv)
        public_results = suite.run(workspace)

        evaluation = evaluate_final(
            card,
            frozen,
            diff_text,
            lambda checkout: IsolationRunner(checkout, toolchain, bwrap_path=bwrap_path),
            encounter_dir=encounter_dir,
            pytest_argv=pytest_argv,
            ruff_argv=ruff_argv,
        )

        main_public_green = all(
            _is_green(public_results, req.probe) for req in card.public_requirements
        )
        # 離線評分不經回合協定，終局必非 failed:protocol
        clear = compute_clear(
            critical_pass=evaluation.gates.critical_pass,
            main_public_green=main_public_green,
            protocol_failed=False,
        )
        economy, economy_reason = _economy_na(card)

        store.append_event(
            {
                "type": "score_diff",
                "diff_sha256": diff_sha256,
                "public_probes": {
                    probe_id: outcome.status
                    for probe_id, outcome in public_results.items()
                },
            }
        )
        store.write_result(
            _build_result(
                run_id=run_id,
                diff_sha256=diff_sha256,
                clear=clear,
                main_public_green=main_public_green,
                evaluation=evaluation,
                public_results=public_results,
                economy=economy,
                economy_reason=economy_reason,
            )
        )

        archive_path = runs_root / f"{run_id}-private.tar"
        store.archive_private(archive_path)

    return ScoreDiffSummary(
        run_dir=store.run_dir,
        archive_path=archive_path,
        clear=clear,
        critical_pass=evaluation.gates.critical_pass,
        power_total=evaluation.power.total,
    )


# ---------------------------------------------------------------------------
# run 子命令（Task 13，milestone B 收口）
# ---------------------------------------------------------------------------


def _cmd_run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud run",
        description="回合制對局：模型（或 scripted 劇本）打完一場 encounter。",
    )
    parser.add_argument("--encounter", required=True, type=Path, help="encounter 目錄")
    parser.add_argument(
        "--model",
        required=True,
        help="model spec：scripted:<file> / anthropic:<model> / openai:<model>[@<base_url>]",
    )
    parser.add_argument("--loadout", required=True, help="forced loadout，如 P0T0R0")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"), help="run 目錄根")
    parser.add_argument("--run-id", default=None, help="run 識別字串（預設自動產生）")
    ns = parser.parse_args(argv)

    try:
        result = run_cli(
            ns.encounter, ns.model, ns.loadout, ns.runs_root, run_id=ns.run_id
        )
    except (
        RunCliError,
        ScoreDiffError,
        AdapterError,
        DeckError,
        EvaluatorError,
        StoreError,
        WorkspaceError,
        LedgerError,
        ValueError,
    ) as exc:
        print(f"run 失敗：{exc}", file=sys.stderr)
        return 2

    print(
        f"end_reason={result.end_reason} clear={result.clear} "
        f"turns={result.turns_used} power_total={result.evaluation.power.total}"
    )
    return 0


def run_cli(
    encounter_dir: Path,
    model_spec: str,
    loadout_spec: str,
    runs_root: Path,
    *,
    run_id: str | None = None,
    bwrap_path: str = DEFAULT_BWRAP_PATH,
):
    """`patchmud run` 串線：真佈線（IsolationRunner／ProbeSuite／evaluator）
    交給 `run_encounter`（spec §5、§6；plan Task 13）。"""
    encounter_dir = Path(encounter_dir).resolve()
    card = load_card(encounter_dir / "card.yaml")
    loadout = Loadout.from_string(loadout_spec)
    adapter = _build_adapter(model_spec)

    toolchain = _toolchain_paths()
    pytest_argv = _sandbox_pytest_argv()
    ruff_argv = _ruff_argv(toolchain)
    _require_isolation(bwrap_path, toolchain)

    if run_id is None:
        run_id = f"run-{card.issue_id}-{time.strftime('%Y%m%d%H%M%S')}"

    runs_root = Path(runs_root)
    with tempfile.TemporaryDirectory(prefix="patchmud-run-") as tmp:
        tmp_path = Path(tmp)
        frozen = materialize_repo(encounter_dir, tmp_path / "worktree")

        store = RunStore.create(
            {
                "run_id": run_id,
                "frozen_sha": frozen.sha,
                "pricing_hash": _OFFLINE_NA,
                "harness_prompt_version": HARNESS_PROMPT_VERSION,
                "schedule_ref": _OFFLINE_NA,
                "encounter_dir": str(encounter_dir),
                "loadout": loadout.name,
                "model": model_spec,
            },
            runs_root,
        )

        # checkpoints 落在 run 目錄（spec §3：shadow bare repo 供離線重播）
        workspace = Workspace(
            frozen=frozen,
            encounter_dir=encounter_dir,
            shadow_dir=store.run_dir / "checkpoints",
        )
        runner = IsolationRunner(frozen.path, toolchain, bwrap_path=bwrap_path)
        config = RunConfig(
            workspace=workspace,
            probe_suite=ProbeSuite.from_card(card, runner, pytest_argv=pytest_argv),
            evaluate=lambda final_diff: evaluate_final(
                card,
                frozen,
                final_diff,
                lambda checkout: IsolationRunner(
                    checkout, toolchain, bwrap_path=bwrap_path
                ),
                encounter_dir=encounter_dir,
                pytest_argv=pytest_argv,
                ruff_argv=ruff_argv,
            ),
            run_agent_tests=build_agent_test_runner(runner, pytest_argv=pytest_argv),
        )
        return run_encounter(card, adapter, loadout, config, store)


_SCRIPT_DELIMITER = "-----"


def _build_adapter(spec: str) -> ModelAdapter:
    """model spec → adapter；HTTP adapter 的 api key 一律取自 env（不進 CLI）。"""
    kind, _, rest = spec.partition(":")
    if kind == "scripted":
        script = Path(rest)
        if not rest or not script.is_file():
            raise RunCliError(f"scripted 劇本檔不存在：{rest!r}")
        replies = _split_script(script.read_text(encoding="utf-8"))
        if not replies:
            raise RunCliError(f"scripted 劇本檔沒有任何回覆：{script}")
        return ScriptedAdapter(replies)
    if kind == "anthropic":
        if not rest:
            raise RunCliError("anthropic spec 缺 model id：anthropic:<model>")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise RunCliError("env ANTHROPIC_API_KEY 未設定")
        return AnthropicAdapter(rest, api_key)
    if kind == "openai":
        if not rest:
            raise RunCliError("openai spec 缺 model id：openai:<model>[@<base_url>]")
        model, _, base_url = rest.partition("@")
        kwargs: dict = {}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAICompatAdapter(
            model, os.environ.get("OPENAI_API_KEY", ""), **kwargs
        )
    raise RunCliError(f"未知 model spec：{spec!r}")


def _split_script(text: str) -> list[str]:
    """scripted 劇本：回覆以獨立一行 `-----` 分隔（回覆內容逐 byte 保留）。"""
    replies: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == _SCRIPT_DELIMITER:
            replies.append("\n".join(current))
            current = []
        else:
            current.append(line)
    replies.append("\n".join(current))
    return [reply for reply in replies if reply.strip()]


# ---------------------------------------------------------------------------
# replay 子命令（Task 16，spec §12.2）
# ---------------------------------------------------------------------------


def _cmd_replay(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud replay",
        description=(
            "兩級 replay：預設 L1 位元一致重算（不執行 probe）；"
            "--l2 於 pinned 環境重執行全部 probes 後走 L1（spec §12.2）。"
        ),
    )
    parser.add_argument("run_dir", type=Path, help="run 目錄（runs/<run_id>）")
    parser.add_argument(
        "--l2",
        action="store_true",
        help="L2：pinned 環境重執行全部 probes（functional/compat/robustness 必須相等，perf 容忍帶）",
    )
    ns = parser.parse_args(argv)

    try:
        if ns.l2:
            report = replay_l2(ns.run_dir, _pinned_reexecute)
        else:
            report = replay_l1(ns.run_dir)
    except (
        ReplayError,
        ScoreDiffError,
        DeckError,
        EvaluatorError,
        StoreError,
        WorkspaceError,
        FloodError,
        LedgerError,
    ) as exc:
        print(f"replay 失敗：{exc}", file=sys.stderr)
        return 2

    print(
        f"replay {report.level}: identical={report.identical} "
        f"diffs={len(report.diffs)} perf_deviations={len(report.perf_deviations)}"
    )
    for diff in report.diffs:
        print(
            f"  diff {diff.field}: archived={diff.archived!r} "
            f"recomputed={diff.recomputed!r}"
        )
    for dev in report.perf_deviations:
        print(
            f"  perf {dev.section}/{dev.probe_id}: "
            f"status {dev.archived_status}->{dev.reexecuted_status} "
            f"wall_ms {dev.archived_wall_ms}->{dev.reexecuted_wall_ms}"
        )
    # L1 輸出與封存 result.yaml 不一致 → exit non-zero（spec §12.2）
    return 0 if report.identical else 1


def _pinned_reexecute(
    run_dir: Path, *, bwrap_path: str = DEFAULT_BWRAP_PATH
) -> ReexecutedProbes:
    """L2 真佈線：pinned 環境重執行全部 probes（spec §12.2）。

    deck 重物化的 frozen SHA 必須與 run.yaml 封存完全一致（deck 漂移 →
    拒絕）；final worktree 自 checkpoints shadow bare repo 的最末 checkpoint
    重建（`git fetch` 進 frozen clone 後 `git diff <frozen>..<checkpoint>`），
    diff 於原始 run 已逐回合過 workspace 路徑規則，此處不重新裁決、直接
    `git apply`；public suite 與 hidden evaluator 全部經 IsolationRunner
    重新執行。
    """
    run_dir = Path(run_dir)
    store = RunStore.open(run_dir)
    events = store.load_events()
    record = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
    encounter_dir = Path(record["encounter_dir"])
    card = load_card(encounter_dir / "card.yaml")

    checkpoints = [
        event["checkpoint"]
        for event in events
        if isinstance(event.get("checkpoint"), str) and event["checkpoint"]
    ]
    if not checkpoints:
        raise ReplayError("events 無 checkpoint，無法重建 final worktree（L2）")
    checkpoints_dir = run_dir / "checkpoints"
    if not checkpoints_dir.is_dir():
        raise ReplayError(
            f"run 目錄缺 checkpoints/ shadow repo，無法 L2 重執行：{run_dir}"
        )

    toolchain = _toolchain_paths()
    pytest_argv = _sandbox_pytest_argv()
    ruff_argv = _ruff_argv(toolchain)
    _require_isolation(bwrap_path, toolchain)

    with tempfile.TemporaryDirectory(prefix="patchmud-replay-") as tmp:
        tmp_path = Path(tmp)
        frozen = materialize_repo(encounter_dir, tmp_path / "worktree")
        if frozen.sha != record.get("frozen_sha"):
            raise ReplayError(
                "deck 重物化 SHA 與封存不符（deck 已漂移，pinned 重執行不成立）："
                f"{frozen.sha} != {record.get('frozen_sha')!r}"
            )
        final_diff = _checkpoint_diff(checkpoints_dir, frozen, checkpoints[-1])

        workspace = Workspace(
            frozen=frozen, encounter_dir=encounter_dir, shadow_dir=tmp_path / "shadow"
        )
        if final_diff.strip():
            _replay_apply(final_diff, workspace.worktree)

        runner = IsolationRunner(frozen.path, toolchain, bwrap_path=bwrap_path)
        suite = ProbeSuite.from_card(card, runner, pytest_argv=pytest_argv)
        public = suite.run(workspace)
        evaluation = evaluate_final(
            card,
            frozen,
            final_diff,
            lambda checkout: IsolationRunner(checkout, toolchain, bwrap_path=bwrap_path),
            encounter_dir=encounter_dir,
            pytest_argv=pytest_argv,
            ruff_argv=ruff_argv,
        )
        return ReexecutedProbes(
            public={probe_id: public[probe_id] for probe_id in public},
            evaluator={
                probe_id: evaluation.probe_outcomes[probe_id]
                for probe_id in evaluation.probe_outcomes
            },
        )


def _replay_git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def _checkpoint_diff(checkpoints_dir: Path, frozen, checkpoint_sha: str) -> str:
    """checkpoint shadow repo → final diff（fetch 進 frozen clone 後 diff）。"""
    fetch = subprocess.run(
        [
            "git",
            "-C",
            str(frozen.path),
            "fetch",
            "--quiet",
            str(checkpoints_dir),
            "refs/heads/checkpoints",
        ],
        env=_replay_git_env(),
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        raise ReplayError(f"checkpoints fetch 失敗：{fetch.stderr.strip()[:200]}")
    diff = subprocess.run(
        ["git", "-C", str(frozen.path), "diff", frozen.sha, checkpoint_sha],
        env=_replay_git_env(),
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        raise ReplayError(f"checkpoint diff 失敗：{diff.stderr.strip()[:200]}")
    return diff.stdout


def _replay_apply(diff: str, worktree: Path) -> None:
    res = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(worktree),
        input=diff,
        env=_replay_git_env(),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise ReplayError(f"final diff 重建後無法套用：{res.stderr.strip()[:200]}")


# ---------------------------------------------------------------------------
# report 子命令（Task 17，milestone C 收口；spec §10.3、§13、報告 §11.2）
# ---------------------------------------------------------------------------

REPORT_SCHEMA_VERSION = 1

#: pre-registered EuTB 預算檔的預設位置（§10.4；Task 19 calibrate 產出）。
DEFAULT_REGISTERED_DIR = Path("analysis/registered")
_EUTB_BUDGET_FILE = "eutb_budget.yaml"

#: 有列資料的榜各出一份 CSV；skipped 榜不出假 CSV。
_BOARD_ORDER = (
    "clear_rate",
    "cost_per_clear",
    "tokens_per_clear",
    "qaty",
    "eutb",
    "power",
    "control",
    "ftr",
)


class _SkipRun(Exception):
    """單場 run 不進 ranked 榜（列入 runs_skipped，不中止整份 report）。"""


@dataclass(frozen=True)
class _ReportRun:
    """單場 run 的 report 聚合視圖（只讀封存；invariant 4）。"""

    run_id: str
    model: str
    loadout: str
    clear: int
    power_total: float
    cost: Decimal | None
    cost_reason: str | None
    work_tokens: int | None
    observable_tokens: int
    control: float
    ftr: float
    tau_uncalibrated: bool
    flood_create_tokens: int
    flood_repair_tokens: int


def _cmd_report(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud report",
        description=(
            "多榜研究 report（報告 §11.2）：只讀多場 run 的落盤封存，"
            "輸出 YAML/CSV 到 --out 目錄。"
        ),
    )
    parser.add_argument("--runs", required=True, help="run 目錄 glob，如 'runs/*'")
    parser.add_argument("--out", type=Path, default=Path("report"), help="輸出目錄")
    parser.add_argument(
        "--pricing",
        type=Path,
        default=None,
        help="pricing snapshot 檔；只對 run.yaml pin 了相同 content hash 的 run 計價",
    )
    parser.add_argument(
        "--registered",
        type=Path,
        default=DEFAULT_REGISTERED_DIR,
        help="pre-registered 參數目錄（EuTB 預算檔 eutb_budget.yaml，§10.4）",
    )
    ns = parser.parse_args(argv)

    try:
        report = build_report(
            ns.runs, ns.out, pricing_path=ns.pricing, registered_dir=ns.registered
        )
    except (
        ReportError,
        DeckError,
        StoreError,
        ReplayError,
        FloodError,
        EconomyError,
        EfficiencyError,
        LedgerError,
    ) as exc:
        print(f"report 失敗：{exc}", file=sys.stderr)
        return 2

    print(f"report 輸出：{ns.out / 'report.yaml'}")
    print(
        f"runs_included={report['runs_included']} "
        f"runs_skipped={len(report['runs_skipped'])}"
    )
    return 0


def build_report(
    runs_glob: str,
    out_dir: Path,
    *,
    pricing_path: Path | None = None,
    registered_dir: Path = DEFAULT_REGISTERED_DIR,
) -> dict:
    """多場 run 封存 → 多榜 report（YAML + 每榜一份 CSV）。

    只讀 run 目錄封存與 deck card、絕不寫回 run 目錄（invariant 4）；
    聚合鍵為 (model, loadout)。回傳 report dict（同步落盤 report.yaml）。
    """
    snapshot = None
    if pricing_path is not None:
        snapshot = PricingSnapshot.load(pricing_path)

    runs: list[_ReportRun] = []
    skipped: list[dict] = []
    matches = sorted(globmod.glob(str(runs_glob)))
    if not matches:
        raise ReportError(f"--runs glob 無任何匹配：{runs_glob!r}")
    for match in matches:
        run_dir = Path(match)
        if not (run_dir / "run.yaml").is_file():
            # runs root 可能混有封存 tar 等非 run 目錄項目
            skipped.append({"run_id": run_dir.name, "reason": "非 run 目錄（缺 run.yaml）"})
            continue
        try:
            runs.append(_load_report_run(run_dir, snapshot))
        except _SkipRun as exc:
            skipped.append({"run_id": run_dir.name, "reason": str(exc)})
    if not runs:
        raise ReportError(f"glob 匹配 {len(matches)} 項但無任何可聚合 run：{runs_glob!r}")

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "runs_included": len(runs),
        "runs_skipped": skipped,
        "runs": [_run_row(run) for run in runs],
        "leaderboards": _build_leaderboards(runs, registered_dir),
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.yaml").write_text(
        yaml.safe_dump(report, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    for name in _BOARD_ORDER:
        board = report["leaderboards"][name]
        rows = board.get("rows")
        if rows:
            _write_csv(out_dir / f"{name}.csv", rows)
    return report


# ---- run 封存載入（fail-closed） -------------------------------------------


def _load_report_run(run_dir: Path, snapshot: PricingSnapshot | None) -> _ReportRun:
    store = RunStore.open(run_dir)  # fail-closed：run.yaml schema、event seq
    record = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))

    result_path = run_dir / "result.yaml"
    if not result_path.is_file():
        raise _SkipRun("run 未完成（缺 result.yaml）")
    result = yaml.safe_load(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ReportError(f"result.yaml 內容必須是 mapping：{run_dir.name}")
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ReportError(
            f"result.yaml schema_version 不符（{run_dir.name}）："
            f"{result.get('schema_version')!r}"
        )
    if result.get("mode") != "run":
        raise _SkipRun(f"mode 非 run（{result.get('mode')!r}），不進 ranked 榜")
    if result.get("human") is True:
        raise _SkipRun("human run 不進 ranked 榜（spec §5.4）")

    clear = result.get("clear")
    if clear not in (0, 1):
        raise ReportError(f"result.yaml clear 非 0/1（{run_dir.name}）：{clear!r}")
    power = result.get("power")
    if not isinstance(power, dict) or not isinstance(
        power.get("total"), (int, float)
    ):
        raise ReportError(f"result.yaml 缺 power.total（{run_dir.name}）")

    entries = load_ledger(run_dir)
    if not entries:
        raise ReportError(f"ledger.jsonl 無任何 entry（{run_dir.name}）")
    cost, cost_reason = _run_cost(record, entries, snapshot)

    card = load_card(Path(str(record["encounter_dir"])) / "card.yaml")
    flood = flood_metrics(store.load_events(), card, load_flood_coeffs())

    return _ReportRun(
        run_id=str(record["run_id"]),
        model=str(record.get("model", _OFFLINE_NA)),
        loadout=str(result.get("loadout", record.get("loadout", _OFFLINE_NA))),
        clear=int(clear),
        power_total=float(power["total"]),
        cost=cost,
        cost_reason=cost_reason,
        work_tokens=_work_tokens_of(result, run_dir),
        observable_tokens=_observable_tokens(entries, run_dir),
        control=flood.control,
        ftr=flood.ftr,
        tau_uncalibrated=flood.tau_uncalibrated,
        flood_create_tokens=flood.flood_create_tokens,
        flood_repair_tokens=flood.flood_repair_tokens,
    )


def _run_cost(
    record: dict, entries: list[LedgerEntry], snapshot: PricingSnapshot | None
) -> tuple[Decimal | None, str | None]:
    """C_run 只以 run.yaml pin 的 snapshot 計價（§10.2）；不可得 → NA＋理由。"""
    pricing_hash = record.get("pricing_hash")
    if pricing_hash == _OFFLINE_NA:
        return None, "run 未 pin pricing snapshot（pricing_hash=NA）"
    if snapshot is None:
        return None, "未提供 --pricing snapshot，無法對 pin 的 hash 計價"
    if snapshot.content_hash != pricing_hash:
        return None, (
            "--pricing snapshot content hash 與 run.yaml pin 不符"
            f"（{snapshot.content_hash[:12]}… != {str(pricing_hash)[:12]}…）"
        )
    return compute_run_cost(entries, snapshot).total, None


def _work_tokens_of(result: dict, run_dir: Path) -> int | None:
    ledger = result.get("ledger")
    if not isinstance(ledger, dict) or "work_tokens" not in ledger:
        raise ReportError(f"result.yaml 缺 ledger.work_tokens（{run_dir.name}）")
    work = ledger["work_tokens"]
    if work == _OFFLINE_NA:
        return None
    if isinstance(work, bool) or not isinstance(work, int):
        raise ReportError(
            f"result.yaml ledger.work_tokens 非整數或 NA（{run_dir.name}）：{work!r}"
        )
    return work


def _observable_tokens(entries: list[LedgerEntry], run_dir: Path) -> int:
    """common-observable = input + output_visible（§10.1；永遠可得）。"""
    total = 0
    for entry in entries:
        if entry.output_visible is None:
            raise ReportError(
                f"ledger entry 缺 output_visible，observable 欄無法計算"
                f"（{run_dir.name} turn={entry.turn}）"
            )
        total += entry.billed_input_total + entry.output_visible
    return total


# ---- 榜組裝（純資料轉換） ---------------------------------------------------


def _build_leaderboards(runs: list[_ReportRun], registered_dir: Path) -> dict:
    groups: dict[tuple[str, str], list[_ReportRun]] = {}
    for run in runs:
        groups.setdefault((run.model, run.loadout), []).append(run)
    samples = {key: [_sample_of(run) for run in members] for key, members in groups.items()}

    boards = {
        "clear_rate": _clear_rate_board(groups),
        "cost_per_clear": _cost_board(groups, samples),
        "tokens_per_clear": _efficiency_board(samples, tokens_per_clear),
        "qaty": _efficiency_board(samples, qaty),
        "eutb": _eutb_board(samples, registered_dir),
        "power": _mean_board(groups, lambda run: run.power_total, reverse=True),
        "control": _control_board(groups),
        "ftr": _ftr_board(groups),
    }
    return boards


def _sample_of(run: _ReportRun) -> RunSample:
    return RunSample(
        clear=run.clear,
        power=run.power_total,
        cost=run.cost,
        work_tokens=run.work_tokens,
        observable_tokens=run.observable_tokens,
    )


def _group_fields(key: tuple[str, str]) -> dict:
    return {"model": key[0], "loadout": key[1]}


def _clear_rate_board(groups: dict[tuple[str, str], list[_ReportRun]]) -> dict:
    rows = []
    for key, members in groups.items():
        clears = sum(run.clear for run in members)
        rows.append(
            {
                **_group_fields(key),
                "runs": len(members),
                "clears": clears,
                "value": clears / len(members),
            }
        )
    rows.sort(key=lambda row: (-row["value"], row["model"], row["loadout"]))
    return {"status": "ok", "rows": rows}


def _cost_board(
    groups: dict[tuple[str, str], list[_ReportRun]],
    samples: dict[tuple[str, str], list[RunSample]],
) -> dict:
    """CostPerClear 榜：cost 缺漏（未 pin／snapshot 不可得）→ NA，不假 0。"""
    ranked_rows: list[tuple[Decimal, dict]] = []
    na_rows: list[dict] = []
    for key, members in groups.items():
        missing = [run for run in members if run.cost is None]
        if missing:
            na_rows.append(
                {
                    **_group_fields(key),
                    "value": _OFFLINE_NA,
                    "ranked": False,
                    "reason": missing[0].cost_reason or "run 缺 C_run",
                }
            )
            continue
        value = cost_per_clear(samples[key])
        ranked_rows.append(
            (
                value,
                {
                    **_group_fields(key),
                    "value": "inf" if not value.is_finite() else str(value),
                    "ranked": True,
                    "reason": "",
                },
            )
        )
    ranked_rows.sort(key=lambda item: (item[0], item[1]["model"], item[1]["loadout"]))
    na_rows.sort(key=lambda row: (row["model"], row["loadout"]))
    return {"status": "ok", "rows": [row for _, row in ranked_rows] + na_rows}


def _efficiency_board(
    samples: dict[tuple[str, str], list[RunSample]], metric_fn
) -> dict:
    results = {key: metric_fn(samples[key]) for key in samples}
    return _efficiency_rows(results)


def _eutb_board(
    samples: dict[tuple[str, str], list[RunSample]], registered_dir: Path
) -> dict:
    """EuTB 榜：registered 預算檔缺失 → skipped 而非假值（§19.9 fail-closed）。"""
    try:
        budget = load_eutb_budget(Path(registered_dir) / _EUTB_BUDGET_FILE)
    except NotRegisteredError as exc:
        return {"status": "skipped", "reason": str(exc)}
    results = {key: eutb(samples[key], budget) for key in samples}
    return _efficiency_rows(results)


def _efficiency_rows(results: dict[tuple[str, str], EfficiencyResult]) -> dict:
    """EfficiencyResult → 榜列（雙欄＋cohort，F17／§13）。

    排名一律委派 metrics 層 ``rank_efficiency``（方向由指標 pin，caller
    不得自選；跨 cohort 排名在該層被拒）。跨 cohort 時整榜退為
    ``non_ranking``：rows **只發布**以 input + output_visible 一致計算的
    common-observable 描述性欄位——cohort 依賴的 ``value`` 欄（full
    群組為 T^work 基礎值）一概不出——列序退為群組名稱字典序。兩分支
    的 rows 皆逐列帶 ``non_ranking`` 標註（non-ranking 另帶 ``note``），
    CSV 由 rows 直出，檔案層即可與排名榜區分（§13）。
    """
    try:
        ordered = rank_efficiency(results)
    except CohortMismatchError as exc:
        note = str(exc)
        rows = [
            {
                **_group_fields(key),
                "observable": _num(results[key].observable),
                "disclosure_cohort": results[key].disclosure_cohort,
                "non_ranking": True,
                "note": note,
            }
            for key in sorted(results)
        ]
        return {"status": "ok", "non_ranking": True, "note": note, "rows": rows}
    rows = [
        {
            **_group_fields(key),
            "value": _num(results[key].value),
            "observable": _num(results[key].observable),
            "disclosure_cohort": results[key].disclosure_cohort,
            "non_ranking": False,
        }
        for key in ordered
    ]
    return {"status": "ok", "non_ranking": False, "rows": rows}


def _mean_board(
    groups: dict[tuple[str, str], list[_ReportRun]], value_of, *, reverse: bool
) -> dict:
    rows = []
    for key, members in groups.items():
        rows.append(
            {
                **_group_fields(key),
                "runs": len(members),
                "value": statistics.fmean(value_of(run) for run in members),
            }
        )
    rows.sort(
        key=lambda row: (-row["value"] if reverse else row["value"], row["model"], row["loadout"])
    )
    return {"status": "ok", "rows": rows}


def _control_board(groups: dict[tuple[str, str], list[_ReportRun]]) -> dict:
    board = _mean_board(groups, lambda run: run.control, reverse=True)
    for row in board["rows"]:
        members = groups[(row["model"], row["loadout"])]
        # τ 未經 §10.4 estimator 校準的 Control 必須明示，不得偽裝正式值
        row["tau_uncalibrated"] = any(run.tau_uncalibrated for run in members)
    return board


def _ftr_board(groups: dict[tuple[str, str], list[_ReportRun]]) -> dict:
    board = _mean_board(groups, lambda run: run.ftr, reverse=False)
    for row in board["rows"]:
        members = groups[(row["model"], row["loadout"])]
        row["flood_create_tokens"] = sum(run.flood_create_tokens for run in members)
        row["flood_repair_tokens"] = sum(run.flood_repair_tokens for run in members)
    return board


# ---- 序列化 helpers ----------------------------------------------------------


def _num(value: float | None) -> float | str:
    """榜值序列化：NA → "NA"、無限大 → "inf"（artifact 格式英文）。"""
    if value is None:
        return _OFFLINE_NA
    if math.isinf(value):
        return "inf"
    return value


def _run_row(run: _ReportRun) -> dict:
    return {
        "run_id": run.run_id,
        "model": run.model,
        "loadout": run.loadout,
        "clear": run.clear,
        "power_total": run.power_total,
        "cost": _OFFLINE_NA if run.cost is None else str(run.cost),
        "work_tokens": _OFFLINE_NA if run.work_tokens is None else run.work_tokens,
        "observable_tokens": run.observable_tokens,
        "control": run.control,
        "ftr": run.ftr,
        "tau_uncalibrated": run.tau_uncalibrated,
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# result.yaml 組裝（純資料轉換）
# ---------------------------------------------------------------------------


def _build_result(
    *,
    run_id: str,
    diff_sha256: str,
    clear: int,
    main_public_green: bool,
    evaluation: FinalEvaluation,
    public_results: ProbeResults,
    economy: str,
    economy_reason: str,
) -> dict:
    return {
        "run_id": run_id,
        "mode": "score-diff",
        "diff_sha256": diff_sha256,
        "clear": clear,
        "protocol_failed": False,
        "main_public_green": main_public_green,
        "gates": {
            "critical_pass": evaluation.gates.critical_pass,
            "power_cap": evaluation.gates.power_cap,
            "run_invalid": evaluation.gates.run_invalid,
        },
        "power": _power_dict(evaluation.power),
        "probes": {
            "public": _probes_dict(public_results),
            "evaluator": _probes_dict(evaluation.probe_outcomes),
        },
        "economy": economy,
        "economy_reason": economy_reason,
    }


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


def _probes_dict(results: ProbeResults) -> dict:
    return {probe_id: _outcome_dict(results[probe_id]) for probe_id in results}


def _outcome_dict(outcome: ProbeOutcome) -> dict:
    return {
        "status": outcome.status,
        "cases_total": outcome.cases_total,
        "cases_passed": outcome.cases_passed,
        "failure_fingerprints": list(outcome.failure_fingerprints),
        "wall_ms": outcome.wall_ms,
        "cpu_ms": outcome.cpu_ms,
    }


def _economy_na(card: IssueCard) -> tuple[str, str]:
    """milestone A 離線評分的 Economy 一律 `NA`（不得記 0，§10.1）。"""
    if card.reference_cost is None:
        return _OFFLINE_NA, "card reference_cost 為 null，Economy 無法計算"
    return _OFFLINE_NA, "離線評分無 ledger 支出，Economy 不適用"


def _is_green(results: ProbeResults, probe_id: str) -> bool:
    return probe_id in results and results[probe_id].status == "passed"


# ---------------------------------------------------------------------------
# 沙箱 toolchain 佈線（probe 執行唯一 seam 是 IsolationRunner）
# ---------------------------------------------------------------------------


def _require_isolation(bwrap_path: str, toolchain: tuple[Path, ...]) -> None:
    """namespace 能力 fail-closed：不足即拒絕執行 candidate code（spec §7）。"""
    probe = IsolationRunner(Path("/tmp"), toolchain, bwrap_path=bwrap_path)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        raise ScoreDiffError("sandbox 隔離能力不足（degraded），拒絕執行 candidate code")


def _pytest_site_dir() -> Path:
    """host pytest 的 site-packages 目錄（沙箱內以 ro-bind + sys.path 提供）。"""
    spec = importlib.util.find_spec("pytest")
    if spec is None or spec.origin is None:
        raise ScoreDiffError("找不到 pytest，probe 無法在沙箱內執行")
    return Path(spec.origin).resolve().parents[1]


def _toolchain_paths() -> tuple[Path, ...]:
    """沙箱 ro-bind 的 toolchain 路徑：/usr + pytest site dir（不在 /usr 下時）。"""
    site_dir = _pytest_site_dir()
    paths: list[Path] = [Path("/usr")]
    if not site_dir.is_relative_to(Path("/usr")):
        paths.append(site_dir)
    return tuple(paths)


def _sandbox_pytest_argv() -> tuple[str, ...]:
    """沙箱內 pytest 起手式：以 bootstrap 注入 site dir（沙箱無 HOME/user-site）。"""
    site_dir = _pytest_site_dir()
    if site_dir.is_relative_to(Path("/usr")):
        return DEFAULT_PYTEST_ARGV
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(site_dir)!r}); "
        "from pytest import console_main; sys.exit(console_main())"
    )
    return ("python3", "-B", "-c", bootstrap)


def _ruff_argv(toolchain: tuple[Path, ...]) -> tuple[str, ...]:
    """沙箱內可執行的 ruff argv；找不到或不在 toolchain 內 → 交給 evaluator
    fail-closed（lint 0 分），不擴充 bind allowlist。"""
    found = shutil.which("ruff")
    if found is not None:
        ruff = Path(found).resolve()
        if any(ruff.is_relative_to(root) for root in toolchain):
            return (str(ruff),)
    return ("ruff",)


if __name__ == "__main__":
    raise SystemExit(main())
