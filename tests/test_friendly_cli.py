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
        assert normalize_model_spec("opus") == "anthropic:claude-opus-4-8"
        assert normalize_model_spec("fable") == "anthropic:claude-fable-5"

    def test_anthropic_prefixed_alias_expands_when_credentials_set(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        assert normalize_model_spec("anthropic:sonnet") == "anthropic:claude-sonnet-5"

    def test_full_spec_untouched(self):
        assert normalize_model_spec("anthropic:claude-sonnet-5") == "anthropic:claude-sonnet-5"
        assert normalize_model_spec("scripted:/tmp/x") == "scripted:/tmp/x"
        assert normalize_model_spec("openai:gpt-x@http://h/v1") == "openai:gpt-x@http://h/v1"


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


