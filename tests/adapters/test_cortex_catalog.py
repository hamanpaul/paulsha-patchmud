"""paulsha-cortex#842 Gap A／B：vendored Cortex adapter 身分與 observed plane。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from patchmud.adapters.cortex_catalog import (
    CATALOG_FILENAME,
    PROVENANCE_FILENAME,
    CortexCatalogError,
    cortex_adapter_identity,
    load_catalog_provenance,
)
from patchmud.adapters.observation import RuntimeObservation
from patchmud.adapters.profile import (
    ProfileRecordError,
    build_execution_profile_record,
    build_registered_adapter,
    finalize_execution_profile_record,
    verify_execution_profile_record,
)

DATA_DIR = (
    Path(__file__).resolve().parents[2] / "patchmud" / "adapters" / "data"
)
FAKE_TOOLCHAIN = {"id": "codex-cli", "version": "sha256:" + "a" * 64}


@pytest.fixture(autouse=True)
def _offline_codex_toolchain(monkeypatch):
    """CI 沒有 codex 可執行檔：固定 toolchain identity，不讀本機 binary。"""
    import patchmud.adapters.profile as profile_module

    monkeypatch.setattr(
        profile_module, "_runtime_toolchain", lambda _registration: dict(FAKE_TOOLCHAIN)
    )


def test_vendored_catalog_matches_pinned_provenance():
    provenance = load_catalog_provenance()
    data = (DATA_DIR / CATALOG_FILENAME).read_bytes()
    assert hashlib.sha256(data).hexdigest() == provenance["sha256"]
    assert provenance["source_repo"] == "hamanpaul/paulsha-cortex"
    assert provenance["source_path"] == "paulsha_cortex/coordinator/data/execution-adapters.yaml"
    assert len(provenance["source_revision"]) == 40


def test_ci_checks_out_cortex_at_the_vendored_revision():
    """CI 的 conformance 測試必須跑在與 vendored catalog 同一個 Cortex revision。"""
    workflow = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "tests.yml"
    ).read_text(encoding="utf-8")
    provenance = load_catalog_provenance()
    assert f"ref: {provenance['source_revision']}" in workflow
    assert "PATCHMUD_CORTEX_SRC:" in workflow


def test_edited_catalog_without_provenance_update_fails_closed(tmp_path):
    for name in (CATALOG_FILENAME, PROVENANCE_FILENAME):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    catalog = tmp_path / CATALOG_FILENAME
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace(
            "runtime_version: cortex-adapter-v1", "runtime_version: cortex-adapter-v2", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(CortexCatalogError, match="SHA-256"):
        cortex_adapter_identity("codex", data_dir=tmp_path)


def test_codex_identity_mirrors_cortex_descriptor_fields_construction():
    identity = cortex_adapter_identity("codex")
    assert dict(identity.descriptor_fields) == {
        "id": "codex-cli",
        "protocol_id": "openai-codex-cli",
        "protocol_version": "1",
        "runtime_version": "cortex-adapter-v1",
    }
    assert identity.efforts == ("low", "medium", "high", "xhigh", "max")
    with pytest.raises(CortexCatalogError):
        cortex_adapter_identity("no-such-executor")


def _codex_adapter(effort: str = "max"):
    return build_registered_adapter("codex:gpt-6-luna", effort=effort)


def _observation(**overrides) -> RuntimeObservation:
    values = {
        "source": "codex-app-server:thread/read",
        "model_id": "gpt-6-luna",
        "effort": "max",
        "evidence": ({"call": 1, "state": "observed", "thread_sha256": "0" * 64},),
    }
    values.update(overrides)
    return RuntimeObservation(**values)


def test_codex_descriptor_uses_cortex_identity_and_keeps_harness_runtime_as_discovery():
    record = build_execution_profile_record(_codex_adapter(), "P0T0R0")
    descriptor = record["descriptor"]
    assert descriptor["adapter"] == dict(cortex_adapter_identity("codex").descriptor_fields)
    discovery = descriptor["metadata"]["discovery"]
    assert discovery["harness_adapter"] == "patchmud.codex-cli"
    assert discovery["harness_runtime"].startswith("patchmud-")
    assert {"kind": "adapter-identity-source", "ref": cortex_adapter_identity("codex").source_ref} in (
        descriptor["provenance"]
    )
    # 開局快照：尚無 provider 觀測。
    assert record["observed"]["conditions"]["model"]["state"] == "unknown"
    assert record["actual_condition_key"] is None


def test_confirmed_observation_yields_actual_key_and_matches_resolved_conditions():
    adapter = _codex_adapter()
    record = build_execution_profile_record(adapter, "P0T0R0", observation=_observation())
    assert record["observed"]["conditions"] == record["resolved"]["conditions"]
    assert record["actual_condition_key"].startswith("epk:v1:actual:")
    assert {"kind": "observer", "ref": "codex-app-server:thread/read"} in (
        record["observed"]["provenance"]
    )
    # metadata 不進 key：evidence 不同，actual key 相同。
    other = build_execution_profile_record(
        adapter, "P0T0R0", observation=_observation(evidence=())
    )
    assert other["actual_condition_key"] == record["actual_condition_key"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"model_id": "gpt-6-astra"}, "model"),
        ({"model_id": None, "model_reason": "provider-identity-unavailable:1/1-calls"}, "model"),
        ({"effort": "turbo"}, "effort"),
        ({"effort": None, "effort_reason": "provider-effort-inconsistent-across-calls"}, "effort"),
    ],
)
def test_unconfirmed_or_conflicting_observation_stays_unknown(overrides, field):
    record = build_execution_profile_record(
        _codex_adapter(), "P0T0R0", observation=_observation(**overrides)
    )
    assert record["observed"]["conditions"][field]["state"] == "unknown"
    assert record["actual_condition_key"] is None


def test_observed_effort_different_from_resolved_is_kept_as_known_mismatch():
    record = build_execution_profile_record(
        _codex_adapter("max"), "P0T0R0", observation=_observation(effort="high")
    )
    assert record["observed"]["conditions"]["effort"] == {"state": "known", "value": "high"}
    assert record["resolved"]["conditions"]["effort"] == {"state": "known", "value": "max"}
    assert record["actual_condition_key"] is not None


def test_finalize_rebuilds_observed_without_changing_resolved_profile():
    adapter = _codex_adapter()
    pre_run = build_execution_profile_record(adapter, "P0T0R0")
    adapter.runtime_observation = _observation  # type: ignore[method-assign]
    post_run = finalize_execution_profile_record(adapter, pre_run)
    assert post_run["profile_id"] == pre_run["profile_id"]
    assert post_run["resolved"] == pre_run["resolved"]
    assert post_run["actual_condition_key"] is not None

    tampered = json.loads(json.dumps(pre_run))
    tampered["resolved"]["conditions"]["loadout"]["value"]["id"] = "P1T0R0"
    with pytest.raises(ProfileRecordError):
        finalize_execution_profile_record(adapter, tampered)


def test_verify_rejects_key_or_field_tampering():
    record = build_execution_profile_record(
        _codex_adapter(), "P0T0R0", observation=_observation()
    )
    assert verify_execution_profile_record(record) == record
    for key, value in (
        ("actual_condition_key", None),
        ("profile_id", "epk:v1:resolved:" + "0" * 64),
    ):
        tampered = {**record, key: value}
        with pytest.raises(ProfileRecordError):
            verify_execution_profile_record(tampered)
    with pytest.raises(ProfileRecordError):
        verify_execution_profile_record({**record, "extra": 1})


def _cortex_source() -> Path | None:
    value = os.environ.get("PATCHMUD_CORTEX_SRC", "").strip()
    return Path(value) if value else None


@pytest.mark.skipif(_cortex_source() is None, reason="未設定 PATCHMUD_CORTEX_SRC（Cortex source 根目錄）")
def test_vendored_catalog_is_byte_identical_to_cortex_source_and_descriptor_fields():
    """跨 repo conformance：與 Cortex 真實的 ExecutionAdapter.descriptor_fields() 逐欄一致。"""
    root = _cortex_source()
    provenance = load_catalog_provenance()
    source = root / provenance["source_path"]
    assert source.read_bytes() == (DATA_DIR / CATALOG_FILENAME).read_bytes()
    script = (
        "import json\n"
        "from paulsha_cortex.coordinator.execution_adapters import load_adapter_catalog\n"
        "catalog = load_adapter_catalog(config_root=__import__('sys').argv[1])\n"
        "print(json.dumps({'fields': catalog['codex'].descriptor_fields(),"
        " 'efforts': list(catalog['codex'].efforts)}))\n"
    )
    completed = subprocess.run(
        [os.environ.get("PATCHMUD_CORTEX_PYTHON", "python3"), "-c", script, str(root / "no-overlay")],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    cortex = json.loads(completed.stdout)
    identity = cortex_adapter_identity("codex")
    assert cortex["fields"] == dict(identity.descriptor_fields)
    assert tuple(cortex["efforts"]) == identity.efforts
