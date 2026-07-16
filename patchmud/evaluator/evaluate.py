"""Hidden evaluator：終局評分（spec §9.1）。

只在終局後執行：evaluator 在**獨立 checkout**（frozen base + final diff）以
§7 同級 namespace 隔離套 hidden probes——永不觸碰 agent worktree，hidden
資產也只 overlay 進這個 evaluator 私有 checkout。

流程：
1. 從 frozen repo clone 出獨立 checkout（deterministic git env）。
2. `git apply --numstat` 解析 final diff 的檔案變更（無法解析 → fail-closed）。
3. lint 基準：對 changed `.py` 檔在 frozen base 上跑 `ruff check`（經
   IsolationRunner seam）；套 diff 後再跑一次，計「新增 diagnostics」。
   ruff 無法執行 / 輸出不可解析 → `None`（無法驗證 → lint 0 分，fail-closed）。
4. overlay hidden probes（只複製 card 引用的檔案）、以 deck 原始 bytes 還原
   保護區（plan invariant 2），經 `ProbeSuite` 在隔離內執行全部 rubric +
   critical probes。
5. `evaluate_gates` → `score_power` → `apply_power_cap`（cap 疊加取低）。

runtime_efficiency 判定以量測當下 outcome 定案並封存（PerfJudgment），
L1 重算只引用封存值（spec §12.2）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from patchmud.deck.materialize import FrozenRepo
from patchmud.deck.model import IssueCard
from patchmud.evaluator.gates import GateResult, apply_power_cap, evaluate_gates
from patchmud.evaluator.power import (
    EvaluatorError,
    FileChange,
    PowerReport,
    score_power,
)
from patchmud.sandbox.isolate import Execution
from patchmud.sandbox.probes import (
    DEFAULT_PYTEST_ARGV,
    Probe,
    ProbeResults,
    ProbeSuite,
)
from patchmud.sandbox.workspace import HARNESS_CONFIG_NAMES, PROTECTED_PREFIXES

__all__ = ["EvaluatorError", "FinalEvaluation", "evaluate_final"]

_HIDDEN_PREFIX = "hidden/"
_TIMINGS_SCHEMA_VERSION = 1
_DEFAULT_TIMEOUT_S = 120.0


class _Runner(Protocol):
    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution: ...


#: runner 參數型別：runner 實例，或 `factory(checkout) -> runner`
RunnerOrFactory = _Runner | Callable[[Path], _Runner]


@dataclass(frozen=True)
class FinalEvaluation:
    """終局評分結果：probe 逐項 outcome、Power（已套 cap）、hard gates。"""

    probe_outcomes: ProbeResults
    power: PowerReport
    gates: GateResult


def evaluate_final(
    card: IssueCard,
    frozen: FrozenRepo,
    final_diff: str,
    runner: RunnerOrFactory,
    *,
    encounter_dir: Path,
    checkout_dir: Path | None = None,
    pytest_argv: Sequence[str] = DEFAULT_PYTEST_ARGV,
    ruff_argv: Sequence[str] = ("ruff",),
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    hidden_access_detected: bool = False,
) -> FinalEvaluation:
    """在獨立 checkout（frozen + final diff）執行 hidden evaluator 全套。

    `runner` 可以是 runner 實例（unit tests 注入 fake），或
    `factory(checkout_path) -> runner`（真 IsolationRunner 需綁定 checkout）。
    `checkout_dir` 未指定時使用暫存目錄並於結束後清除。
    """
    encounter_dir = Path(encounter_dir)
    cleanup = checkout_dir is None
    if checkout_dir is None:
        checkout = Path(tempfile.mkdtemp(prefix="patchmud-eval-"))
    else:
        checkout = Path(checkout_dir)
        if checkout.exists() and any(checkout.iterdir()):
            raise EvaluatorError(f"checkout 目的地必須是不存在或空目錄：{checkout}")

    try:
        _clone_frozen(frozen, checkout)

        diff_text = final_diff or ""
        has_diff = bool(diff_text.strip())
        file_changes = _numstat(diff_text, checkout) if has_diff else ()
        changed_py = [fc.path for fc in file_changes if fc.path.endswith(".py")]

        runner_obj: _Runner = (
            runner if hasattr(runner, "run") else runner(checkout)  # type: ignore[operator]
        )

        before = _ruff_counts(
            runner_obj, checkout, ruff_argv, _existing(checkout, changed_py), timeout_s
        )
        if has_diff:
            _apply_diff(diff_text, checkout)
        after = _ruff_counts(
            runner_obj, checkout, ruff_argv, _existing(checkout, changed_py), timeout_s
        )
        lint_new: int | None = None
        if before is not None and after is not None:
            lint_new = sum(
                max(0, count - before[key]) for key, count in after.items()
            )

        probes = _evaluation_probes(card)
        _overlay_hidden(
            [p.probe_id for p in probes if p.probe_id.startswith(_HIDDEN_PREFIX)],
            encounter_dir,
            checkout,
        )

        suite = ProbeSuite(
            probes, runner_obj, pytest_argv=pytest_argv, timeout_s=timeout_s
        )
        outcomes = suite.run(_EvaluatorCheckout(checkout, encounter_dir))

        gates = evaluate_gates(
            card, outcomes, hidden_access_detected=hidden_access_detected
        )
        report = score_power(
            card,
            outcomes,
            file_changes,
            lint_new_diagnostics=lint_new,
            reference_timings_ms=_load_reference_timings(encounter_dir),
        )
        return FinalEvaluation(
            probe_outcomes=outcomes,
            power=apply_power_cap(report, gates),
            gates=gates,
        )
    finally:
        if cleanup:
            shutil.rmtree(checkout, ignore_errors=True)


# ---------------------------------------------------------------------------
# 獨立 checkout
# ---------------------------------------------------------------------------


class _EvaluatorCheckout:
    """ProbeSuite 需要的最小 workspace 介面（worktree + restore_protected）。"""

    def __init__(self, worktree: Path, encounter_dir: Path) -> None:
        self._worktree = worktree
        self._encounter_dir = encounter_dir

    @property
    def worktree(self) -> Path:
        return self._worktree

    def restore_protected(self) -> None:
        """以 deck 原始 bytes 還原保護區與 harness 設定檔（plan invariant 2、§7）。

        除三個保護區目錄外，另中和 pytest / Python 自動載入的 harness 設定檔
        （`conftest.py`、`pytest.ini`、`pyproject.toml`、`tox.ini`、`setup.cfg`、
        `sitecustomize.py`…）：deck 宣告者還原原始 bytes、agent 於 final diff 新增
        者一律移除。否則 agent 可在保護區外的 auto-loaded 設定檔注入 hook（例如
        `pytest_runtest_makereport` 將 failed 改 passed）偽造 hidden probe 判定。
        """
        deck_repo = self._encounter_dir / "repo"
        for prefix in PROTECTED_PREFIXES:
            rel = prefix.rstrip("/")
            src = deck_repo / rel
            dst = self._worktree / rel
            if dst.exists():
                shutil.rmtree(dst)
            if src.is_dir():
                shutil.copytree(src, dst)
        self._neutralize_harness_config(deck_repo)

    def _neutralize_harness_config(self, deck_repo: Path) -> None:
        """checkout 內所有 harness 設定檔 → deck 原始 bytes 或（agent 新增者）移除。"""
        deck_files: dict[str, Path] = {}
        for src in deck_repo.rglob("*"):
            if ".git" in src.parts:
                continue
            if src.is_file() and src.name in HARNESS_CONFIG_NAMES:
                deck_files[src.relative_to(deck_repo).as_posix()] = src

        for dst in list(self._worktree.rglob("*")):
            if ".git" in dst.parts:
                continue
            if not dst.is_file() or dst.name not in HARNESS_CONFIG_NAMES:
                continue
            rel = dst.relative_to(self._worktree).as_posix()
            deck_src = deck_files.get(rel)
            if deck_src is not None:
                shutil.copyfile(deck_src, dst)  # deck 宣告 → 還原原始 bytes
            else:
                dst.unlink()  # agent 新增 → 移除

        for rel, src in deck_files.items():  # deck 宣告但被 agent 刪除 → 補回
            dst = self._worktree / rel
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)


def _clone_frozen(frozen: FrozenRepo, checkout: Path) -> None:
    _git(
        ["git", "clone", "--quiet", "--no-hardlinks", str(frozen.path), str(checkout)]
    )
    _git(
        [
            "git",
            "-C",
            str(checkout),
            "-c",
            "advice.detachedHead=false",
            "checkout",
            "--quiet",
            frozen.sha,
        ]
    )


def _apply_diff(diff: str, checkout: Path) -> None:
    check = _git_raw(
        ["git", "apply", "--whitespace=nowarn", "--check", "-"],
        cwd=checkout,
        stdin=diff,
    )
    if check.returncode != 0:
        raise EvaluatorError(f"final diff 無法套用：{check.stderr.strip()[:200]}")
    applied = _git_raw(
        ["git", "apply", "--whitespace=nowarn", "-"], cwd=checkout, stdin=diff
    )
    if applied.returncode != 0:
        raise EvaluatorError(
            f"final diff 於 --check 通過後套用失敗：{applied.stderr.strip()[:200]}"
        )


def _numstat(diff: str, checkout: Path) -> tuple[FileChange, ...]:
    res = _git_raw(["git", "apply", "--numstat", "-"], cwd=checkout, stdin=diff)
    if res.returncode != 0:
        raise EvaluatorError(f"final diff 無法解析：{res.stderr.strip()[:200]}")
    changes: list[FileChange] = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        changes.append(
            FileChange(
                path=path,
                added=int(added) if added != "-" else 0,
                deleted=int(deleted) if deleted != "-" else 0,
            )
        )
    if not changes:
        raise EvaluatorError("final diff 解析不到任何檔案變更")
    return tuple(changes)


def _overlay_hidden(
    hidden_paths: Sequence[str], encounter_dir: Path, checkout: Path
) -> None:
    for rel in sorted(set(hidden_paths)):
        src = encounter_dir / rel
        if not src.is_file():
            raise EvaluatorError(f"hidden probe 不存在於 deck：{rel}")
        dst = checkout / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def _evaluation_probes(card: IssueCard) -> tuple[Probe, ...]:
    """rubric + critical 全部 probes（去重、保序）；hidden 與 public 都經同一隔離。"""
    rubric = card.power_rubric
    ordered: dict[str, Probe] = {}

    def add(path: str) -> None:
        kind = "hidden" if path.startswith(_HIDDEN_PREFIX) else "compat"
        ordered.setdefault(path, Probe(probe_id=path, kind=kind, target=path))

    for cr in card.critical_requirements:
        add(cr.hidden_probe)
    for group in rubric.functional.groups:
        add(group.probe)
    for path in rubric.robustness.probes:
        add(path)
    for path in rubric.compatibility.probes:
        add(path)
    for cp in card.compat_probes:
        add(cp.probe)
    for path in rubric.runtime_efficiency.probes:
        add(path)
    return tuple(ordered.values())


# ---------------------------------------------------------------------------
# lint（ruff 經 IsolationRunner seam；無法驗證 → None → 0 分 fail-closed）
# ---------------------------------------------------------------------------


def _existing(checkout: Path, paths: Sequence[str]) -> list[str]:
    return [p for p in paths if (checkout / p).is_file()]


def _ruff_counts(
    runner: _Runner,
    checkout: Path,
    ruff_argv: Sequence[str],
    files: Sequence[str],
    timeout_s: float,
) -> Counter | None:
    """對 files 跑 `ruff check --output-format=json`，回 (path, code) 計數。

    無檔可 lint → 空 Counter（零 diagnostics）。執行失敗（exit ≥ 2、timeout、
    輸出不可解析）→ None（無法驗證）。
    """
    if not files:
        return Counter()
    argv = [*ruff_argv, "check", "--output-format=json", *files]
    try:
        ex = runner.run(list(argv), cwd=checkout, timeout_s=timeout_s)
    except OSError:
        return None
    if ex.timed_out or ex.exit_code not in (0, 1):
        return None
    try:
        data = json.loads(ex.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    counts: Counter = Counter()
    for item in data:
        if not isinstance(item, dict):
            return None
        counts[(_normalize(str(item.get("filename", "")), checkout), str(item.get("code")))] += 1
    return counts


def _normalize(filename: str, checkout: Path) -> str:
    path = Path(filename)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(checkout.resolve()).as_posix()
        except ValueError:
            return filename
    return filename


# ---------------------------------------------------------------------------
# reference timings（deck CI 產物；僅 evaluator 使用，永不進 run）
# ---------------------------------------------------------------------------


def _load_reference_timings(encounter_dir: Path) -> dict[str, float]:
    path = encounter_dir / "hidden" / "reference_timings.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvaluatorError(f"reference_timings.yaml 解析失敗：{exc}") from exc
    if not isinstance(data, dict):
        raise EvaluatorError("reference_timings.yaml 頂層必須是 mapping")
    if data.get("schema_version") != _TIMINGS_SCHEMA_VERSION:
        raise EvaluatorError(
            f"reference_timings schema_version 不支援：{data.get('schema_version')}"
            f"（僅支援 {_TIMINGS_SCHEMA_VERSION}）"
        )
    timings = data.get("timings_ms") or {}
    if not isinstance(timings, dict):
        raise EvaluatorError("timings_ms 必須是 mapping")
    return {str(key): float(value) for key, value in timings.items()}


# ---------------------------------------------------------------------------
# host git helpers（deterministic env；不執行 candidate code）
# ---------------------------------------------------------------------------


def _git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def _git(args: list[str]) -> None:
    res = _git_raw(args)
    if res.returncode != 0:
        raise EvaluatorError(f"{' '.join(args[:3])} 失敗：{res.stderr.strip()[:200]}")


def _git_raw(
    args: list[str], cwd: Path | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        env=_git_env(),
        cwd=str(cwd) if cwd else None,
        input=stdin,
        capture_output=True,
        text=True,
    )
