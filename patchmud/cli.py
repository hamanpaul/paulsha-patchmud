"""patchmud CLI 進入點。

子命令（validate-deck / score-diff / run / pilot / replay / report）依
docs/superpowers/plans/2026-07-16-patchmud-mvp.md 逐 task 落地。

`score-diff`（Task 8，milestone A 收口）：給定 encounter + 手工 diff，離線
跑完整評分——全部 public probes（§5.2 終局語意）+ hidden evaluator（§9）——
落盤 `result.yaml`（PowerReport、gates、probe outcomes、Clear、Economy）並
產出私有封存（§12.1）。執行 candidate code 的唯一 seam 是 `IsolationRunner`；
namespace 能力不足一律 fail-closed 拒絕執行。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.deck.model import DeckError, IssueCard
from patchmud.evaluator.evaluate import EvaluatorError, FinalEvaluation, evaluate_final
from patchmud.evaluator.gates import compute_clear
from patchmud.evaluator.power import PowerReport
from patchmud.ledger.tokens import LedgerError
from patchmud.sandbox.isolate import DEFAULT_BWRAP_PATH, IsolationRunner
from patchmud.sandbox.probes import (
    DEFAULT_PYTEST_ARGV,
    ProbeOutcome,
    ProbeResults,
    ProbeSuite,
)
from patchmud.sandbox.workspace import Workspace, WorkspaceError
from patchmud.store.run_store import RunStore
from patchmud.store.schemas import StoreError

__all__ = ["ScoreDiffError", "ScoreDiffSummary", "main", "score_diff"]

#: 離線評分沒有 harness prompt / pricing / schedule；欄位以 NA 佔位（非 0，§10.1）。
_OFFLINE_NA = "NA"


class ScoreDiffError(Exception):
    """score-diff 操作性失敗（輸入缺漏、隔離能力不足、diff 遭拒等）。"""


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
            "patchmud 0.0.0 — 子命令：score-diff；其餘見 "
            "docs/superpowers/plans/2026-07-16-patchmud-mvp.md"
        )
        return 0
    if args[0] == "score-diff":
        return _cmd_score_diff(args[1:])
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
