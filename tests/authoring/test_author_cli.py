"""Part B Task B3 e2e：author-encounter 產出可玩、可驗證的 deck（真 bwrap）。

用內建示範 source（docs/authoring/examples/discount-clamp.source.yaml）實跑
出題：品質閘走與 validate-deck 相同的隔離 CI——baseline MAIN 紅（bug 可重現）、
套 reference.patch 後 public/hidden 全綠。namespace 能力不足則 skip。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from patchmud.cli import main
from patchmud.deck.loader import load_card
from patchmud.sandbox.isolate import IsolationRunner

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "authoring" / "examples" / "discount-clamp.source.yaml"
_BWRAP = Path("/usr/bin/bwrap")


@pytest.fixture()
def real_capabilities():
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    probe = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


class TestAuthorEncounterE2E:
    def test_author_produces_validatable_deck(self, real_capabilities, tmp_path):
        deck_root = tmp_path / "deck"
        rc = main(["author-encounter", str(EXAMPLE), "--into", str(deck_root)])
        assert rc == 0

        enc = deck_root / "discount-clamp-v1"
        assert (enc / "card.yaml").is_file()
        assert (enc / "hidden" / "reference.patch").is_file()
        assert (enc / "hidden" / "reference_timings.yaml").is_file()  # 品質閘量測產物
        card = load_card(enc / "card.yaml")
        assert card.briefing  # 白話 briefing 已寫入

        # 產出的關能被 validate-deck 獨立接受（再跑一次同一套 CI）
        assert main(["validate-deck", str(deck_root), "--no-write-timings"]) == 0

    def test_author_rejects_when_already_exists(self, real_capabilities, tmp_path):
        deck_root = tmp_path / "deck"
        assert main(["author-encounter", str(EXAMPLE), "--into", str(deck_root)]) == 0
        # 第二次同 issue_id → 拒絕覆寫，非 0
        assert main(["author-encounter", str(EXAMPLE), "--into", str(deck_root)]) == 2
