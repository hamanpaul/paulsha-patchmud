"""paulsha-cortex#842：builder lane 與 Cortex 真實程式的一致性證明。

設定 ``PATCHMUD_CORTEX_SRC``（Cortex source 根目錄；CI checkout vendored 資料釘住的
revision）才執行。Cortex 程式一律在**子行程**載入，環境以 ``env -i`` 的精神重建：
``HOME`` 與所有 ``PSC_*`` root 指向 pytest 暫存目錄，絕不讀寫任何真實 Cortex 狀態。

1. key 相等：同一份 target 輸入，PatchMUD 產出的 resolved／request key 等於 Cortex
   ``SubprocessLauncher`` → ``_specialize_workflow_launcher`` →
   ``_bind_workflow_execution_profile``（Manager 派工當下的計算）與
   ``make_launcher_profile`` 算出的 key，也等於 target 檔記錄的 key。
2. 發射契約：codex argv 逐 token 等於 Cortex ``build_codex_argv``；persona 前言等於
   ``render_contract_prompt("builder")``；target 的卡片欄位等於 Cortex ``cards.yaml``。
3. 端到端：builder lane（離線假 codex、真 bwrap 評分）→ report → profile-binding →
   Cortex ``import_report``（blockers 為空）→ operator receipt approve →
   ``_bind_workflow_execution_profile(qualification_policy="enforce")`` 放行；revoke 後同一
   查詢被擋下。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from patchmud.cli import build_profile_binding, main
from patchmud.cortex_dispatch.codex_argv import build_cortex_builder_codex_argv
from patchmud.cortex_dispatch.lane import run_dispatch_lane
from patchmud.cortex_dispatch.persona import load_persona_contract, render_contract_prompt
from patchmud.cortex_dispatch.profile import build_dispatch_profile_record, resolve_launch_contract
from patchmud.cortex_dispatch.target import load_dispatch_target
from tests.cortex_dispatch.test_lane_e2e import FakeCodex, _config, _deck, real_capabilities  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "fixtures" / "cortex-dispatch" / "small-fix-subagent-build-codex-gpt-6-luna-green.json"
CORTEX_SRC = os.environ.get("PATCHMUD_CORTEX_SRC", "").strip()

_COMMON = r"""
import json, os, sys
from pathlib import Path
from types import SimpleNamespace

def setup(target):
    config_root = Path(os.environ["PSC_PROJECT_CONFIG_ROOT"])
    config_root.mkdir(parents=True, exist_ok=True)
    ident = target["identity"]
    # 與 qualification/contract.py render_model_identity_overlay() 同形（block YAML，純量用 JSON 字面值）。
    overlay = "\n".join([
        "schema_version: 3",
        "resolution_policy:",
        '  packaged_fallback: "deny"',
        "identities:",
        f"  - executor: {json.dumps(ident['executor'])}",
        f"    model_id: {json.dumps(ident['model_id'])}",
        f"    independence_domain: {json.dumps(ident['independence_domain'])}",
        f"    capabilities: {json.dumps(list(ident['capabilities']))}",
    ]) + "\n"
    (config_root / "model-identities.yaml").write_text(overlay, encoding="utf-8")
    from paulsha_cortex.coordinator.model_identities import load_model_identities
    identity = load_model_identities(str(config_root)).require(ident["executor"], ident["model_id"])
    card = target["card"]
    step = SimpleNamespace(
        persona=target["persona"], card=card["id"], phase=card["phase"],
        commit_policy=card["commit_policy"], test_policy=card["test_policy"],
        outputs=tuple(card.get("declared_outputs", ())),
    )
    spec = target["run"]
    run = SimpleNamespace(
        steps=[SimpleNamespace(**row) for row in spec["prior_steps"]],
        model_chain_override=spec["model_chain_override"],
        sizing_band=spec["sizing_band"],
    )
    return identity, step, run

def dispatch_launcher(identity, step):
    from paulsha_cortex.coordinator import manager
    from paulsha_cortex.coordinator.launcher import SubprocessLauncher
    launcher = SubprocessLauncher(executor=identity.executor, allow_unsafe=False, model=identity.model_id)
    return manager._specialize_workflow_launcher(launcher, step)
"""

_KEY_SCRIPT = _COMMON + r"""
target = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
worktree = sys.argv[2]
identity, step, run = setup(target)
from paulsha_cortex.coordinator import execution_adapters, manager
from paulsha_cortex.coordinator.launcher import build_codex_argv
from paulsha_cortex.persona.render import render_contract_prompt
launcher = dispatch_launcher(identity, step)
binding, _bound = manager._bind_workflow_execution_profile(
    run, step, identity, launcher, qualification_policy="disabled"
)
pin = run.model_chain_override.get(step.persona)
requirements = {"independence": {
    "selected_domain": identity.independence_domain,
    "builder_domains": sorted({s.domain for s in run.steps if s.phase == "build"
        and s.gate_result == "passed" and s.commit_policy != "forbidden" and s.domain is not None}),
}, "minimum_quality": {"sizing_band": run.sizing_band}}
if pin is not None:
    requirements["pin"] = dict(pin)
