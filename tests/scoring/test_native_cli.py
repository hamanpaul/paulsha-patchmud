"""Pure native CLI argv and event-protocol tests.

These tests never start a provider process.  The process boundary belongs to
``IsolationRunner``; this module only translates the resolved profile and
normalises the provider's captured output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from patchmud.sandbox.isolate import Execution
from patchmud.scoring.native_cli import build_native_argv, parse_native_execution


def _profile(harness: str, model: str, effort: str) -> dict:
    return {
        "resolved": {"harness": harness, "model": model, "effort": effort},
        "requested": {"harness": harness, "model": model, "effort": effort},
    }


def _execution(
    records: list[dict] | None = None,
    *,
    exit_code: int = 0,
    stderr: str = "",
    timed_out: bool = False,
    wall_ms: int = 37,
) -> Execution:
    stdout = "" if records is None else "\n".join(json.dumps(item) for item in records) + "\n"
    return Execution(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        wall_ms=wall_ms,
        cpu_ms=11,
        timed_out=timed_out,
    )


def test_codex_argv_initial_and_resume_uses_stdin_and_external_sandbox(tmp_path: Path) -> None:
    profile = _profile("codex", "gpt-5.6-luna", "max")
    initial = build_native_argv(
        profile, workspace=tmp_path, executable="/opt/patchmud-native/bin/codex"
    )
    assert initial == [
        "/opt/patchmud-native/bin/codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-m",
        "gpt-5.6-luna",
        "-c",
        'model_reasoning_effort="max"',
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--cd",
        str(tmp_path),
        "--json",
        "-",
    ]

    resumed = build_native_argv(
        profile,
        workspace=tmp_path,
        executable="/opt/patchmud-native/bin/codex",
        session_id="thread-1",
    )
    assert resumed == [
        "/opt/patchmud-native/bin/codex",
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "-m",
        "gpt-5.6-luna",
        "-c",
        'model_reasoning_effort="max"',
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--json",
        "resume",
        "thread-1",
        "-",
    ]


def test_agy_argv_uses_text_stdin_stream_events_and_conversation_resume(tmp_path: Path) -> None:
    profile = _profile("agy", "gemini-3.8-flash-high", "high")
    initial = build_native_argv(
        profile, workspace=tmp_path, executable="/opt/patchmud-native/bin/agy"
    )
    assert initial == [
        "/opt/patchmud-native/bin/agy",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        "gemini-3.8-flash-high",
        "--effort",
        "high",
        "--dangerously-skip-permissions",
        "--add-dir",
        str(tmp_path),
        "--disable-slash-commands",
    ]
    resumed = build_native_argv(
        profile,
        workspace=tmp_path,
        executable="/opt/patchmud-native/bin/agy",
        session_id="conversation-1",
    )
    assert resumed[-2:] == ["--conversation", "conversation-1"]
    assert resumed[resumed.index("--input-format") + 1] == "stream-json"
    assert "--print" not in resumed


def test_agy_uses_ndjson_stdin_without_print_prompt_option(tmp_path: Path) -> None:
    argv = build_native_argv(
        _profile("agy", "gemini-3.8-flash-high", "high"),
        workspace=tmp_path,
        executable="/opt/patchmud-native/bin/agy",
    )
    assert "--print" not in argv
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert argv[argv.index("--output-format") + 1] == "stream-json"


def test_agy_observed_print_option_error_is_nonzero_process_failure() -> None:
    result = parse_native_execution(
        "agy",
        _execution(
            exit_code=2,
            stderr=(
                'Error: --print took "--input-format" as its prompt; '
                "the intended prompt was left as an argument and ignored."
            ),
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "nonzero_exit"
    assert "--print" in result["error"]
    assert result["stderr"] == result["error"].split("; ", 1)[-1]


def test_native_argv_rejects_missing_or_invalid_continuation() -> None:
    with pytest.raises(ValueError, match="unsupported native harness"):
        build_native_argv(
            _profile("unknown", "model", "high"),
            workspace=Path("/repo"),
            executable="native",
        )
    with pytest.raises(ValueError, match="session_id"):
        build_native_argv(
            _profile("codex", "gpt-5.6-luna", "max"),
            workspace=Path("/repo"),
            executable="native",
            session_id="",
        )


def test_codex_success_keeps_tool_events_transcript_session_and_usage() -> None:
    usage = {"input_tokens": 21, "output_tokens": 8}
    records = [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "msg-1", "type": "agent_message", "text": "I inspected the fixture."},
        },
        {
            "type": "item.completed",
            "item": {
                "id": "cmd-1",
                "type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": "2 passed",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "msg-2", "type": "agent_message", "text": "All checks passed."},
        },
        {"type": "turn.completed", "usage": usage},
    ]

    result = parse_native_execution("codex", _execution(records))

    assert result["status"] == "completed"
    assert result["end_reason"] == "completed"
    assert result["error"] is None
    assert result["final_report"] == "All checks passed."
    assert result["native_session_id"] == "thread-1"
    assert result["wall_ms"] == 37
    assert [message["content"] for message in result["transcript"]] == [
        "I inspected the fixture.",
        "All checks passed.",
    ]
    assert len(result["native_events"]) == len(records)
    assert result["native_events"][3]["item"]["id"] == "cmd-1"
    assert len(result["events"]) == 1
    assert result["events"][0]["evidence_id"] == "native-1"
    assert result["events"][0]["kind"] == "native_tool"
    assert result["events"][0]["action"] == "command_execution"
    assert result["events"][0]["status"] == "completed"
    assert result["usage"] == {
        "provider": "codex",
        "scope": "turn",
        "semantics": "delta",
        "is_cumulative": False,
        "delta": usage,
        "cumulative": None,
        "raw": usage,
    }


def test_codex_turn_failed_is_provider_error_and_not_a_scoreable_completion() -> None:
    result = parse_native_execution(
        "codex",
        _execution(
            [
                {"type": "thread.started", "thread_id": "thread-2"},
                {"type": "turn.failed", "error": {"message": "provider unavailable"}},
            ]
        ),
    )

    assert result["status"] == "error"
    assert result["end_reason"] == "provider_error"
    assert "provider unavailable" in result["error"]
    assert result["native_session_id"] == "thread-2"
    assert result["final_report"] is None


@pytest.mark.parametrize(
    ("execution", "status", "reason", "error"),
    [
        (
            _execution([{"type": "thread.started", "thread_id": "t"}], exit_code=9),
            "error",
            "nonzero_exit",
            True,
        ),
        (
            _execution([{"type": "thread.started", "thread_id": "t"}], exit_code=-2),
            "error",
            "interrupted",
            True,
        ),
        (
            _execution([{"type": "thread.started", "thread_id": "t"}], timed_out=True),
            "budget_exhausted",
            "wall_clock",
            False,
        ),
    ],
)
def test_codex_process_failures_preserve_partial_evidence(
    execution: Execution,
    status: str,
    reason: str,
    error: bool,
) -> None:
    result = parse_native_execution("codex", execution)
    assert result["status"] == status
    assert result["end_reason"] == reason
    assert result["native_session_id"] == "t"
    assert result["wall_ms"] == 37
    assert bool(result["error"]) is error
    assert result["cpu_ms"] == 11
    assert result["exit_code"] == execution.exit_code
    assert result["timed_out"] is execution.timed_out
    assert result["stdout"] == execution.stdout
    assert result["stderr"] == execution.stderr


def test_codex_provider_error_wins_over_wall_deadline() -> None:
    result = parse_native_execution(
        "codex",
        _execution(
            [
                {"type": "thread.started", "thread_id": "t"},
                {"type": "turn.failed", "error": {"message": "upstream unavailable"}},
            ],
            timed_out=True,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "provider_error"
    assert "upstream unavailable" in result["error"]


def test_malformed_codex_output_is_protocol_error() -> None:
    result = parse_native_execution(
        "codex",
        Execution(
            exit_code=0,
            stdout='{"type":"thread.started","thread_id":"t"}\nnot-json\n',
            stderr="",
            wall_ms=9,
            cpu_ms=1,
            timed_out=False,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "protocol_error"
    assert result["native_session_id"] == "t"


def test_codex_timeout_rejects_middle_malformed_json_instead_of_scoring() -> None:
    records = [
        {"type": "thread.started", "thread_id": "t-mid"},
        {
            "type": "item.completed",
            "item": {"id": "m", "type": "agent_message", "text": "partial"},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]
    stdout = "\n".join(
        [json.dumps(records[0]), "NOT_JSON_MIDSTREAM", *(json.dumps(item) for item in records[1:])]
    ) + "\n"
    result = parse_native_execution(
        "codex",
        Execution(
            exit_code=0,
            stdout=stdout,
            stderr="",
            wall_ms=13,
            cpu_ms=2,
            timed_out=True,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "protocol_error"
    assert result["final_report"] == "partial"
    assert result["stdout"] == stdout


def test_codex_timeout_accepts_only_a_final_incomplete_json_tail() -> None:
    complete = [
        {"type": "thread.started", "thread_id": "t-tail"},
        {
            "type": "item.completed",
            "item": {"id": "m", "type": "agent_message", "text": "partial"},
        },
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ]
    stdout = "\n".join(json.dumps(item) for item in complete)
    stdout += '\n{"type":"item.completed","item":'
    result = parse_native_execution(
        "codex",
        Execution(
            exit_code=0,
            stdout=stdout,
            stderr="",
            wall_ms=13,
            cpu_ms=2,
            timed_out=True,
        ),
    )
    assert result["status"] == "budget_exhausted"
    assert result["end_reason"] == "wall_clock"
    assert result["error"] is None
    assert result["final_report"] == "partial"
    assert result["stdout"] == stdout


def test_agy_success_keeps_native_tool_events_and_marks_usage_cumulative() -> None:
    usage = {"input_tokens": 34, "output_tokens": 13, "total_tokens": 47}
    records = [
        {"event": "init", "conversation_id": "conversation-1", "init": {"tools": ["shell"]}},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conversation-1",
                "step_index": 1,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "shell",
                "tool_info": {
                    "name": "shell",
                    "parameters": {"command": "pytest -q"},
                    "output": "2 passed",
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conversation-1",
                "step_index": 2,
                "state": "DONE",
                "step_type": "response",
                "text_delta": "Implemented and verified.",
            },
        },
        {
            "event": "result",
            "result": {
                "conversation_id": "conversation-1",
                "status": "SUCCESS",
                "response": "Implemented and verified.",
                "usage": usage,
            },
        },
    ]

    result = parse_native_execution("agy", _execution(records))

    assert result["status"] == "completed"
    assert result["end_reason"] == "completed"
    assert result["native_session_id"] == "conversation-1"
    assert result["final_report"] == "Implemented and verified."
    assert result["transcript"] == [{"role": "assistant", "content": "Implemented and verified."}]
    assert result["events"][0]["evidence_id"] == "native-1"
    assert result["events"][0]["action"] == "shell"
    assert result["usage"] == {
        "provider": "agy",
        "scope": "conversation",
        "semantics": "cumulative",
        "is_cumulative": True,
        "delta": None,
        "cumulative": usage,
        "raw": usage,
    }


def test_agy_tool_lifecycle_merges_same_step_without_merging_other_steps() -> None:
    records = [
        {"event": "init", "conversation_id": "conversation-lifecycle"},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conversation-lifecycle",
                "step_index": 7,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "parameters": {"CommandLine": "pytest -q"},
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conversation-lifecycle",
                "step_index": 7,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "output": "all tests passed\n" * 100,
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conversation-lifecycle",
                "step_index": 8,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "parameters": {"CommandLine": "pytest -q"},
                    "output": "same command, different step\n",
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "conversation-lifecycle",
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {"name": "run_command", "output": "no index\n"},
            },
        },
        {
            "event": "result",
            "result": {
                "conversation_id": "conversation-lifecycle",
                "status": "SUCCESS",
                "response": "done",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        },
    ]

    result = parse_native_execution("agy", _execution(records))

    assert result["status"] == "completed"
    assert len(result["native_events"]) == len(records)
    assert len(result["events"]) == 3
    indexed = {event["step_index"]: event for event in result["events"] if "step_index" in event}
    assert indexed[7]["status"] == "done"
    assert indexed[7]["output"]["tool_info"]["parameters"] == {
        "CommandLine": "pytest -q"
    }
    assert indexed[7]["output"]["tool_info"]["output"] == "all tests passed\n" * 100
    assert indexed[8]["output"]["tool_info"]["parameters"] == {
        "CommandLine": "pytest -q"
    }
    assert sum("step_index" not in event for event in result["events"]) == 1


def test_agy_non_success_result_is_provider_error() -> None:
    result = parse_native_execution(
        "agy",
        _execution(
            [
                {"event": "init", "conversation_id": "c-2"},
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "c-2",
                        "status": "ERROR",
                        "response": "partial",
                        "error": {"type": "provider", "message": "quota exceeded"},
                        "usage": {"input_tokens": 2},
                    },
                },
            ]
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "provider_error"
    assert "quota exceeded" in result["error"]
    assert result["final_report"] == "partial"
    assert result["usage"]["is_cumulative"] is True


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "status", "reason"),
    [
        (7, False, "error", "nonzero_exit"),
        (-2, False, "error", "interrupted"),
        (0, True, "budget_exhausted", "wall_clock"),
    ],
)
def test_agy_process_failures_are_classified(
    exit_code: int, timed_out: bool, status: str, reason: str
) -> None:
    result = parse_native_execution(
        "agy",
        _execution(
            [{"event": "init", "conversation_id": "c-3"}],
            exit_code=exit_code,
            timed_out=timed_out,
        ),
    )
    assert result["status"] == status
    assert result["end_reason"] == reason
    assert result["native_session_id"] == "c-3"


def test_agy_provider_error_wins_over_wall_deadline() -> None:
    result = parse_native_execution(
        "agy",
        _execution(
            [
                {"event": "init", "conversation_id": "c-5"},
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "c-5",
                        "status": "ERROR",
                        "error": {"message": "upstream unavailable"},
                    },
                },
            ],
            timed_out=True,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "provider_error"
    assert "upstream unavailable" in result["error"]


@pytest.mark.parametrize("terminal_status", ["ERROR", "INVALID"])
def test_agy_terminal_non_success_wins_over_wall_deadline_without_error_field(
    terminal_status: str,
) -> None:
    result = parse_native_execution(
        "agy",
        _execution(
            [
                {"event": "init", "conversation_id": "c-terminal-error"},
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "c-terminal-error",
                        "status": terminal_status,
                        "response": "partial",
                        "usage": {"input_tokens": 1},
                    },
                },
            ],
            timed_out=True,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == (
        "provider_error" if terminal_status == "ERROR" else "protocol_error"
    )
    assert result["error"]
    assert result["final_report"] == "partial"
    assert result["usage"]["raw"] == {"input_tokens": 1}


@pytest.mark.parametrize(
    "terminal_result",
    [
        {
            "conversation_id": "c-schema",
            "status": "SUCCESS",
            "usage": {"input_tokens": 1},
        },
        {
            "status": "SUCCESS",
            "response": "partial",
            "usage": {"input_tokens": 1},
        },
    ],
)
def test_agy_success_terminal_schema_is_validated_before_timeout_fallback(
    terminal_result: dict,
) -> None:
    result = parse_native_execution(
        "agy",
        _execution(
            [{"event": "result", "result": terminal_result}],
            timed_out=True,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "protocol_error"
    assert result["error"]


def test_malformed_agy_output_is_protocol_error() -> None:
    result = parse_native_execution(
        "agy",
        Execution(
            exit_code=0,
            stdout='{"event":"init","conversation_id":"c-4"}\n{"event":"result"}\n',
            stderr="",
            wall_ms=10,
            cpu_ms=1,
            timed_out=False,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "protocol_error"
    assert result["native_session_id"] == "c-4"


def test_agy_timeout_rejects_middle_nonobject_event_instead_of_scoring() -> None:
    records = [
        {"event": "init", "conversation_id": "c-mid"},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "c-mid",
                "step_index": 1,
                "state": "DONE",
                "step_type": "response",
                "text_delta": "partial",
            },
        },
        {
            "event": "result",
            "result": {
                "conversation_id": "c-mid",
                "status": "SUCCESS",
                "response": "partial",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        },
    ]
    stdout = "\n".join(
        [
            json.dumps(records[0]),
            json.dumps("NONOBJECT_MIDSTREAM"),
            *(json.dumps(item) for item in records[1:]),
        ]
    ) + "\n"
    result = parse_native_execution(
        "agy",
        Execution(
            exit_code=0,
            stdout=stdout,
            stderr="",
            wall_ms=13,
            cpu_ms=2,
            timed_out=True,
        ),
    )
    assert result["status"] == "error"
    assert result["end_reason"] == "protocol_error"
    assert result["final_report"] == "partial"
    assert result["stdout"] == stdout


def test_agy_timeout_accepts_only_a_final_incomplete_json_tail() -> None:
    complete = [
        {"event": "init", "conversation_id": "c-tail"},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "c-tail",
                "step_index": 1,
                "state": "DONE",
                "step_type": "response",
                "text_delta": "partial",
            },
        },
    ]
    stdout = "\n".join(json.dumps(item) for item in complete)
    stdout += '\n{"event":"result","result":'
    result = parse_native_execution(
        "agy",
        Execution(
            exit_code=0,
            stdout=stdout,
            stderr="",
            wall_ms=13,
            cpu_ms=2,
            timed_out=True,
        ),
    )
    assert result["status"] == "budget_exhausted"
    assert result["end_reason"] == "wall_clock"
    assert result["error"] is None
    assert result["final_report"] == "partial"
    assert result["stdout"] == stdout
