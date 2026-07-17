"""Task 3：Workspace patch stack、保護區、checkpoints（spec §7）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from patchmud.deck.materialize import materialize_repo
from patchmud.sandbox.workspace import Workspace

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"

_GIT_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@test.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@test.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], env=_GIT_ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def make_patch(worktree: Path, mutate) -> str:
    """在 worktree 的暫存複本上套 mutate，回傳對應 unified diff（含正確 context）。"""
    with tempfile.TemporaryDirectory() as td:
        clone = Path(td) / "clone"
        shutil.copytree(worktree, clone, ignore=shutil.ignore_patterns(".git"))
        _git(clone, "init", "--quiet")
        _git(clone, "add", "--all")
        _git(clone, "commit", "--quiet", "--no-verify", "-m", "base")
        mutate(clone)
        _git(clone, "add", "--all")
        return _git(clone, "diff", "--cached")


@pytest.fixture()
def ws(tmp_path: Path) -> Workspace:
    frozen = materialize_repo(FIXTURE, tmp_path / "wt")
    return Workspace(frozen=frozen, encounter_dir=FIXTURE, shadow_dir=tmp_path / "shadow")


def _append_lines(path: Path, n: int, tag: str) -> None:
    with path.open("a") as f:
        for i in range(n):
            f.write(f"# {tag} line {i}\n")


class TestApply:
    def test_production_patch_applies(self, ws: Workspace):
        diff = make_patch(ws.worktree, lambda c: _append_lines(c / "src/inventory.py", 3, "p1"))
        res = ws.apply_patch(diff, kind="production")
        assert res.applied and not res.rejected
        assert "p1 line 0" in (ws.worktree / "src/inventory.py").read_text()
        stats = ws.diff_stats()
        assert stats.added == 3 and stats.deleted == 0
        assert "src/inventory.py" in stats.files
        assert "p1 line 0" in ws.cumulative_diff()

    def test_strict_apply_rejects_context_mismatch(self, ws: Workspace):
        diff = make_patch(ws.worktree, lambda c: _append_lines(c / "src/inventory.py", 1, "x"))
        before = (ws.worktree / "src/inventory.py").read_bytes()
        # 竄改 context 行 → 嚴格模式必拒，且 worktree 不變（append 的 context 必含檔尾 def total）
        assert "def total" in diff
        broken = diff.replace("def total", "def totalX")
        res = ws.apply_patch(broken, kind="production")
        assert res.rejected and res.reason
        assert (ws.worktree / "src/inventory.py").read_bytes() == before

    def test_unparseable_patch_rejected(self, ws: Workspace):
        res = ws.apply_patch("this is not a diff", kind="production")
        assert res.rejected

    def test_test_patch_only_tests_agent(self, ws: Workspace):
        bad = make_patch(ws.worktree, lambda c: _append_lines(c / "src/inventory.py", 1, "t"))
        assert ws.apply_patch(bad, kind="test").rejected

        def add_agent_test(c: Path):
            (c / "tests/agent").mkdir(parents=True, exist_ok=True)
            (c / "tests/agent/test_new.py").write_text("def test_new():\n    assert True\n")

        good = make_patch(ws.worktree, add_agent_test)
        assert ws.apply_patch(good, kind="test").applied
        assert (ws.worktree / "tests/agent/test_new.py").exists()

    def test_production_patch_protected_rejected(self, ws: Workspace):
        for target in ("tests/public/test_remove_missing.py", "tests/starter/test_inventory_basics.py"):
            diff = make_patch(ws.worktree, lambda c, t=target: _append_lines(c / t, 1, "evil"))
            res = ws.apply_patch(diff, kind="production")
            assert res.rejected, target

        def add_benchmark(c: Path):
            (c / "benchmark").mkdir(exist_ok=True)
            (c / "benchmark/leak.txt").write_text("x\n")

        assert ws.apply_patch(make_patch(ws.worktree, add_benchmark), kind="production").rejected

    def test_dot_git_path_rejected(self, ws: Workspace):
        evil = (
            "diff --git a/.git/hooks/pwn b/.git/hooks/pwn\n"
            "new file mode 100644\n--- /dev/null\n+++ b/.git/hooks/pwn\n"
            "@@ -0,0 +1 @@\n+pwned\n"
        )
        assert ws.apply_patch(evil, kind="production").rejected
        assert not (ws.worktree / ".git/hooks/pwn").exists()

    def test_production_patch_to_harness_config_rejected(self, ws: Workspace):
        """harness 設定檔（pytest auto-load）是改測試過關的側門，production patch 必拒。

        root `conftest.py` 被 pytest 自動載入；`pytest.ini` / `pyproject.toml` /
        `tox.ini` / `setup.cfg` 的 addopts、`sitecustomize.py` 於 import 時執行——
        任一都能偽造 probe 判定，故 kind=production 一律拒收（spec §7 杜絕改測試過關）。
        """
        before = (ws.worktree / "conftest.py").read_bytes()
        tamper = make_patch(
            ws.worktree, lambda c: _append_lines(c / "conftest.py", 1, "hook")
        )
        res = ws.apply_patch(tamper, kind="production")
        assert res.rejected and res.reason
        assert (ws.worktree / "conftest.py").read_bytes() == before

        def add_config(name: str):
            def _mutate(c: Path):
                (c / name).write_text("[pytest]\naddopts = -p evil\n")

            return _mutate

        for name in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg", "sitecustomize.py"):
            diff = make_patch(ws.worktree, add_config(name))
            assert ws.apply_patch(diff, kind="production").rejected, name
            assert not (ws.worktree / name).exists(), name


class TestProtectedRestore:
    def test_restore_protected_from_deck_bytes(self, ws: Workspace):
        target = ws.worktree / "tests/public/test_remove_missing.py"
        original = (FIXTURE / "repo/tests/public/test_remove_missing.py").read_bytes()
        target.write_text("# polluted\n")
        stray = ws.worktree / "tests/public/injected.py"
        stray.write_text("# stray\n")
        ws.restore_protected()
        assert target.read_bytes() == original
        assert not stray.exists()


class TestRollback:
    def test_rollback_restores_previous_state(self, ws: Workspace):
        target = ws.worktree / "src/inventory.py"
        ws.apply_patch(make_patch(ws.worktree, lambda c: _append_lines(c / "src/inventory.py", 2, "p1")), kind="production")
        after_p1 = target.read_bytes()
        ws.apply_patch(make_patch(ws.worktree, lambda c: _append_lines(c / "src/inventory.py", 2, "p2")), kind="production")
        assert ws.rollback() is True
        assert target.read_bytes() == after_p1
        assert ws.rollback() is True  # 回到 frozen
        assert b"p1 line" not in target.read_bytes()
        assert ws.rollback() is False  # stack 已空

    def test_rollback_removes_new_files(self, ws: Workspace):
        def add_file(c: Path):
            (c / "src/extra.py").write_text("VALUE = 1\n")

        ws.apply_patch(make_patch(ws.worktree, add_file), kind="production")
        assert (ws.worktree / "src/extra.py").exists()
        ws.rollback()
        assert not (ws.worktree / "src/extra.py").exists()


class TestCheckpoint:
    def test_same_content_same_sha(self, ws: Workspace):
        c1 = ws.checkpoint()
        c2 = ws.checkpoint()
        assert c1 == c2
        ws.apply_patch(make_patch(ws.worktree, lambda c: _append_lines(c / "src/inventory.py", 1, "p")), kind="production")
        c3 = ws.checkpoint()
        assert c3 != c1

    def test_checkpoint_shas_deterministic_across_workspaces(self, tmp_path: Path):
        shas = []
        for name in ("a", "b"):
            frozen = materialize_repo(FIXTURE, tmp_path / name / "wt")
            w = Workspace(frozen=frozen, encounter_dir=FIXTURE, shadow_dir=tmp_path / name / "shadow")
            shas.append(w.checkpoint())
        assert shas[0] == shas[1]


class TestRevertedLoc:
    def test_reverting_own_added_lines_counted(self, ws: Workspace):
        def add_block(c: Path):
            (c / "src/extra.py").write_text("".join(f"LINE_{i} = {i}\n" for i in range(12)))

        ws.apply_patch(make_patch(ws.worktree, add_block), kind="production")

        def shrink(c: Path):
            (c / "src/extra.py").write_text("LINE_0 = 0\n")

        ws.apply_patch(make_patch(ws.worktree, shrink), kind="production")
        assert ws.diff_stats().reverted_loc == 11

    def test_removing_frozen_lines_not_reverted(self, ws: Workspace):
        def strip(c: Path):
            p = c / "src/inventory.py"
            lines = p.read_text().splitlines(keepends=True)
            p.write_text("".join(lines[:-2]))

        ws.apply_patch(make_patch(ws.worktree, strip), kind="production")
        assert ws.diff_stats().reverted_loc == 0


class TestRelativeShadowDir:
    """相對 shadow_dir（如 `patchmud play --runs-root runs`）不得破壞 checkpoint。

    checkpoint() 以 cwd=worktree（frozen tempdir）執行 git，GIT_DIR 若為相對路徑
    會被 git 相對 cwd 解析 → 找不到 shadow repo（`not a git repository`）。
    """

    def test_relative_shadow_dir_resolved_and_checkpoints(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        frozen = materialize_repo(FIXTURE, tmp_path / "wt")  # worktree 為絕對 tempdir
        ws = Workspace(
            frozen=frozen,
            encounter_dir=FIXTURE,
            shadow_dir=Path("relruns/run-x/checkpoints"),  # 相對於 cwd
        )
        assert ws.shadow_dir.is_absolute()
        sha = ws.checkpoint()  # bug 時會 fatal: not a git repository
        assert sha
        # 套 patch 後 checkpoint 仍可推進
        diff = make_patch(ws.worktree, lambda c: _append_lines(c / "src/inventory.py", 1, "p"))
        ws.apply_patch(diff, kind="production")
        assert ws.checkpoint() != sha