direct = execution_adapters.make_launcher_profile(launcher, identity, step.persona, requirements=requirements)
argv = build_codex_argv(
    prompt="PROMPT", slice_id="conformance", log_dir="/unused", worktree=worktree,
    model=identity.model_id, commit_required=launcher._commit_required,
    write_forbidden=launcher._write_forbidden, last_message_path="/codex-home/last.json",
    effort=launcher._effort,
)
print(json.dumps({
    "resolved_key": binding.resolved_key,
    "request_key": binding.request_key,
    "direct_resolved_key": direct.resolved_key,
    "direct_request_key": direct.request_key,
    "resolved": binding.resolved.to_dict(),
    "argv": argv,
    "persona": render_contract_prompt("builder"),
}))
"""

_LIFECYCLE_SCRIPT = _COMMON + r"""
import hashlib
from datetime import datetime, timedelta, timezone
report_path, binding_path, target_path, store_root = sys.argv[1:5]
report_bytes = Path(report_path).read_bytes()
binding_bytes = Path(binding_path).read_bytes()
target = json.loads(Path(target_path).read_text(encoding="utf-8"))
identity, step, run = setup(target)
from paulsha_cortex.coordinator import execution_adapters, manager, qualification_lifecycle
from paulsha_cortex.coordinator.qualification_lifecycle import QualificationStore, _candidate_approval_blockers
store = QualificationStore(root=Path(store_root))
qualification_lifecycle._default_paths = lambda: store.paths
binding_payload = json.loads(binding_bytes)
profile_key = binding_payload["profile_id"]
imported = store.import_report(
    json.loads(report_bytes), source_revision="0123456789abcdef0123456789abcdef01234567",
    source_artifact_digest="sha256:" + hashlib.sha256(report_bytes).hexdigest(),
    profile_key=profile_key, executor=identity.executor, model_id=identity.model_id, role="build",
    profile_binding=binding_payload, profile_source_revision=None,
    profile_source_artifact_digest="sha256:" + hashlib.sha256(binding_bytes).hexdigest(),
    expected_revision=0, idempotency_key="dispatch-conformance-import", test_only=False,
)
candidate = json.loads((store.paths.candidates_root / f"{imported['candidate_id']}.json").read_text())["payload"]
blockers = _candidate_approval_blockers(candidate)
now = datetime.now(timezone.utc).replace(microsecond=0)
reviewed = now.isoformat().replace("+00:00", "Z")
expires = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
authority = store.issue_operator_receipt(
    imported["candidate_id"], verdict="approved", actor="conformance-owner", reason="conformance",
    policy_revision="qualification-policy-v1", reviewed_at=reviewed, expires_at=expires, now=reviewed,
)
approval = store.review_candidate(
    imported["candidate_id"], verdict="approved", operator_receipt_id=authority["operator_receipt_id"],
    test_only=False, expected_revision=imported["revision"], idempotency_key="dispatch-conformance-approve",
    now=reviewed,
)
admitted, _ = manager._bind_workflow_execution_profile(
    run, step, identity, dispatch_launcher(identity, step), qualification_policy="enforce"
)
revoke = store.issue_operator_receipt(
    imported["candidate_id"], verdict="revoked", actor="conformance-owner", reason="conformance revoke",
    policy_revision="qualification-policy-v1", reviewed_at=reviewed, expires_at=expires, now=reviewed,
)
store.revoke_qualification(
    imported["candidate_id"], operator_receipt_id=revoke["operator_receipt_id"], test_only=False,
    expected_revision=approval["revision"], idempotency_key="dispatch-conformance-revoke", now=reviewed,
)
try:
    manager._bind_workflow_execution_profile(
        run, step, identity, dispatch_launcher(identity, step), qualification_policy="enforce"
    )
except execution_adapters.ExecutionAdapterError as exc:
    blocked = str(exc)
else:
    blocked = None
