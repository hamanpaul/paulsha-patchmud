"""encounter fixture 物化為 frozen git repo（spec §4.1）。

只物化 `repo/`；hidden 資產（hidden/**）永不進 worktree。
固定 author 與 epoch 0 timestamp，因此同一 deck 版本的 frozen SHA 恆定。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from patchmud.deck.model import DeckError

__all__ = ["FrozenRepo", "materialize_repo"]

_FIXED_IDENTITY_NAME = "PatchMUD Deck"
_FIXED_IDENTITY_EMAIL = "deck@patchmud.invalid"
_EPOCH_ZERO = "1970-01-01T00:00:00+00:00"
_COMMIT_MESSAGE = "deck: frozen fixture repo"

# 物化排除：快取類產物會破壞 tree SHA 的決定性
_COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")


@dataclass(frozen=True)
class FrozenRepo:
    """物化完成的 fixture repo：dest 路徑 + frozen commit SHA。"""

    path: Path
    sha: str


def materialize_repo(encounter_dir: Path, dest: Path) -> FrozenRepo:
    """把 encounter 的 repo/ 物化成單一 initial commit 的 git repo。"""
    encounter_dir = Path(encounter_dir)
    dest = Path(dest)

    src = encounter_dir / "repo"
    if not src.is_dir():
        raise DeckError(f"encounter 缺 repo/ 目錄：{encounter_dir}")
    if dest.exists() and any(dest.iterdir()):
        raise DeckError(f"物化目的地必須是不存在或空目錄：{dest}")

    shutil.copytree(src, dest, ignore=_COPY_IGNORE, dirs_exist_ok=True)

    env = _deterministic_git_env()
    _git(dest, env, "init", "--quiet")
    _git(dest, env, "add", "--all")
    _git(dest, env, "commit", "--quiet", "--no-verify", "-m", _COMMIT_MESSAGE)
    sha = _git(dest, env, "rev-parse", "HEAD")
    return FrozenRepo(path=dest, sha=sha)


def _deterministic_git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_AUTHOR_NAME": _FIXED_IDENTITY_NAME,
        "GIT_AUTHOR_EMAIL": _FIXED_IDENTITY_EMAIL,
        "GIT_AUTHOR_DATE": _EPOCH_ZERO,
        "GIT_COMMITTER_NAME": _FIXED_IDENTITY_NAME,
        "GIT_COMMITTER_EMAIL": _FIXED_IDENTITY_EMAIL,
        "GIT_COMMITTER_DATE": _EPOCH_ZERO,
        # 隔絕使用者/系統 git 設定（gpgsign、hooks 模板等）確保決定性
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "init.defaultBranch=main", *args],
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DeckError(
            f"git {' '.join(args)} 失敗（exit {result.returncode}）：{result.stderr.strip()}"
        )
    return result.stdout.strip()
