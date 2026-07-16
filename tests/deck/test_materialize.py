"""Task 1 RED：deterministic fixture 物化（spec §4.1）。

鎖定：materialize_repo 兩次呼叫產生相同 commit SHA（固定 author、epoch 0、
單一 initial commit）；dest 內不存在 hidden/ 任何檔案或 bytes。
"""

import subprocess
from pathlib import Path

from patchmud.deck.materialize import FrozenRepo, materialize_repo

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_materialize_twice_same_commit_sha(tmp_path):
    a = materialize_repo(FIXTURE, tmp_path / "a")
    b = materialize_repo(FIXTURE, tmp_path / "b")
    assert isinstance(a, FrozenRepo)
    assert a.sha == b.sha
    assert len(a.sha) == 40
    assert a.path == tmp_path / "a"
    assert a.sha == _git(tmp_path / "a", "rev-parse", "HEAD")


def test_materialize_fixed_author_epoch_zero_single_commit(tmp_path):
    materialize_repo(FIXTURE, tmp_path / "wt")
    author_ts, committer_ts = _git(
        tmp_path / "wt", "log", "-1", "--format=%at %ct"
    ).split()
    assert author_ts == "0"
    assert committer_ts == "0"
    assert _git(tmp_path / "wt", "rev-list", "--count", "HEAD") == "1"


def test_materialize_excludes_hidden_assets(tmp_path):
    dest = tmp_path / "wt"
    materialize_repo(FIXTURE, dest)

    assert not (dest / "hidden").exists()

    worktree_files = [
        p for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts
    ]
    names = {p.name for p in worktree_files}
    assert "reference.patch" not in names
    assert "reference_timings.yaml" not in names
    assert "test_cr1_no_negative_stock.py" not in names

    hidden_probe_bytes = (
        FIXTURE / "hidden" / "test_cr1_no_negative_stock.py"
    ).read_bytes()
    for path in worktree_files:
        assert hidden_probe_bytes not in path.read_bytes()

    # repo/ 的內容必須完整到位
    assert (dest / "src" / "inventory.py").is_file()
    assert (dest / "tests" / "starter" / "test_inventory_basics.py").is_file()
    assert (dest / "tests" / "public" / "test_remove_missing.py").is_file()
