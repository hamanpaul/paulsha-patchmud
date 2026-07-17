"""F3/F4 回歸：deck path 隔離（symlink 逃逸、`..` 路徑穿越）。

codex gpt-5.6-sol 對抗審查 findings（docs/superpowers/reviews/2026-07-17-...）：
- F3：`materialize_repo` 預設解 symlink，`repo/x -> ../hidden/ref.patch` 會把
  hidden bytes 複製進 candidate 可讀的 worktree。
- F4：public/hidden disjoint 只用 `startswith("hidden/")`，未正規化 `..`，
  `hidden/../repo/tests/public/x.py` 可偽裝成 critical hidden probe。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from patchmud.deck.loader import DeckError, load_card
from patchmud.deck.materialize import materialize_repo

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"


def _clone_encounter(dst: Path) -> Path:
    shutil.copytree(FIXTURE, dst)
    return dst


class TestMaterializeSymlinkEscape:
    def test_symlink_into_hidden_rejected(self, tmp_path: Path) -> None:
        enc = _clone_encounter(tmp_path / "enc")
        # repo/answer.txt -> ../hidden/reference.patch（逃逸到 hidden 資產）
        (enc / "repo" / "answer.txt").symlink_to(Path("../hidden/reference.patch"))
        with pytest.raises(DeckError):
            materialize_repo(enc, tmp_path / "wt")

    def test_symlink_absolute_rejected(self, tmp_path: Path) -> None:
        enc = _clone_encounter(tmp_path / "enc")
        (enc / "repo" / "leak.txt").symlink_to(enc / "hidden" / "reference.patch")
        with pytest.raises(DeckError):
            materialize_repo(enc, tmp_path / "wt")

    def test_clean_repo_still_materializes(self, tmp_path: Path) -> None:
        enc = _clone_encounter(tmp_path / "enc")
        frozen = materialize_repo(enc, tmp_path / "wt")
        assert frozen.sha
        assert not (frozen.path / "hidden").exists()


class TestPathTraversalInCard:
    def _write_card(self, enc: Path, mutate) -> Path:
        card_path = enc / "card.yaml"
        data = yaml.safe_load(card_path.read_text(encoding="utf-8"))
        mutate(data)
        card_path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return card_path

    def test_hidden_probe_with_dotdot_rejected(self, tmp_path: Path) -> None:
        enc = _clone_encounter(tmp_path / "enc")

        def mutate(data: dict) -> None:
            # 偽裝：以 hidden/.. 穿越回 public test 當 critical hidden probe
            data["critical_requirements"][0]["hidden_probe"] = (
                "hidden/../repo/tests/public/test_remove_missing.py"
            )

        card_path = self._write_card(enc, mutate)
        with pytest.raises(DeckError):
            load_card(card_path)

    def test_public_probe_with_dotdot_rejected(self, tmp_path: Path) -> None:
        enc = _clone_encounter(tmp_path / "enc")

        def mutate(data: dict) -> None:
            data["public_requirements"][0]["probe"] = "tests/public/../../hidden/test_cr1_no_negative_stock.py"

        card_path = self._write_card(enc, mutate)
        with pytest.raises(DeckError):
            load_card(card_path)

    def test_absolute_probe_rejected(self, tmp_path: Path) -> None:
        enc = _clone_encounter(tmp_path / "enc")

        def mutate(data: dict) -> None:
            data["public_requirements"][0]["probe"] = "/etc/passwd"

        card_path = self._write_card(enc, mutate)
        with pytest.raises(DeckError):
            load_card(card_path)

    def test_clean_card_still_loads(self, tmp_path: Path) -> None:
        enc = _clone_encounter(tmp_path / "enc")
        card = load_card(enc / "card.yaml")
        assert card.issue_id
