"""`patchmud profile-binding`：run 結束後的 execution profile → Cortex binding。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import patchmud.adapters.profile as profile_module
from patchmud.adapters.observation import RuntimeObservation
from patchmud.adapters.profile import (
    build_execution_profile_record,
    build_registered_adapter,
)
from patchmud.cli import ProfileBindingError, build_profile_binding, main
from patchmud.store.run_store import RunStore
from patchmud.store.schemas import StoreError


@pytest.fixture(autouse=True)
def _offline_toolchain(monkeypatch):
    monkeypatch.setattr(
        profile_module,
        "_runtime_toolchain",
        lambda _registration: {"id": "codex-cli", "version": "sha256:" + "c" * 64},
    )


def _observation(thread: str, **overrides) -> RuntimeObservation:
    values = {
        "source": "codex-app-server:thread/read",
        "model_id": "gpt-6-luna",
        "effort": "max",
        "evidence": ({"call": 1, "state": "observed", "thread_sha256": thread * 64},),
    }
    values.update(overrides)
    return RuntimeObservation(**values)


def _run(tmp_path: Path, run_id: str, observation: RuntimeObservation | None, *, post_run=True):
    encounter = tmp_path / "encounter"
    encounter.mkdir(exist_ok=True)
    adapter = build_registered_adapter("codex:gpt-6-luna", effort="max")
    pre_run = build_execution_profile_record(adapter, "P0T0R0")
    store = RunStore.create(
        {
            "run_id": run_id,
            "frozen_sha": "frozen-test",
            "pricing_hash": "pricing-test",
            "harness_prompt_version": "harness-test",
            "schedule_ref": "schedule-test",
            "encounter_dir": str(encounter),
            "model": "codex:gpt-6-luna",
            "execution_profile": pre_run,
        },
        tmp_path / "runs",
    )
    if post_run:
        store.write_execution_profile(
            build_execution_profile_record(adapter, "P0T0R0", observation=observation)
        )
    return store.run_dir


def test_single_run_binding_has_cortex_payload_shape(tmp_path, capsys):
    run_dir = _run(tmp_path, "run-a", _observation("a"))
    out = tmp_path / "binding" / "execution-profile.json"
    assert main(["profile-binding", str(run_dir), "--out", str(out)]) == 0

    binding = json.loads(out.read_text(encoding="utf-8"))
    for key in (
        "schema_version", "descriptor", "profile_id", "requested", "requested_key",
        "resolved", "resolved_key", "observed", "actual_condition_key",
    ):
        assert key in binding
    assert binding["profile_id"] == binding["resolved_key"]
    assert binding["actual_condition_key"].startswith("epk:v1:actual:")
    assert binding["observed"]["metadata"]["evidence_refs"][0]["run_id"] == "run-a"
    assert "profile_id=" in capsys.readouterr().out


def test_multiple_runs_merge_evidence_only_when_observation_agrees(tmp_path):
    first = _run(tmp_path, "run-a", _observation("a"))
    second = _run(tmp_path, "run-b", _observation("b"))
    binding = build_profile_binding([first, second])
    assert [item["run_id"] for item in binding["observed"]["metadata"]["evidence_refs"]] == [
        "run-a",
        "run-b",
    ]

    disagreeing = _run(tmp_path, "run-c", _observation("c", effort=None, effort_reason="x"))
    with pytest.raises(ProfileBindingError, match="observed conditions"):
        build_profile_binding([first, disagreeing])


def test_runs_without_post_run_profile_are_rejected(tmp_path, capsys):
    run_dir = _run(tmp_path, "run-legacy", None, post_run=False)
    assert main(["profile-binding", str(run_dir)]) == 2
    assert "execution_profile.json" in capsys.readouterr().err


def test_tampered_post_run_profile_is_rejected(tmp_path):
    run_dir = _run(tmp_path, "run-a", _observation("a"))
    path = run_dir / "execution_profile.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["observed"]["conditions"]["effort"] = {"state": "known", "value": "low"}
    path.write_text(json.dumps(record), encoding="utf-8")
    assert main(["profile-binding", str(run_dir)]) == 2


def test_post_run_profile_is_write_once(tmp_path):
    run_dir = _run(tmp_path, "run-a", _observation("a"))
    store = RunStore.open(run_dir)
    with pytest.raises(StoreError, match="不可覆寫"):
        store.write_execution_profile(store.load_execution_profile())
