"""Task 13 e2e：`patchmud run` milestone B 收口（spec §5、§6；plan Task 13 Step 4）。

`mini_encounter` + scripted「兩回合修好」劇本（turn 1 PATCH reference diff、
turn 2 COMMIT）：Clear=1、ledger 有 entries、result.yaml 完整、每 turn 恰一筆
turn event。真 bwrap e2e（環境無能力時 skip，比照 score-diff e2e）；probe 執行
唯一 seam 是 IsolationRunner（plan invariant 3）。
"""

from __future__ import annotations

import json
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

PATCH_REPLY = (
    "ACTION: PATCH\n"
    "TARGET_ISSUES: MAIN-1\n"
    "CLAIM: 修 remove 的缺貨與缺項防護\n"
    "PATCH:\n" + REFERENCE_DIFF
)
COMMIT_REPLY = "ACTION: COMMIT"


@pytest.fixture()
def real_capabilities():
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    probe = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


class TestTwoTurnFix:
    def test_scripted_run_clears(self, real_capabilities, tmp_path) -> None:
        script = tmp_path / "script.txt"
        script.write_text(
            PATCH_REPLY + "\n-----\n" + COMMIT_REPLY + "\n", encoding="utf-8"
        )
        runs_root = tmp_path / "runs"

        rc = main(
            [
                "run",
                str(FIXTURE),  # encounter 為 positional（可為名字或路徑）
                "--model",
                f"scripted:{script}",
                "--loadout",
                "P0T0R0",
                "--runs-root",
                str(runs_root),
                "--run-id",
                "e2e-run",
            ]
        )
        assert rc == 0

        run_dir = runs_root / "e2e-run"
        result = yaml.safe_load((run_dir / "result.yaml").read_text(encoding="utf-8"))

        # 兩回合修好：Clear=1（critical 綠 + MAIN 綠 + 非 failed:protocol）
        assert result["clear"] == 1
        assert result["end_reason"] == "commit"
        assert result["protocol_failed"] is False
        assert result["turns"] == 2
        assert result["loadout"] == "P0T0R0"
        assert result["gates"]["critical_pass"] is True
        assert result["main_public_green"] is True
        assert result["power"]["total"] >= 60
        assert result["probes"]["public"][MAIN_PROBE]["status"] == "passed"
        assert result["probes"]["evaluator"][HIDDEN_PROBE]["status"] == "passed"
        # Economy 無 pricing / reference_cost → NA（不得記 0，§10.1）
        assert result["economy"] == "NA"

        # ledger 有 entries：兩次作者呼叫、billed totals 落盤
        assert result["ledger"]["entries"] == 2
        ledger_lines = [
            json.loads(line)
            for line in (run_dir / "ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(ledger_lines) == 2
        assert all(entry["role"] == "author" for entry in ledger_lines)
        assert all(entry["billed_input_total"] > 0 for entry in ledger_lines)

        # event log：baseline + 每 turn 恰一筆 turn event + final
        events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        types = [e["type"] for e in events]
        assert types[0] == "baseline"
        assert types.count("turn") == 2
        assert types[-1] == "final"
        turn_events = [e for e in events if e["type"] == "turn"]
        assert all(e["checkpoint"] for e in turn_events)
        # turn 1 PATCH 後 MAIN 轉綠 → queue 清空
        assert turn_events[0]["queue"]["b_t"] == 0

        # run.yaml 記 harness_prompt_version（含 render pack 版本，§5.3–5.4）
        run_record = yaml.safe_load(
            (run_dir / "run.yaml").read_text(encoding="utf-8")
        )
        assert "render-zh-TW" in run_record["harness_prompt_version"]
