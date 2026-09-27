"""LLM CLI judge：prompt 攤平、argv 隔離旗標、輸出解析與成本 basis，全程 fake runner。"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from patchmud.judge.llm_cli import (
    OUTPUT_CONTRACT,
    SYSTEM_PROMPT,
    ClaudeCliJudge,
    CopilotCliJudge,
    copilot_user_mcp_servers,
    extract_json_object,
    render_user_prompt,
)
from patchmud.judge.structured import ChoiceQuestion, JudgeError, StructuredRequest

REQUEST = StructuredRequest(
    state={"acceptance_criterion": "非 ASCII 也要保留", "evidence": [{"id": "e1", "kind": "log", "content": "ok"}]},
    questions={"verdict": ChoiceQuestion(instructions="Decide.", criteria={"yes": "y", "no": "n"})},
)
ANSWER = json.dumps({"answers": {"verdict": {"choice": "yes", "confidence": 0.7}}})


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.5
        return self.now


class Runner:
    def __init__(self, outputs, usage=None):
        self.outputs = list(outputs)
        self.usage = usage
        self.calls = []

    def __call__(self, argv, cwd: Path):
        self.calls.append((list(argv), cwd))
        assert cwd.is_dir() and not any(cwd.iterdir()), "judge 必須在空暫存目錄執行"
        if self.usage is not None and "--usage-output-file" in argv:
            Path(argv[argv.index("--usage-output-file") + 1]).write_text(json.dumps(self.usage))
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _claude_stdout(result=ANSWER, **extra):
    data = {
        "type": "result",
        "is_error": False,
        "result": result,
        "total_cost_usd": 0.0021,
        "duration_api_ms": 2900,
        "usage": {"input_tokens": 900, "output_tokens": 30},
        "modelUsage": {"claude-sonnet-5": {}},
    }
    data.update(extra)
    return json.dumps(data)


def test_user_prompt_contains_state_questions_and_contract_verbatim():
    prompt = render_user_prompt(REQUEST)
    assert prompt.startswith("STATE:\n")
    assert "非 ASCII 也要保留" in prompt
    assert '"criteria"' in prompt and OUTPUT_CONTRACT in prompt


def test_claude_argv_disables_tools_and_replaces_system_prompt():
    runner = Runner([_claude_stdout()])
    response = ClaudeCliJudge(model="sonnet", binary="claude", runner=runner, clock=Clock()).judge(REQUEST)
    argv = runner.calls[0][0]
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--system-prompt") + 1] == SYSTEM_PROMPT
    assert argv[argv.index("-p") + 1] == render_user_prompt(REQUEST)
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert response.answers["verdict"].choice == "yes"
    assert response.model == "claude-sonnet-5"
    assert response.cost.usd == Decimal("0.0021")
    assert response.cost.basis == "claude_cli_reported_total_cost_usd"
    assert response.api_ms == 2900 and response.wall_ms == 1500


def test_claude_reported_error_is_retried_once_then_raised():
    runner = Runner([_claude_stdout(is_error=True, result="overloaded")] * 2)
    with pytest.raises(JudgeError) as exc:
        ClaudeCliJudge(binary="claude", runner=runner, clock=Clock()).judge(REQUEST)
    assert exc.value.kind == "transport"
    assert len(runner.calls) == 2


def test_prose_answer_is_invalid_output_and_not_retried():
    runner = Runner([_claude_stdout(result="I think it is satisfied.")])
    with pytest.raises(JudgeError) as exc:
        ClaudeCliJudge(binary="claude", runner=runner, clock=Clock()).judge(REQUEST)
    assert exc.value.kind == "invalid_output"
    assert len(runner.calls) == 1


def test_fenced_json_is_extracted_but_flagged_inexact():
    body, exact = extract_json_object('```json\n{"answers": {}}\n```')
    assert body == {"answers": {}} and exact is False
    assert extract_json_object(' {"a": 1} ') == ({"a": 1}, True)


def _copilot_stdout(content=ANSWER):
    events = [
        {"type": "session.mcp_server_status_changed", "data": {}},
        {"type": "assistant.message", "data": {"content": ""}},
        {"type": "assistant.message", "data": {"content": content}},
    ]
    return "\n".join(json.dumps(e) for e in events) + "\nnot json\n"


USAGE = {
    "totalPremiumRequestCost": 6,
    "totalApiDurationMs": 3100,
    "modelMetrics": {"gpt-5.4": {"usage": {"inputTokens": 15000, "outputTokens": 40, "reasoningTokens": 10}}},
}


def test_copilot_argv_disables_tools_instructions_and_mcp_servers():
    runner = Runner([_copilot_stdout()], usage=USAGE)
    judge = CopilotCliJudge(binary="copilot", runner=runner, clock=Clock(), disabled_mcp_servers=("lsp", "memory"))
    response = judge.judge(REQUEST)
    argv, cwd = runner.calls[0]
    assert argv[argv.index("-p") + 1] == SYSTEM_PROMPT + "\n\n" + render_user_prompt(REQUEST)
    assert argv[argv.index("--available-tools") + 1] == "--no-custom-instructions"
    assert "--disable-builtin-mcps" in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--disable-mcp-server"] == ["lsp", "memory"]
    assert Path(argv[argv.index("--usage-output-file") + 1]).parent == cwd
    assert response.model == "gpt-5.4"
    assert response.cost.usd == Decimal("0.24")
    assert response.cost.basis.endswith("_estimated")
    assert response.cost.units["premium_request_units"] == 6
    assert response.api_ms == 3100


def test_copilot_without_usage_file_keeps_cost_unknown():
    response = CopilotCliJudge(binary="copilot", runner=Runner([_copilot_stdout()]), clock=Clock()).judge(REQUEST)
    assert response.cost.usd is None and response.cost.basis == "copilot_usage_unavailable"


def test_copilot_without_assistant_message_is_invalid_output():
    runner = Runner(['{"type": "session.idle", "data": {}}'], usage=USAGE)
    with pytest.raises(JudgeError) as exc:
        CopilotCliJudge(binary="copilot", runner=runner, clock=Clock()).judge(REQUEST)
    assert exc.value.kind == "invalid_output"


def test_copilot_mcp_server_listing(tmp_path):
    config = tmp_path / "mcp-config.json"
    config.write_text(json.dumps({"mcpServers": {"b": {}, "a": {}}}))
    assert copilot_user_mcp_servers(config) == ["a", "b"]
    assert copilot_user_mcp_servers(tmp_path / "missing.json") == []
