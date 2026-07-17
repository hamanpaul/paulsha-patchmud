"""`patchmud validate-deck` e2e（spec §4.2；plan Task 20 acceptance gate）。

鎖定：對一個結構正確的 encounter（`mini_encounter` 的獨立複本，避免污染
共用 fixture）——schema、probe 檔案存在、baseline（MAIN 紅、regression/
compat 綠）、套用 `hidden/reference.patch` 後 public probes 全綠、hidden
evaluator critical gate 成立且全部 rubric/critical probe 綠、量測後覆寫
`reference_timings.yaml`——全部通過；deck 目錄缺 encounter、encounter 缺
`hidden/reference.patch`、probe 檔案缺漏 三種操作性錯誤各自 fail-closed
（該 encounter 標記不過，不中止整份報告）。

真 bwrap e2e（環境無能力時 skip，比照 score-diff / evaluator 整合測試）；
probe 執行的唯一 seam 是 IsolationRunner（plan invariant 3）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from patchmud.cli import DeckValidationError, main, validate_deck
from patchmud.sandbox.isolate import IsolationRunner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_encounter"

_BWRAP = Path("/usr/bin/bwrap")


@pytest.fixture()
def real_capabilities():
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    probe = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


def _copy_encounter(tmp_path: Path, name: str = "mini_encounter") -> Path:
    """複製 mini_encounter fixture 到 tmp_path，避免測試寫回共用 fixture。"""
    dest = tmp_path / "deck" / name
    shutil.copytree(FIXTURE, dest)
    return dest


class TestWellFormedEncounter:
    def test_all_checks_pass_and_timings_measured(self, real_capabilities, tmp_path):
        encounter_dir = _copy_encounter(tmp_path)
        deck_dir = encounter_dir.parent

        report = validate_deck(deck_dir, write_timings=True)

        assert report.all_passed is True
        assert len(report.encounters) == 1
        result = report.encounters[0]
        assert result.encounter_id == "mini_encounter"
        assert result.error is None
        assert "hidden_probes_green_under_reference" in result.checks
        assert "reference_timings_measured" in result.checks

        # deck CI 產物：以量測所得 wall_ms 覆寫（僅供 evaluator 使用，§4.2）
        timings = yaml.safe_load(
            (encounter_dir / "hidden" / "reference_timings.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert timings["schema_version"] == 1
        assert "hidden/test_cr1_no_negative_stock.py" in timings["timings_ms"]
        assert timings["timings_ms"]["hidden/test_cr1_no_negative_stock.py"] >= 0

    def test_no_write_timings_leaves_file_untouched(self, real_capabilities, tmp_path):
        encounter_dir = _copy_encounter(tmp_path)
        deck_dir = encounter_dir.parent
        original = (encounter_dir / "hidden" / "reference_timings.yaml").read_text(
            encoding="utf-8"
        )

        report = validate_deck(deck_dir, write_timings=False)

        assert report.all_passed is True
        result = report.encounters[0]
        assert "reference_timings_measured" not in result.checks
        assert (
            encounter_dir / "hidden" / "reference_timings.yaml"
        ).read_text(encoding="utf-8") == original

    def test_cli_exit_zero(self, real_capabilities, tmp_path):
        encounter_dir = _copy_encounter(tmp_path)
        rc = main(["validate-deck", str(encounter_dir.parent), "--no-write-timings"])
        assert rc == 0


class TestBrokenEncounters:
    def test_missing_reference_patch_fails_closed(self, real_capabilities, tmp_path):
        encounter_dir = _copy_encounter(tmp_path)
        (encounter_dir / "hidden" / "reference.patch").unlink()

        report = validate_deck(encounter_dir.parent, write_timings=False)

        assert report.all_passed is False
        result = report.encounters[0]
        assert result.passed is False
        assert "reference.patch" in result.error

    def test_missing_probe_file_fails_closed(self, real_capabilities, tmp_path):
        encounter_dir = _copy_encounter(tmp_path)
        (encounter_dir / "hidden" / "test_cr1_no_negative_stock.py").unlink()

        report = validate_deck(encounter_dir.parent, write_timings=False)

        result = report.encounters[0]
        assert result.passed is False
        assert result.checks == ("schema", "provenance")
        assert "probe" in result.error

    def test_bugfix_already_applied_fails_baseline(self, real_capabilities, tmp_path):
        """baseline 若已經是修好的版本（issue 不可重現）→ MAIN 未紅 → fail-closed。"""
        encounter_dir = _copy_encounter(tmp_path)
        reference = (encounter_dir / "hidden" / "reference.patch").read_text(
            encoding="utf-8"
        )
        inventory = encounter_dir / "repo" / "src" / "inventory.py"
        text = inventory.read_text(encoding="utf-8")
        # 手動套用 reference.patch 對應的修法到 repo/（baseline 不再重現 issue）
        assert "current - qty" in text
        fixed = text.replace(
            "        current = self._items.get(name, 0)\n"
            "        self._items[name] = current - qty\n",
            "        if name not in self._items:\n"
            "            raise KeyError(name)\n"
            "        if qty > self._items[name]:\n"
            "            raise ValueError('qty exceeds stock')\n"
            "        self._items[name] = self._items[name] - qty\n",
        )
        assert fixed != text
        inventory.write_text(fixed, encoding="utf-8")
        assert reference  # sanity：patch 檔仍在（只是現在會 apply 失敗，非本測試重點）

        report = validate_deck(encounter_dir.parent, write_timings=False)

        result = report.encounters[0]
        assert result.passed is False
        assert result.checks == ("schema", "provenance", "probe_files_exist", "materialize")
        assert "MAIN probe 必須為 failed" in (result.error or "")

    def test_empty_deck_dir_raises(self, real_capabilities, tmp_path):
        empty = tmp_path / "empty-deck"
        empty.mkdir()
        with pytest.raises(DeckValidationError):
            validate_deck(empty)
