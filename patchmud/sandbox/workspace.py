"""Workspace：patch stack、probe 保護區、shadow checkpoints（spec §7）。

- patch 套用走 `git apply` 嚴格模式（先 `--check`；無 fuzz、無 3way）。
- `tests/public/**`、`tests/starter/**`、`benchmark/**` 對 patch 唯讀；
  `restore_protected()` 以 deck 原始 bytes 還原（probe 執行前的防改測試防線）。
- checkpoint 以獨立 shadow bare repo 保存每回合 worktree 快照；
  固定 identity 與 epoch 0 timestamp，相同內容（含 parent 鏈）SHA 恆定，
  供離線 MTY 重播與 replay 驗證。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Literal

from patchmud.deck.materialize import FrozenRepo

__all__ = ["ApplyResult", "DiffStats", "Workspace", "WorkspaceError"]

PROTECTED_PREFIXES: tuple[str, ...] = ("tests/public/", "tests/starter/", "benchmark/")
_PROTECTED_DIRS: tuple[str, ...] = ("tests/public", "tests/starter", "benchmark")
_TEST_PREFIX = "tests/agent/"

_FIXED_IDENTITY_NAME = "PatchMUD Engine"
_FIXED_IDENTITY_EMAIL = "engine@patchmud.invalid"
_EPOCH_ZERO = "1970-01-01T00:00:00+00:00"


class WorkspaceError(Exception):
    """Workspace 內部不可恢復錯誤（git 失敗等）。"""


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    reason: str | None = None

    @property
    def rejected(self) -> bool:
        return not self.applied


@dataclass(frozen=True)
class DiffStats:
    added: int
    deleted: int
    files: tuple[str, ...]
    reverted_loc: int


@dataclass
class Workspace:
    frozen: FrozenRepo
    encounter_dir: Path
    shadow_dir: Path

    _stack: list[str] = field(default_factory=list, init=False)
    _added_lines: dict[str, Counter] = field(default_factory=dict, init=False)
    _reverted_loc: int = field(default=0, init=False)
    _last_checkpoint: str | None = field(default=None, init=False)
    _last_tree: str | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.encounter_dir = Path(self.encounter_dir)
        self.shadow_dir = Path(self.shadow_dir)
        if not self.shadow_dir.exists():
            self.shadow_dir.mkdir(parents=True)
            self._run(["git", "init", "--quiet", "--bare", str(self.shadow_dir)], env=self._base_env())

    @property
    def worktree(self) -> Path:
        return self.frozen.path

    # ---- patch 套用 ----------------------------------------------------

    def apply_patch(self, diff: str, kind: Literal["production", "test"]) -> ApplyResult:
        paths = self._patch_paths(diff)
        if paths is None:
            return ApplyResult(False, "patch 無法解析")
        illegal = self._illegal_paths(paths, kind)
        if illegal:
            return ApplyResult(False, illegal)
        check = self._git_apply(diff, check_only=True)
        if check.returncode != 0:
            return ApplyResult(False, f"git apply --check 失敗：{check.stderr.strip()[:200]}")
        applied = self._git_apply(diff, check_only=False)
        if applied.returncode != 0:  # --check 過了卻套不上：狀態未知，fail-closed
            raise WorkspaceError(f"git apply 於 --check 通過後失敗：{applied.stderr.strip()}")
        self._stack.append(diff)
        self._track_revert(diff)
        return ApplyResult(True)

    def rollback(self) -> bool:
        if not self._stack:
            return False
        replay = self._stack[:-1]
        self._reset_to_frozen()
        self._stack = []
        self._added_lines = {}
        self._reverted_loc = 0
        for d in replay:
            res = self._git_apply(d, check_only=False)
            if res.returncode != 0:
                raise WorkspaceError("rollback 重放 patch stack 失敗")
            self._stack.append(d)
            self._track_revert(d)
        return True

    # ---- 保護區 ---------------------------------------------------------

    def restore_protected(self) -> None:
        """以 deck 原始 bytes 還原 probe 保護區（多餘檔案一併移除）。"""
        deck_repo = self.encounter_dir / "repo"
        for rel in _PROTECTED_DIRS:
            src = deck_repo / rel
            dst = self.worktree / rel
            if dst.exists():
                shutil.rmtree(dst)
            if src.is_dir():
                shutil.copytree(src, dst)

    # ---- 快照與差異 ------------------------------------------------------

    def checkpoint(self) -> str:
        env = self._shadow_env()
        self._run(["git", "add", "-A"], env=env, cwd=self.worktree)
        tree = self._run(["git", "write-tree"], env=env, cwd=self.worktree).strip()
        if self._last_checkpoint is not None and tree == self._last_tree:
            return self._last_checkpoint
        args = ["git", "commit-tree", tree, "-m", f"checkpoint {len(self._stack)}"]
        if self._last_checkpoint is not None:
            args += ["-p", self._last_checkpoint]
        sha = self._run(args, env=env, cwd=self.worktree).strip()
        self._run(["git", "update-ref", "refs/heads/checkpoints", sha], env=env, cwd=self.worktree)
        self._last_checkpoint, self._last_tree = sha, tree
        return sha

    def cumulative_diff(self) -> str:
        env = self._base_env()
        self._run(["git", "add", "-A"], env=env, cwd=self.worktree)
        return self._run(
            ["git", "diff", "--cached", self.frozen.sha], env=env, cwd=self.worktree
        )

    def diff_stats(self) -> DiffStats:
        env = self._base_env()
        self._run(["git", "add", "-A"], env=env, cwd=self.worktree)
        numstat = self._run(
            ["git", "diff", "--cached", "--numstat", self.frozen.sha], env=env, cwd=self.worktree
        )
        added = deleted = 0
        files: list[str] = []
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            files.append(path)
            if a != "-":
                added += int(a)
            if d != "-":
                deleted += int(d)
        return DiffStats(added=added, deleted=deleted, files=tuple(files), reverted_loc=self._reverted_loc)

    # ---- 內部 -----------------------------------------------------------

    def _patch_paths(self, diff: str) -> list[str] | None:
        res = self._run_raw(
            ["git", "apply", "--numstat", "-"], env=self._base_env(), cwd=self.worktree, stdin=diff
        )
        if res.returncode != 0:
            return None
        paths: list[str] = []
        for line in res.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                paths.append(parts[2])
        return paths if paths else None

    @staticmethod
    def _illegal_paths(paths: list[str], kind: str) -> str | None:
        for p in paths:
            pure = PurePosixPath(p)
            if pure.is_absolute() or ".." in pure.parts or ".git" in pure.parts:
                return f"非法路徑：{p}"
            if kind == "test":
                if not p.startswith(_TEST_PREFIX):
                    return f"test patch 只能觸及 {_TEST_PREFIX}**：{p}"
            else:
                for prefix in PROTECTED_PREFIXES:
                    if p.startswith(prefix):
                        return f"production patch 不得觸及保護區：{p}"
        return None

    def _track_revert(self, diff: str) -> None:
        """removal 行若命中先前 patch 新增的行內容 → reverted_loc（先比對舊帳再記新增）。"""
        current: str | None = None
        removals: list[tuple[str, str]] = []
        additions: list[tuple[str, str]] = []
        for line in diff.splitlines():
            if line.startswith("+++ "):
                target = line[4:].strip()
                current = target[2:] if target.startswith("b/") else target
            elif current and line.startswith("+") and not line.startswith("+++"):
                additions.append((current, line[1:]))
            elif current and line.startswith("-") and not line.startswith("---"):
                removals.append((current, line[1:]))
        for path, content in removals:
            bucket = self._added_lines.get(path)
            if bucket and bucket[content] > 0:
                bucket[content] -= 1
                self._reverted_loc += 1
        for path, content in additions:
            self._added_lines.setdefault(path, Counter())[content] += 1

    def _reset_to_frozen(self) -> None:
        env = self._base_env()
        self._run(["git", "reset", "--hard", self.frozen.sha, "--quiet"], env=env, cwd=self.worktree)
        self._run(["git", "clean", "-fdq"], env=env, cwd=self.worktree)

    def _git_apply(self, diff: str, *, check_only: bool) -> subprocess.CompletedProcess[str]:
        args = ["git", "apply", "--whitespace=nowarn"]
        if check_only:
            args.append("--check")
        args.append("-")
        return self._run_raw(args, env=self._base_env(), cwd=self.worktree, stdin=diff)

    def _shadow_env(self) -> dict[str, str]:
        env = self._base_env()
        env.update(
            {
                "GIT_DIR": str(self.shadow_dir),
                "GIT_WORK_TREE": str(self.worktree),
                "GIT_INDEX_FILE": str(self.shadow_dir / "patchmud-index"),
                "GIT_AUTHOR_DATE": _EPOCH_ZERO,
                "GIT_COMMITTER_DATE": _EPOCH_ZERO,
            }
        )
        return env

    @staticmethod
    def _base_env() -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GIT_AUTHOR_NAME": _FIXED_IDENTITY_NAME,
            "GIT_AUTHOR_EMAIL": _FIXED_IDENTITY_EMAIL,
            "GIT_COMMITTER_NAME": _FIXED_IDENTITY_NAME,
            "GIT_COMMITTER_EMAIL": _FIXED_IDENTITY_EMAIL,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        }

    def _run(self, args: list[str], env: dict[str, str], cwd: Path | None = None) -> str:
        res = self._run_raw(args, env=env, cwd=cwd)
        if res.returncode != 0:
            raise WorkspaceError(f"{' '.join(args)} 失敗：{res.stderr.strip()}")
        return res.stdout

    @staticmethod
    def _run_raw(
        args: list[str],
        env: dict[str, str],
        cwd: Path | None = None,
        stdin: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            env=env,
            cwd=str(cwd) if cwd else None,
            input=stdin,
            capture_output=True,
            text=True,
        )
