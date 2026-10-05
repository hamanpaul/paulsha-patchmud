"""paulsha-cortex#842：dispatch target 驗證、requirements 推導與 dispatch profile key。

Cortex 端的 key 由 ``tests/test_cortex_dispatch_conformance.py`` 以真 Cortex 程式重算；
這裡釘住離線可驗的部分：target 檔記錄的 Cortex key、推導規則與 fail-closed 路徑。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from patchmud.adapters.cortex_catalog import cortex_adapter_identity
from patchmud.adapters.observation import RuntimeObservation
from patchmud.cortex_dispatch.profile import (
    DispatchProfileError,
    LaunchObservation,
    build_dispatch_profile_record,
    resolve_launch_contract,
)
from patchmud.cortex_dispatch.target import (
    DispatchTargetError,
    load_dispatch_target,
    parse_dispatch_target,
)
from patchmud.adapters.profile import verify_execution_profile_record

TARGET_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "cortex-dispatch"
    / "small-fix-subagent-build-codex-gpt-6-luna-green.json"
)


def _payload() -> dict:
    return json.loads(TARGET_PATH.read_text(encoding="utf-8"))


def _observations(**overrides):
    launch = dict(
        started=True,
        argv_conforms=True,
        workspace_write_boundary=True,
        persona_contract_rendered=True,
        git_commit_available=True,
        facts={"codex_argv_sha256": "sha256:" + "0" * 64},
    )
    launch.update(overrides)
    thread = RuntimeObservation(
        source="codex-app-server:thread/read",
        model_id="gpt-6-luna",
        effort="max",
        evidence=({"call": 1, "state": "observed"},),
    )
    return LaunchObservation(**launch), thread


def test_fixture_target_reproduces_recorded_cortex_keys():
    target = load_dispatch_target(TARGET_PATH)
    record = build_dispatch_profile_record(target, resolve_launch_contract(target))
    assert record["resolved_key"] == target.cortex_resolved_key
    assert record["profile_id"] == target.cortex_resolved_key
    assert record["requested_key"] == target.cortex_request_key
    # 開局沒有任何觀測：observed 全 unknown，actual key 為空。
    assert record["actual_condition_key"] is None
    verify_execution_profile_record(record)


def test_resolved_plane_matches_cortex_builder_launch_contract():
    target = load_dispatch_target(TARGET_PATH)
    record = build_dispatch_profile_record(target, resolve_launch_contract(target))
    conditions = record["resolved"]["conditions"]
    assert conditions["model"]["value"] == {"id": "gpt-6-luna", "revision": "unreported"}
    assert conditions["effort"]["value"] == "max"  # catalog model_defaults，launcher 未明示
    assert conditions["loadout"]["value"] == {"id": "builder", "version": "1"}
    assert conditions["toolset"]["value"] == [{"id": "git-commit", "version": "1"}]
    assert conditions["sandbox"]["value"] == {"id": "workspace-write", "version": "1"}
    assert conditions["permissions"]["value"] == [{"id": "workspace-write", "version": "1"}]
    assert conditions["toolchain"]["value"] == {"id": "codex", "version": "cortex-adapter-v1"}
    assert conditions["adapter"]["value"] == dict(cortex_adapter_identity("codex").descriptor_fields)
    requirements = record["resolved"]["requirements"]
    assert requirements["role"]["value"] == "build"
    assert requirements["minimum_quality"]["value"] == {"sizing_band": "green"}
    assert requirements["pin"]["value"] == {"executor": "codex", "model_id": "gpt-6-luna"}
    assert requirements["independence"]["value"] == {
        "selected_domain": "openai",
        "builder_domains": [],
    }


def test_observed_plane_known_only_with_both_trusted_observations():
    target = load_dispatch_target(TARGET_PATH)
    contract = resolve_launch_contract(target)
    launch, thread = _observations()
    record = build_dispatch_profile_record(target, contract, launch=launch, thread=thread)
    observed = record["observed"]["conditions"]
    assert all(value["state"] == "known" for value in observed.values())
    assert observed == record["resolved"]["conditions"]
    assert record["actual_condition_key"].startswith("epk:v1:actual:")
    assert record["observed"]["requirements"] == record["resolved"]["requirements"]
    refs = {item["ref"] for item in record["observed"]["provenance"]}
    assert {"codex-app-server:thread/read", "patchmud:dispatch-launch-v1"} <= refs
    # 發射事實只進 metadata，不影響 key。
    assert record["observed"]["metadata"]["discovery"]["launch"]["codex_argv_sha256"]


@pytest.mark.parametrize(
    ("override", "unknown"),
    [
        ({"started": False}, {"adapter", "toolchain", "loadout", "toolset", "sandbox", "permissions"}),
        ({"argv_conforms": False}, {"adapter", "toolchain"}),
        ({"workspace_write_boundary": False}, {"sandbox", "permissions"}),
        ({"persona_contract_rendered": False}, {"loadout"}),
        ({"git_commit_available": False}, {"toolset"}),
    ],
)
def test_unconfirmed_launch_fact_keeps_condition_unknown(override, unknown):
    target = load_dispatch_target(TARGET_PATH)
    launch, thread = _observations(**override)
    record = build_dispatch_profile_record(
        target, resolve_launch_contract(target), launch=launch, thread=thread
    )
    observed = record["observed"]["conditions"]
    assert {name for name, value in observed.items() if value["state"] != "known"} == unknown
    assert record["actual_condition_key"] is None


def test_provider_reported_other_model_or_effort_is_not_borrowed_from_resolved():
    target = load_dispatch_target(TARGET_PATH)
    launch, _ = _observations()
    thread = RuntimeObservation(
        source="codex-app-server:thread/read", model_id="gpt-5.6-luna", effort="high"
    )
    record = build_dispatch_profile_record(
        target, resolve_launch_contract(target), launch=launch, thread=thread
    )
    observed = record["observed"]["conditions"]
    assert observed["model"]["state"] == "unknown"
    # effort 在 grammar 內但與 resolved 不同：如實記 known（Cortex 會判 mismatch）。
    assert observed["effort"] == {"state": "known", "value": "high"}
    assert record["actual_condition_key"] is None


def test_builder_domains_follow_cortex_rule():
    payload = _payload()
    payload["run"]["prior_steps"] = [
        {"card": "worktree-isolation", "phase": "build", "gate_result": "passed",
         "commit_policy": "forbidden", "domain": "openai"},
        {"card": "tdd-red", "phase": "build", "gate_result": "passed",
         "commit_policy": "required", "domain": "openai"},
        {"card": "tdd-red-2", "phase": "build", "gate_result": "failed",
         "commit_policy": "required", "domain": "anthropic"},
        {"card": "brainstorming", "phase": "define", "gate_result": "passed",
         "commit_policy": None, "domain": "google"},
        {"card": "x", "phase": "build", "gate_result": "passed",
         "commit_policy": "optional", "domain": "zhipu"},
    ]
    target = parse_dispatch_target(payload)
    assert target.builder_domains == ("openai", "zhipu")
    record = build_dispatch_profile_record(target, resolve_launch_contract(target))
    assert record["resolved_key"] != _payload()["cortex_resolved_key"]


def test_unpinned_target_keeps_pin_known_null_and_requested_adapter_unknown():
    payload = _payload()
    payload["run"]["model_chain_override"] = {}
    target = parse_dispatch_target(payload)
    record = build_dispatch_profile_record(target, resolve_launch_contract(target))
    assert record["resolved"]["requirements"]["pin"] == {"state": "known", "value": None}
    assert record["requested"]["conditions"]["adapter"]["state"] == "unknown"
    assert record["resolved_key"] != payload["cortex_resolved_key"]


def test_explicit_launcher_effort_is_requested_and_resolved():
    payload = _payload()
    payload["launcher"]["effort"] = "high"
    target = parse_dispatch_target(payload)
    record = build_dispatch_profile_record(target, resolve_launch_contract(target))
    assert record["requested"]["conditions"]["effort"] == {"state": "known", "value": "high"}
    assert record["resolved"]["conditions"]["effort"] == {"state": "known", "value": "high"}


def test_model_without_catalog_effort_default_fails_closed():
    payload = _payload()
    payload["identity"]["model_id"] = "gpt-unknown"
    payload["run"]["model_chain_override"]["builder"]["model_id"] = "gpt-unknown"
    target = parse_dispatch_target(payload)
    with pytest.raises(DispatchProfileError, match="effort"):
        resolve_launch_contract(target)


def _mutated(path: str, value):
    payload = _payload()
    node = payload
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[keys[-1]]
    else:
        node[keys[-1]] = value
    return payload


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("schema", "other/v1", "schema"),
        ("persona", "reviewer", "builder persona"),
        ("card.commit_policy", "optional", "commit_policy=required"),
        ("card.phase", "review", "build phase"),
        ("launcher.allow_unsafe", True, "allow_unsafe"),
        ("run.sizing_band", None, "sizing_band"),
        ("run.sizing_band", "blue", "sizing_band"),
        ("identity.executor", "claude", "codex"),
        ("identity.capabilities", ["review"], "build capability"),
        ("run.model_chain_override", {"builder": {"executor": "codex", "model_id": "gpt-5.6-luna"}}, "pin"),
        ("card.unexpected", 1, "未知欄位"),
        ("identity.independence_domain", _DELETE, "缺欄位"),
    ],
)
def test_target_outside_builder_lane_conditions_is_rejected(path, value, message):
    with pytest.raises(DispatchTargetError, match=message):
        parse_dispatch_target(_mutated(path, value))


def test_target_digest_tracks_content():
    first = parse_dispatch_target(_payload())
    changed = copy.deepcopy(_payload())
    changed["description"] = "changed"
    assert parse_dispatch_target(changed).sha256 != first.sha256
    assert first.sha256 == parse_dispatch_target(_payload()).sha256
