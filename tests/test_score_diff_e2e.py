"""Task 8 RED：Milestone A 收口——離線評分 walking skeleton（spec §5.2、§9、§12.1）。

`patchmud score-diff --encounter <dir> --diff <file>` e2e 鎖定：
- 對 `mini_encounter` 跑 reference patch → `Clear` 前置條件（critical 綠）成立、
  `result.yaml` 落盤（PowerReport、gates、probe outcomes）、`archive_private` 可產出；
- 跑空 diff → critical 紅、Economy 欄位 `NA`（card 無 reference_cost；NA 不得變 0）。

真 bwrap e2e（環境無能力時 skip，比照 evaluator / isolate 整合測試）；
probe 執行的唯一 seam 是 IsolationRunner（plan invariant 3）。
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest
import yaml

from patchmud.cli import main
from patchmud.sandbox.isolate import IsolationRunner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_encounter"
REFERENCE_DIFF = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")

MAIN_PROBE = "tests/public/test_remove_missing.py"
HIDDEN_PROBE = "hidden/test_cr1_no_negative_stock.py"

_BWRAP = Path("/usr/bin/bwrap")


@pytest.fixture()
def real_capabilities():
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    probe = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


def _score(tmp_path: Path, diff_text: str, run_id: str) -> tuple[int, Path]:
    """以 CLI main() 跑 score-diff；回傳 (exit code, runs_root)。"""
    diff_file = tmp_path / f"{run_id}.diff"
    diff_file.write_text(diff_text, encoding="utf-8")
    runs_root = tmp_path / "runs"
    rc = main(
        [
            "score-diff",
            "--encounter",
            str(FIXTURE),
            "--diff",
            str(diff_file),
            "--runs-root",
            str(runs_root),
            "--run-id",
            run_id,
        ]
    )
    return rc, runs_root


def _load_result(runs_root: Path, run_id: str) -> dict:
    path = runs_root / run_id / "result.yaml"
    assert path.is_file(), "result.yaml 必須落盤於 run 目錄"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestReferencePatch:
    def test_clear_preconditions_result_and_archive(self, real_capabilities, tmp_path):
        rc, runs_root = _score(tmp_path, REFERENCE_DIFF, "e2e-ref")
        assert rc == 0

        result = _load_result(runs_root, "e2e-ref")

        # Clear 前置條件（critical 綠）成立；§5.2 唯一公式三條件齊備 → Clear=1
        assert result["gates"]["critical_pass"] is True
        assert result["main_public_green"] is True
        assert result["protocol_failed"] is False
        assert result["clear"] == 1

        # PowerReport 與 probe outcomes 完整落盤
        assert result["power"]["total"] >= 60
        assert result["gates"]["power_cap"] is None
        assert result["probes"]["evaluator"][HIDDEN_PROBE]["status"] == "passed"
        assert result["probes"]["public"][MAIN_PROBE]["status"] == "passed"

        # archive_private 可產出：run 目錄 + evaluator bundle（§12.1）
        archive = runs_root / "e2e-ref-private.tar"
        assert archive.is_file()
        with tarfile.open(archive) as tar:
            names = set(tar.getnames())
        assert "e2e-ref/result.yaml" in names
        assert "e2e-ref/run.yaml" in names
        assert "e2e-ref/evaluator_bundle/manifest.yaml" in names
        assert f"e2e-ref/evaluator_bundle/{HIDDEN_PROBE}" in names


class TestEmptyDiff:
    def test_critical_red_and_economy_na(self, real_capabilities, tmp_path):
        rc, runs_root = _score(tmp_path, "", "e2e-empty")
        assert rc == 0

        result = _load_result(runs_root, "e2e-empty")

        # 空 diff = baseline：critical 紅、MAIN 紅 → Clear=0
        assert result["gates"]["critical_pass"] is False
        assert result["clear"] == 0
        assert result["probes"]["public"][MAIN_PROBE]["status"] == "failed"

        # 無 reference_cost → Economy `NA`（不可記 0，§10.1 NA 原則）
        assert result["economy"] == "NA"