print(json.dumps({
    "blockers": blockers,
    "coverage": candidate["coverage"]["state"],
    "observation": candidate["profile_observation"]["state"],
    "verdict": candidate["measurement"]["verdict"],
    "approval_state": approval.get("state"),
    "admitted_key": admitted.resolved_key,
    "blocked_after_revoke": blocked,
}))
"""


def _cortex(script: str, args: list[str], tmp_path: Path) -> dict:
    root = tmp_path / "cortex-isolated"
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "HOME": str(root / "home"),
        "PYTHONPATH": CORTEX_SRC,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PSC_INSTANCE": "cortex",
    }
    for name, rel in (
        ("PSC_AGENTS_ROOT", "agents"),
        ("PSC_COORDINATOR_ROOT", "agents/coordinator"),
        ("PSC_CONTROL_ROOT", "agents/control"),
        ("PSC_SPECS_ROOT", "agents/specs"),
        ("PSC_MONITOR_STATE_ROOT", "agents/monitor"),
        ("PSC_PROJECT_CONFIG_ROOT", "agents/config/project"),
        ("PSC_CONFIG_ROOT", "config"),
        ("PSC_RUN_ROOT", "agents/run/cortex"),
    ):
        env[name] = str(root / rel)
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "home").mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [os.environ.get("PATCHMUD_CORTEX_PYTHON", sys.executable), "-P", "-c", script, *args],
        capture_output=True,
        text=True,
        cwd=root / "home",
        env=env,
    )
    assert completed.returncode == 0, completed.stderr[-3000:]
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.fixture()
def cortex_src():
    if not CORTEX_SRC:
        pytest.skip("未設定 PATCHMUD_CORTEX_SRC（Cortex source 根目錄）")
    return CORTEX_SRC


def test_patchmud_dispatch_key_equals_cortex_manager_dispatch_key(cortex_src, tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    cortex = _cortex(_KEY_SCRIPT, [str(TARGET), str(worktree)], tmp_path)

    target = load_dispatch_target(TARGET)
    contract = resolve_launch_contract(target)
    record = build_dispatch_profile_record(target, contract)

    # Manager 的派工計算與 make_launcher_profile 直算一致，PatchMUD 與兩者完全相同。
    assert cortex["resolved_key"] == cortex["direct_resolved_key"]
    assert record["resolved_key"] == cortex["resolved_key"]
    assert record["profile_id"] == cortex["resolved_key"]
    assert record["requested_key"] == cortex["request_key"] == cortex["direct_request_key"]
    assert record["resolved"]["conditions"] == cortex["resolved"]["conditions"]
    assert record["resolved"]["requirements"] == cortex["resolved"]["requirements"]
    # target 檔記錄的 key 由同一套 Cortex 計算產生（稽核欄位與現況一致）。
    assert target.cortex_resolved_key == cortex["resolved_key"]
    assert target.cortex_request_key == cortex["request_key"]

    patchmud_argv = build_cortex_builder_codex_argv(
        codex="codex",
        prompt="PROMPT",
        model=contract.model_id,
        effort=contract.effort,
        last_message_path="/codex-home/last.json",
        worktree=str(worktree.resolve()),
        host_worktree=worktree,
    )
    assert patchmud_argv[1:] == cortex["argv"][1:]
    assert render_contract_prompt(load_persona_contract("builder")) == cortex["persona"]


def test_target_card_matches_cortex_deck_contract(cortex_src):
    import yaml

    target = json.loads(TARGET.read_text(encoding="utf-8"))
    cards = yaml.safe_load(
        (Path(cortex_src) / "paulsha_cortex" / "deck" / "data" / "cards.yaml").read_text(encoding="utf-8")
    )["cards"]
    card = next(item for item in cards if item["id"] == target["card"]["id"])
    assert card["phase"] == target["card"]["phase"]
    assert card["persona_binding"] == target["persona"]
    assert card["execution"] == {
        "action": target["card"]["action"],
        "commit_policy": target["card"]["commit_policy"],
        "test_policy": target["card"]["test_policy"],
    }
    assert card["produces"] == target["card"]["declared_outputs"]
    combo = yaml.safe_load(
        (Path(cortex_src) / "paulsha_cortex" / "deck" / "data" / "combos" / f"{target['combo']}.yaml").read_text(
            encoding="utf-8"
        )
    )["combo"]
    refs = [item["ref"] for item in combo["cards"]]
    build_cards = [ref for ref in refs if next(c for c in cards if c["id"] == ref)["phase"] == "build"]
    # target 宣告沒有先前的 build step：combo 內這張卡必須是第一張 build 卡。
    assert target["run"]["prior_steps"] == []
    assert build_cards[0] == target["card"]["id"]


def test_dispatch_lane_artifacts_pass_cortex_import_approve_enforce_and_revoke(
    cortex_src, real_capabilities, tmp_path
):
    fake = FakeCodex("fix")
    target = load_dispatch_target(TARGET)
    runs_root = tmp_path / "runs"
    result = run_dispatch_lane(
        _deck(tmp_path), target, runs_root, _config(fake, tmp_path), run_id="dispatch-conformance"
    )
    assert result.clear == 1
    report_dir = tmp_path / "report"
    assert main(["report", "--runs", str(runs_root / "*"), "--out", str(report_dir)]) == 0
    binding_path = tmp_path / "execution-profile.json"
    binding_path.write_text(
        json.dumps(build_profile_binding([result.run_dir]), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    store_root = tmp_path / "qualification-store"  # 由 Cortex store 自行以私有權限建立
    outcome = _cortex(
        _LIFECYCLE_SCRIPT,
        [str(report_dir / "report.json"), str(binding_path), str(TARGET), str(store_root)],
        tmp_path,
    )
    assert outcome["blockers"] == []
    assert outcome["coverage"] == "complete"
    assert outcome["observation"] == "complete"
    assert outcome["verdict"] == "pass"
    assert outcome["approval_state"] == "approved"
    assert outcome["admitted_key"] == target.cortex_resolved_key == result.profile_id
    assert outcome["blocked_after_revoke"] is not None
    assert "qualification" in outcome["blocked_after_revoke"]
