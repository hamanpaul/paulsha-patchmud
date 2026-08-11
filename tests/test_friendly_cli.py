"""friendly CLI：encounter 名字解析 + live --delay 節奏。"""

from __future__ import annotations

from pathlib import Path

import pytest

from patchmud.cli import RunCliError, normalize_model_spec, resolve_encounter
from patchmud.store.watch import LiveSpectator

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestResolveEncounter:
    def test_bare_name_resolves_under_pilot_deck(self, monkeypatch):
        monkeypatch.chdir(REPO_ROOT)
        assert resolve_encounter("input-validation-v1").name == "input-validation-v1"

    def test_explicit_path_used_as_is(self, monkeypatch):
        monkeypatch.chdir(REPO_ROOT)
        got = resolve_encounter("decks/pilot-v1/parser-edge-v1")
        assert (got / "card.yaml").is_file()

    def test_unknown_name_errors_with_available_list(self, monkeypatch):
        monkeypatch.chdir(REPO_ROOT)
        with pytest.raises(RunCliError) as exc:
            resolve_encounter("no-such-level")
        # 報錯附可玩關卡清單
        assert "input-validation-v1" in str(exc.value)


_BASELINE_EVENT = {
    "type": "baseline",
    "probes": {},
    "queue": {"b_t": 0, "m_t": 0, "open_items": []},
}
_TURN_EVENT = {
    "type": "turn",
    "turn": 1,
    "action": "PATCH",
    "outcome": "executed",
    "detail": None,
    "probes": {},
    "queue": {"b_t": 0, "m_t": 0, "open_items": []},
}


class TestModelAlias:
    def test_bare_alias_expands_to_anthropic_when_credentials_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        assert normalize_model_spec("sonnet") == "anthropic:claude-sonnet-5"
        assert normalize_model_spec("haiku") == "anthropic:claude-haiku-4-5"
        assert normalize_model_spec("opus") == "anthropic:claude-opus-5"
        assert normalize_model_spec("fable") == "anthropic:claude-fable-5"

    def test_codex_aliases_expand_to_codex_specs(self):
        # codex 走自帶 OAuth（~/.codex/auth.json），與 Anthropic 憑證無關。
        assert normalize_model_spec("spark") == "codex:gpt-5.3-codex-spark"
        assert normalize_model_spec("luna") == "codex:gpt-5.6-luna"
        assert normalize_model_spec("terra") == "codex:gpt-5.6-terra"
        assert normalize_model_spec("sol") == "codex:gpt-5.6-sol"

    def test_agy_aliases_expand_to_agy_specs(self):
        assert normalize_model_spec("flash") == "agy:gemini-3.6-flash"
        assert normalize_model_spec("pro") == "agy:gemini-3.1-pro"

    def test_codex_and_agy_aliases_ignore_anthropic_credential_state(self, monkeypatch):
        # 別無 Anthropic 憑證時，codex/agy 別名不得被 claude CLI fallback 劫持。
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setattr("patchmud.cli.has_claude_cli", lambda: True)
        assert normalize_model_spec("sol") == "codex:gpt-5.6-sol"
        assert normalize_model_spec("flash") == "agy:gemini-3.6-flash"

    def test_anthropic_prefixed_alias_expands_when_credentials_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        assert normalize_model_spec("anthropic:sonnet") == "anthropic:claude-sonnet-5"

    def test_full_spec_untouched(self):
        assert normalize_model_spec("anthropic:claude-sonnet-5") == "anthropic:claude-sonnet-5"
        assert normalize_model_spec("scripted:/tmp/x") == "scripted:/tmp/x"
        assert normalize_model_spec("openai:gpt-x@http://h/v1") == "openai:gpt-x@http://h/v1"
        assert normalize_model_spec("codex:gpt-5.6-sol") == "codex:gpt-5.6-sol"
        assert normalize_model_spec("agy:gemini-3.1-pro") == "agy:gemini-3.1-pro"


class TestLiveDelay:
    def test_delay_paces_after_each_turn(self):
        slept: list[float] = []
        spec = LiveSpectator(out=lambda _s: None, delay=1.5, sleep=slept.append)
        spec.feed(_BASELINE_EVENT)
        spec.feed(_TURN_EVENT)
        # baseline + turn 各 pace 一次
        assert slept == [1.5, 1.5]

    def test_zero_delay_never_sleeps(self):
        slept: list[float] = []
        spec = LiveSpectator(out=lambda _s: None, delay=0.0, sleep=slept.append)
        spec.feed(_BASELINE_EVENT)
        assert slept == []


