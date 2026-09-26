"""run CLI 的 profile 設定驗證與 run.yaml profile 資料測試。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import patchmud.cli as cli
from patchmud.cli import RunCliError
from patchmud.adapters.profile import build_execution_profile_record
from patchmud.store.run_store import RunStore


class _StopWiring(Exception):
    pass


class _FakeCard:
    issue_id = "offline-profile-test"


def test_run_cli_exposes_effort_and_tool_mode_options(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(cli, "resolve_encounter", lambda value: Path(value))

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            end_reason="commit",
            clear=1,
            turns_used=1,
            evaluation=SimpleNamespace(power=SimpleNamespace(total=1)),
        )

    monkeypatch.setattr(cli, "run_cli", fake_run)
    assert cli._cmd_run(
        [
            "fixture",
            "--model",
            "codex:atlas-next",
            "--effort",
            "xhigh",
            "--tool-mode",
            "none",
        ]
    ) == 0
    assert captured["effort"] == "xhigh"
    assert captured["tool_mode"] == "none"
    assert "end_reason=" in capsys.readouterr().out


def test_cli_validates_native_effort_and_tool_mode_before_runtime_check(monkeypatch):
    monkeypatch.setattr(
        cli,
        "has_codex_cli",
        lambda: pytest.fail("unsupported settings must fail before runtime lookup"),
    )
    with pytest.raises(RunCliError, match="profile 設定不支援"):
        cli._build_adapter("codex:atlas-next", effort="unlisted-effort")
    with pytest.raises(RunCliError, match="不支援的 tool mode"):
        cli._build_adapter("codex:atlas-next", tool_mode="write-access")


def test_default_effort_is_resolved_high_and_ignores_ambient_cli_settings(monkeypatch):
    monkeypatch.setattr(cli, "has_codex_cli", lambda: True)
    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "low")
    first = cli._build_adapter("codex:atlas-next")
    monkeypatch.setenv("CODEX_MODEL_REASONING_EFFORT", "xhigh")
    second = cli._build_adapter("codex:atlas-next")

    assert first.effort == second.effort == "high"
    assert first.execution_profile_resolved_effort == "high"


def test_explicit_unsupported_effort_for_api_adapter_is_rejected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    with pytest.raises(RunCliError, match="profile 設定不支援"):
        cli._build_adapter("anthropic:atlas-next", effort="high")


def test_run_record_contains_all_profile_planes_and_resolved_profile_id(monkeypatch):
    monkeypatch.setattr(cli, "has_codex_cli", lambda: True)
    monkeypatch.setattr(cli, "load_card", lambda _path: _FakeCard())
    captured = {}

    def fake_wire(*_args, **kwargs):
        captured.update(kwargs["record_extra"])
        raise _StopWiring

    monkeypatch.setattr(cli, "_wire_and_run_encounter", fake_wire)
    with pytest.raises(_StopWiring):
        cli.run_cli(
            Path("fixture"),
            "codex:atlas-next",
            "P0T0R0",
            Path("runs"),
            effort="xhigh",
        )

    profile = captured["execution_profile"]
    assert profile["profile_id"].startswith("epk:v1:resolved:")
    assert set(("requested", "resolved", "observed")) <= profile.keys()
    assert profile["resolved"]["conditions"]["effort"] == {
        "state": "known",
        "value": "xhigh",
    }
    assert profile["observed"]["conditions"]["effort"]["state"] == "unknown"
    assert profile["actual_condition_key"] is None


def test_run_store_persists_profile_wire_without_script_path(tmp_path):
    encounter = tmp_path / "encounter"
    encounter.mkdir()
    script = tmp_path / "offline-replies.txt"
    script.write_text("reply\n", encoding="utf-8")
    adapter = cli._build_adapter(f"scripted:{script}")
    profile = build_execution_profile_record(adapter, "P0T0R0")
    store = RunStore.create(
        {
            "run_id": "profile-run",
            "frozen_sha": "frozen-test",
            "pricing_hash": "pricing-test",
            "harness_prompt_version": "harness-test",
            "schedule_ref": "schedule-test",
            "encounter_dir": str(encounter),
            "model": "scripted:offline-scripted",
            "execution_profile": profile,
        },
        tmp_path / "runs",
    )

    run_yaml = (store.run_dir / "run.yaml").read_text(encoding="utf-8")
    record = yaml.safe_load(run_yaml)
    assert record["execution_profile"] == profile
    assert str(script) not in run_yaml
