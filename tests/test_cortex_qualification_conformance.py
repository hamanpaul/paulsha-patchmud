"""paulsha-cortex#842 e2e：PatchMUD 產物交給 Cortex 真實的 qualification 驗證程式。

流程與 #842 付費驗收相同，只把 codex 子行程與 app-server 換成離線假件：
真 bwrap 對局（單關 deck → coverage complete）→ ``patchmud report`` 產 v2 →
``patchmud profile-binding`` 產 binding → 在暫存 store 以 Cortex
``QualificationStore.import_report`` 建 candidate，並以 Cortex 自己的
``_candidate_approval_blockers`` 確認沒有任何 approve blocker。

Cortex 不是 PatchMUD 的 runtime 依賴：設定 ``PATCHMUD_CORTEX_SRC``（Cortex
source 根目錄）才執行，Cortex 程式在子行程載入，不污染本測試行程。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from patchmud.cli import main
from patchmud.sandbox.isolate import IsolationRunner

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_encounter"
REFERENCE_DIFF = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")
_BWRAP = Path("/usr/bin/bwrap")
CORTEX_SRC = os.environ.get("PATCHMUD_CORTEX_SRC", "").strip()

PATCH_REPLY = (
    "ACTION: PATCH\n"
    "TARGET_ISSUES: MAIN-1\n"
    "CLAIM: 修 remove 的缺貨與缺項防護\n"
    "PATCH:\n" + REFERENCE_DIFF
)
COMMIT_REPLY = "ACTION: COMMIT"
USAGE = {
    "input_tokens": 1200,
    "cached_input_tokens": 200,
    "cache_write_input_tokens": 0,
    "output_tokens": 40,
    "reasoning_output_tokens": 10,
}

_CORTEX_SCRIPT = r"""
import hashlib, json, sys
from pathlib import Path
from paulsha_cortex.coordinator.qualification_lifecycle import (
    QualificationStore, _candidate_approval_blockers,
)
report_path, binding_path, store_root, profile_key = sys.argv[1:5]
report_bytes = Path(report_path).read_bytes()
binding_bytes = Path(binding_path).read_bytes()
kwargs = dict(
    source_revision="0123456789abcdef0123456789abcdef01234567",
    source_artifact_digest="sha256:" + hashlib.sha256(report_bytes).hexdigest(),
    profile_key=profile_key,
    executor="codex",
    model_id="gpt-6-luna",
    role="build",
    profile_binding=json.loads(binding_bytes),
    profile_source_revision=None,
    profile_source_artifact_digest="sha256:" + hashlib.sha256(binding_bytes).hexdigest(),
)
store = QualificationStore(root=store_root)
report = json.loads(report_bytes)
candidate = store._candidate_from_report(report, test_only=False, **kwargs)
result = store.import_report(
    report, expected_revision=0, idempotency_key="patchmud-conformance", **kwargs
)
print(json.dumps({
    "result": result,
    "blockers": _candidate_approval_blockers(candidate),
    "coverage": candidate["coverage"]["state"],
    "observation": candidate["profile_observation"]["state"],
    "actual_key": candidate["profile_observation"]["actual_key"],
    "verdict": candidate["measurement"]["verdict"],
}))
"""


@pytest.fixture()
def real_capabilities():
    if not CORTEX_SRC:
        pytest.skip("未設定 PATCHMUD_CORTEX_SRC（Cortex source 根目錄）")
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    caps = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP).capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


@pytest.fixture()
def offline_codex(monkeypatch):
    """codex exec 與 app-server 換成離線假件；其餘 registry／profile 路徑照正式程式。"""
    import patchmud.adapters.cli_base as cli_base
    import patchmud.adapters.codex_cli as codex_cli
    import patchmud.adapters.profile as profile_module
    import patchmud.cli as cli

    replies = [PATCH_REPLY, COMMIT_REPLY]
    threads: dict[str, dict] = {}

    def fake_runner(argv: list[str]) -> str:
        assert "--ephemeral" not in argv
        effort = argv[argv.index("-c") + 1].removeprefix("model_reasoning_effort=")
        thread_id = f"offline-thread-{len(threads) + 1:04d}"
        threads[thread_id] = {
            "id": thread_id,
            "model": argv[argv.index("-m") + 1],
            "reasoningEffort": effort,
            "modelProvider": "openai",
        }
        events = [
            {"type": "thread.started", "thread_id": thread_id},
            {"type": "item.completed", "item": {"type": "agent_message", "text": replies.pop(0)}},
            {"type": "turn.completed", "usage": USAGE},
        ]
        return "\n".join(json.dumps(event) for event in events) + "\n"

    monkeypatch.setattr(cli_base, "build_subprocess_runner", lambda *_a, **_k: fake_runner)
    monkeypatch.setattr(
        codex_cli, "build_app_server_thread_reader", lambda *_a, **_k: threads.__getitem__
    )
    monkeypatch.setattr(
        profile_module,
        "_runtime_toolchain",
        lambda _registration: {"id": "codex-cli", "version": "sha256:" + "b" * 64},
    )
    monkeypatch.setattr(cli, "has_codex_cli", lambda: True)
    return threads


def test_patchmud_artifacts_pass_cortex_import_without_approve_blockers(
    real_capabilities, offline_codex, tmp_path
):
    deck = tmp_path / "deck-single" / "mini_encounter"
    shutil.copytree(FIXTURE, deck)
    runs_root = tmp_path / "runs"

    assert main(
        [
            "run", str(deck),
            "--model", "codex:gpt-6-luna",
            "--effort", "max",
            "--loadout", "P0T0R0",
            "--runs-root", str(runs_root),
            "--run-id", "conformance-run",
        ]
    ) == 0
    assert len(offline_codex) == 2  # 兩回合各一個 provider thread

    run_dir = runs_root / "conformance-run"
    post_run = json.loads((run_dir / "execution_profile.json").read_text(encoding="utf-8"))
    assert post_run["actual_condition_key"].startswith("epk:v1:actual:")

    report_dir = tmp_path / "report"
    assert main(["report", "--runs", str(runs_root / "*"), "--out", str(report_dir)]) == 0
    binding_path = tmp_path / "execution-profile.json"
    assert main(["profile-binding", str(run_dir), "--out", str(binding_path)]) == 0

    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    rows = report["leaderboards"]["clear_rate"]["rows"]
    assert [row["model"] for row in rows] == ["codex:gpt-6-luna"]
    profile_key = rows[0]["profile_id"]
    assert profile_key == post_run["profile_id"]

    store_root = tmp_path / "qualification-store"
    completed = subprocess.run(
        [
            os.environ.get("PATCHMUD_CORTEX_PYTHON", sys.executable),
            "-c",
            _CORTEX_SCRIPT,
            str(report_dir / "report.json"),
            str(binding_path),
            str(store_root),
            profile_key,
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": CORTEX_SRC},
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    outcome = json.loads(completed.stdout)
    assert outcome["blockers"] == []
    assert outcome["coverage"] == "complete"
    assert outcome["observation"] == "complete"
    assert outcome["actual_key"] == post_run["actual_condition_key"]
    assert outcome["verdict"] == "pass"
    assert outcome["result"]["revision"] == 1
    assert outcome["result"]["candidate_id"].startswith("qcan:v1:")