class TestAnthropicCredentialCheck:
    def test_has_anthropic_credentials_returns_false_when_unset(self, monkeypatch):
        from patchmud.cli import has_anthropic_credentials

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        assert has_anthropic_credentials() is False

    def test_has_anthropic_credentials_returns_true_when_api_key_set(self, monkeypatch):
        from patchmud.cli import has_anthropic_credentials

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        assert has_anthropic_credentials() is True

    def test_has_anthropic_credentials_returns_true_when_auth_token_set(self, monkeypatch):
        from patchmud.cli import has_anthropic_credentials

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
        assert has_anthropic_credentials() is True

    def test_build_adapter_missing_credentials_and_no_claude_cli_errors(self, monkeypatch):
        from patchmud.cli import _build_adapter

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setattr("patchmud.cli.has_claude_cli", lambda: False)
        with pytest.raises(RunCliError) as exc:
            _build_adapter("sonnet")
        err_msg = str(exc.value)
        assert "未設定 Anthropic 憑證" in err_msg
        assert "openai:" in err_msg  # 提供地端免 Key 模型指引

    def test_claude_cli_fallback_when_credentials_unset_and_claude_on_path(self, monkeypatch):
        from patchmud.adapters.claude_cli import ClaudeCliAdapter
        from patchmud.cli import _build_adapter, normalize_model_spec

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.setattr("patchmud.cli.has_claude_cli", lambda: True)

        spec = normalize_model_spec("haiku")
        assert spec == "claude:claude-haiku-4-5"

        adapter = _build_adapter("haiku")
        assert isinstance(adapter, ClaudeCliAdapter)


class TestCodexAndAgyAdapterBuild:
    """codex / agy spec → adapter（CLI 不在 PATH 時 fail-closed）。"""

    def test_codex_spec_builds_codex_adapter_with_high_effort(self, monkeypatch):
        from patchmud.adapters.codex_cli import CodexCliAdapter
        from patchmud.cli import _build_adapter

        monkeypatch.setattr("patchmud.cli.has_codex_cli", lambda: True)
        adapter = _build_adapter("sol")
        assert isinstance(adapter, CodexCliAdapter)
        assert adapter.effort == "high"
        assert adapter.model == "gpt-5.6-sol"

    def test_agy_spec_builds_agy_adapter_with_high_effort(self, monkeypatch):
        from patchmud.adapters.agy_cli import AgyCliAdapter
        from patchmud.cli import _build_adapter

        monkeypatch.setattr("patchmud.cli.has_agy_cli", lambda: True)
        adapter = _build_adapter("flash")
        assert isinstance(adapter, AgyCliAdapter)
        assert adapter.effort == "high"
        assert adapter.model == "gemini-3.6-flash"

    def test_codex_missing_binary_is_fail_closed(self, monkeypatch):
        from patchmud.cli import _build_adapter

        monkeypatch.setattr("patchmud.cli.has_codex_cli", lambda: False)
        with pytest.raises(RunCliError) as exc:
            _build_adapter("sol")
        assert "codex" in str(exc.value)

    def test_agy_missing_binary_is_fail_closed(self, monkeypatch):
        from patchmud.cli import _build_adapter

        monkeypatch.setattr("patchmud.cli.has_agy_cli", lambda: False)
        with pytest.raises(RunCliError) as exc:
            _build_adapter("pro")
        assert "agy" in str(exc.value)

    def test_codex_spec_without_model_is_fail_closed(self, monkeypatch):
        from patchmud.cli import _build_adapter

        monkeypatch.setattr("patchmud.cli.has_codex_cli", lambda: True)
        with pytest.raises(RunCliError):
            _build_adapter("codex:")


class TestRunRecordsExpandedModelSpec:
    """run.yaml 封存展開後的完整 spec，不是使用者打的別名。

    別名表是會演進的間接層（`opus` 曾指向 claude-opus-4-8、現指向
    claude-opus-5）。封存若只記 `opus`，事後無從得知當時實際跑的是哪一個
    模型，違反「可位元重播」的前提。
    """

    def _capture_record_extra(self, monkeypatch, alias: str) -> dict:
        import patchmud.cli as cli

        captured: dict = {}

        def fake_wire(*_args, **kwargs):
            captured.update(kwargs["record_extra"])
            raise _StopWiring

        monkeypatch.setattr(cli, "_wire_and_run_encounter", fake_wire)
        monkeypatch.setattr(cli, "_build_adapter", lambda spec: object())
        monkeypatch.setattr(cli, "load_card", lambda _p: _FakeCard())
        with pytest.raises(_StopWiring):
            cli.run_cli(Path("/nonexistent"), alias, "P0T0R0", Path("/tmp"))
        return captured

    def test_codex_alias_is_expanded_in_run_yaml(self, monkeypatch):
        assert self._capture_record_extra(monkeypatch, "sol")["model"] == (
            "codex:gpt-5.6-sol"
        )

    def test_agy_alias_is_expanded_in_run_yaml(self, monkeypatch):
        assert self._capture_record_extra(monkeypatch, "pro")["model"] == (
            "agy:gemini-3.1-pro"
        )

    def test_anthropic_alias_is_expanded_in_run_yaml(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        assert self._capture_record_extra(monkeypatch, "opus")["model"] == (
            "anthropic:claude-opus-5"
        )

    def test_full_spec_is_recorded_unchanged(self, monkeypatch):
        assert self._capture_record_extra(monkeypatch, "scripted:/tmp/x.txt")[
            "model"
        ] == "scripted:/tmp/x.txt"


class _StopWiring(Exception):
    """在真佈線前中止 run_cli，只擷取 record_extra。"""


class _FakeCard:
    issue_id = "fake-encounter-v1"


