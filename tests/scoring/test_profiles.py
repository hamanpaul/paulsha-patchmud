from __future__ import annotations

import json
import subprocess

import pytest

from patchmud.adapters.agy_cli import AgyCliAdapter
from patchmud.adapters.base import AdapterError
from patchmud.adapters.cli_base import build_subprocess_runner
from patchmud.adapters.codex_cli import CodexCliAdapter
from patchmud.scoring.runner import build_scoring_adapter, resolve_profile


def test_agy_profile_keeps_requested_identity_and_resolves_effort_variant() -> None:
    profile = resolve_profile("agy", "gemini-3.8-flash", "high")

    assert profile["requested"] == {
        "harness": "agy",
        "model": "gemini-3.8-flash",
        "effort": "high",
    }
    assert profile["resolved"]["model"] == "gemini-3.8-flash-high"
    assert profile["resolved"]["effort"] == "high"
    assert profile["observed"]["model"] is None
    assert profile["observed"]["effort"] is None


def test_codex_profile_accepts_luna_max_from_local_model_cache() -> None:
    profile = resolve_profile("codex", "gpt-5.6-luna", "max")

    assert profile["requested"]["effort"] == "max"
    assert profile["resolved"]["model"] == "gpt-5.6-luna"
    assert profile["resolved"]["effort"] == "max"


def test_codex_profile_catalog_does_not_depend_on_user_home_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "patchmud.scoring.runner._CODEX_CACHE", tmp_path / "missing-models.json"
    )

    profile = resolve_profile("codex", "gpt-5.6-luna", "max")

    assert profile["resolved"]["model"] == "gpt-5.6-luna"


def test_installed_cli_profile_fails_closed_without_native_tool_control() -> None:
    profile = resolve_profile("codex", "gpt-5.6-luna", "max")

    assert profile["capability"]["controlled_supported"] is False
    assert profile["capability"]["cacheable"] is False
    with pytest.raises(AdapterError, match="native-tools-off"):
        build_scoring_adapter(profile)


@pytest.mark.parametrize(
    "harness,model,effort",
    [
        ("unknown", "m", "high"),
        ("codex", "gpt-5.6-luna", "not-an-effort"),
        ("agy", "gemini-3.8-flash-high", "high"),
    ],
)
def test_profile_rejects_unknown_or_ambiguous_requests(
    harness: str, model: str, effort: str
) -> None:
    if (harness, model, effort) == ("agy", "gemini-3.8-flash-high", "high"):
        # A resolved id is accepted, but it must not acquire a second suffix.
        profile = resolve_profile(harness, model, effort)
        assert profile["resolved"]["model"] == model
    else:
        with pytest.raises(ValueError):
            resolve_profile(harness, model, effort)


def test_build_scoring_adapter_does_not_call_a_native_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class _FakeAdapter:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def complete(self, messages):
            raise AssertionError("the adapter must be injected in execution tests")

    monkeypatch.setattr("patchmud.scoring.runner.CodexCliAdapter", _FakeAdapter)
    profile = resolve_profile("codex", "gpt-5.6-luna", "max")
    # The installed CLIs do not expose a strict native-tools-off control.  The
    # fake harness opts into the adapter construction seam without launching it.
    profile["capability"]["controlled_supported"] = True
    adapter = build_scoring_adapter(profile, timeout_s=17)

    assert isinstance(adapter, _FakeAdapter)
    assert adapter.kwargs["effort"] == "max"
    assert adapter.kwargs["timeout_s"] == 17
    assert adapter.kwargs["controlled"] is True
    assert not seen


def test_controlled_cli_sends_deep_prompt_over_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scoring prompt must not hit Linux's per-argument size limit."""

    calls: list[tuple[list[str], dict, str | None, float | None]] = []

    class _FakeProcess:
        returncode = 0

        def __init__(self, argv: list[str], **kwargs) -> None:
            self.argv = argv
            self.kwargs = kwargs

        def communicate(
            self, *, input: str | None = None, timeout: float | None = None
        ) -> tuple[str, str]:
            calls.append((self.argv, self.kwargs, input, timeout))
            if self.argv[0] == "codex":
                stdout = "\n".join(
                    (
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "type": "agent_message",
                                    "text": "ACTION: LOOK",
                                },
                            }
                        ),
                        json.dumps(
                            {
                                "type": "turn.completed",
                                "usage": {"input_tokens": 1, "output_tokens": 1},
                            }
                        ),
                    )
                )
            else:
                stdout = json.dumps(
                    {
                        "status": "SUCCESS",
                        "response": "ACTION: LOOK",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                )
            return stdout, ""

        def poll(self) -> int:
            return self.returncode

    monkeypatch.setattr(
        "patchmud.adapters.cli_base.subprocess.Popen",
        lambda argv, **kwargs: _FakeProcess(argv, **kwargs),
    )
    monkeypatch.setenv("TYPESAFE_API_KEY", "must-not-reach-provider")
    deep_prompt = "x" * (128 * 1024 + 1)
    messages = [{"role": "user", "content": deep_prompt}]

    codex = CodexCliAdapter(
        model="gpt-5.6-luna",
        codex_binary="codex",
        workdir="/tmp/fake-codex",
        controlled=True,
        runner=build_subprocess_runner(timeout_s=11, controlled=True),
    )
    agy = AgyCliAdapter(
        model="gemini-3.8-flash-high",
        agy_binary="agy",
        controlled=True,
        runner=build_subprocess_runner(timeout_s=11, controlled=True),
    )

    codex.complete_with_timeout(messages, 7)
    agy.complete_with_timeout(messages, 8)

    assert len(calls) == 2
    assert [call[3] for call in calls] == [7, 8]
    assert all(call[1]["stdin"] is subprocess.PIPE for call in calls)
    assert all(deep_prompt in (call[2] or "") for call in calls)
    assert all("TYPESAFE_API_KEY" not in call[1]["env"] for call in calls)

    codex_argv, _, _, _ = calls[0]
    assert codex_argv[-1] == "-"
    assert deep_prompt not in codex_argv

    agy_argv, _, _, _ = calls[1]
    assert agy_argv[1:4] == ["--input-format", "text", "--print"]
    assert deep_prompt not in agy_argv
