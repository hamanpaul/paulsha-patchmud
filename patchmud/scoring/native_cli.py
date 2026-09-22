"""Pure argv construction and event parsing for native coding CLIs.

The provider process is deliberately outside this module.  Callers pass the
argv returned by :func:`build_native_argv` to :class:`~patchmud.sandbox.isolate.IsolationRunner`
and pass its :class:`~patchmud.sandbox.isolate.Execution` to
:func:`parse_native_execution`.  Keeping the protocol seam pure makes it
possible to test the native event contract without credentials or a provider
request.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from patchmud.sandbox.isolate import Execution

__all__ = ["build_native_argv", "parse_native_execution"]


_HARNESSES = frozenset({"codex", "agy"})
_CODEX_TOOL_ITEMS = frozenset(
    {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
    }
)
_MAX_ERROR_BYTES = 2000


def _profile_values(profile: Mapping[str, Any]) -> tuple[str, str, str]:
    if not isinstance(profile, Mapping):
        raise ValueError("native profile must be a mapping")

    # ``resolved`` is the provider identity selected by the catalog.  The
    # fallback keeps this pure seam useful for a small hand-built profile in
    # unit tests while never preferring the un-resolved request when both are
    # available.
    resolved = profile.get("resolved")
    requested = profile.get("requested")
    if not isinstance(resolved, Mapping):
        resolved = profile
    if not isinstance(requested, Mapping):
        requested = profile

    raw_harness = resolved.get("harness") or requested.get("harness")
    raw_model = resolved.get("model") or requested.get("model")
    raw_effort = resolved.get("effort") or requested.get("effort")
    harness = str(raw_harness).strip().lower() if raw_harness is not None else ""
    model = str(raw_model).strip() if raw_model is not None else ""
    effort = str(raw_effort).strip().lower() if raw_effort is not None else ""
    if harness not in _HARNESSES:
        raise ValueError(f"unsupported native harness: {raw_harness!r}")
    if not model:
        raise ValueError("native profile model must be non-empty")
    if not effort:
        raise ValueError("native profile effort must be non-empty")
    return harness, model, effort


def _validated_executable(executable: str) -> str:
    if not isinstance(executable, str) or not executable.strip():
        raise ValueError("native executable must be a non-empty string")
    if "\x00" in executable:
        raise ValueError("native executable contains NUL")
    return executable


def _validated_workspace(workspace: Path) -> str:
    try:
        path = Path(workspace)
    except TypeError as exc:
        raise ValueError("native workspace must be path-like") from exc
    value = str(path)
    if not value or "\x00" in value:
        raise ValueError("native workspace must be a valid path")
    return value


def _validated_session_id(session_id: str | None) -> str | None:
    if session_id is None:
        return None
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    if "\x00" in session_id:
        raise ValueError("session_id contains NUL")
    return session_id


def build_native_argv(
    profile: Mapping[str, Any],
    *,
    workspace: Path,
    executable: str,
    session_id: str | None = None,
) -> list[str]:
    """Build a native provider command whose prompt is read from stdin.

    The outer :class:`IsolationRunner` owns the process timeout, namespaces,
    network policy and filesystem mappings.  Codex therefore uses its
    externally-sandboxed approval switch.  Agy receives
    ``--dangerously-skip-permissions`` so native tools do not stop for an
    interactive approval prompt.  Its optional terminal sandbox is omitted:
    the outer ``IsolationRunner`` is the single filesystem/network boundary
    for the whole CLI process.
    """

    harness, model, effort = _profile_values(profile)
    binary = _validated_executable(executable)
    workdir = _validated_workspace(workspace)
    conversation = _validated_session_id(session_id)

    if harness == "codex":
        argv = [
            binary,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
        ]
        if conversation is None:
            # ``--cd`` is an initial-execution option.  On resume, the native
            # thread retains the workspace selected when it was created.
            argv += ["--cd", workdir]
        argv += ["--json"]
        if conversation is not None:
            # Codex's resume subcommand must follow all exec options.  The
            # trailing '-' keeps the next prompt out of argv.
            argv += ["resume", conversation, "-"]
        else:
            argv.append("-")
        return argv

    # Headless AGY's stdin protocol is NDJSON.  The caller sends one
    # ``{"event":"user","message":{"content": prompt}}`` record and
    # closes stdin after each native phase; ``--print`` is intentionally not
    # used because this CLI treats it as an option requiring an argv prompt.
    argv = [
        binary,
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        model,
        "--effort",
        effort,
        "--dangerously-skip-permissions",
        "--add-dir",
        workdir,
        "--disable-slash-commands",
    ]
    if conversation is not None:
        argv += ["--conversation", conversation]
    return argv


def _base_result(execution: Execution) -> dict[str, Any]:
    return {
        "status": "error",
        "end_reason": None,
        "error": None,
        "final_report": None,
        "native_session_id": None,
        "transcript": [],
        "events": [],
        "native_events": [],
        "usage": None,
        "wall_ms": int(execution.wall_ms),
        "cpu_ms": int(execution.cpu_ms),
        "exit_code": execution.exit_code,
        "timed_out": bool(execution.timed_out),
        "stdout": execution.stdout,
        "stderr": execution.stderr,
    }


def _error_message(*parts: Any, stderr: str = "") -> str:
    values: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, Mapping):
            message = part.get("message") or part.get("error") or part.get("type")
            if message is part:
                message = None
            if message is not None:
                values.append(str(message))
            elif part:
                values.append(json.dumps(dict(part), ensure_ascii=False, sort_keys=True))
        elif str(part).strip():
            values.append(str(part).strip())
    if stderr.strip():
        values.append(stderr.strip())
    message = "; ".join(values) or "native CLI failed without an error message"
    return message[:_MAX_ERROR_BYTES]


def _incomplete_json_tail(text: str, error: json.JSONDecodeError) -> bool:
    """Recognize a truncated JSON container, while rejecting arbitrary text.

    A timeout may cut the final NDJSON record after the provider has emitted a
    valid prefix.  We only tolerate a final line whose opening container (or
    string) is unfinished and can be completed as JSON.  A plain diagnostic,
    invalid token, extra data, or a valid non-object is a protocol error even
    when the process hit the wall deadline.
    """

    value = text.strip()
    if not value or value[0] not in "[{" or error.msg == "Extra data":
        return False

    stack: list[str] = []
    in_string = False
    escaped = False
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack or (char == "]" and stack[-1] != "[") or (
                char == "}" and stack[-1] != "{"
            ):
                return False
            stack.pop()

    if not stack and not in_string:
        return False
    closing = "".join("]" if char == "[" else "}" for char in reversed(stack))
    quote = '"' if in_string else ""
    candidates = (value + quote + closing, value + quote + "null" + closing)
    return any(
        _valid_json_candidate(candidate) for candidate in candidates
    )


def _valid_json_candidate(value: str) -> bool:
    try:
        json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return False
    return True


def _json_records(stdout: str) -> tuple[list[Any], int, bool]:
    records: list[Any] = []
    malformed = 0
    incomplete_tail = False
    lines = stdout.splitlines()
    nonempty_indexes = [index for index, line in enumerate(lines) if line.strip()]
    last_nonempty = nonempty_indexes[-1] if nonempty_indexes else None
    for index, line in enumerate(lines):
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
            records.append(value)
            if not isinstance(value, Mapping):
                malformed += 1
        except json.JSONDecodeError as error:
            malformed += 1
            if index == last_nonempty and _incomplete_json_tail(text, error):
                incomplete_tail = True
        except TypeError:
            malformed += 1
    return records, malformed, incomplete_tail


_MISSING = object()


def _tool_event(
    events: list[dict[str, Any]],
    action: str,
    status: Any,
    output: Any,
    *,
    step_index: Any = _MISSING,
) -> None:
    event = {
        "evidence_id": f"native-{len(events) + 1}",
        "kind": "native_tool",
        "action": action,
        "status": str(status).lower() if status is not None else "unknown",
        "output": copy.deepcopy(output),
    }
    if step_index is not _MISSING:
        event["step_index"] = copy.deepcopy(step_index)
    events.append(event)


def _merge_tool_snapshots(previous: Any, latest: Any) -> Any:
    """Merge lifecycle snapshots without losing a command or its output.

    AGY's ACTIVE snapshot commonly has parameters while DONE has the output;
    recursively retaining keys absent from the latest snapshot preserves both
    pieces.  Values present in the latest snapshot always win, including an
    explicit empty string.  Lists and scalar values are provider snapshots and
    therefore replace their earlier value in full.
    """

    if isinstance(previous, Mapping) and isinstance(latest, Mapping):
        merged = {key: copy.deepcopy(value) for key, value in previous.items()}
        for key, value in latest.items():
            if key in merged:
                merged[key] = _merge_tool_snapshots(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(latest)


def _step_identity(step_index: Any) -> tuple[str, str]:
    """Return a stable map key while keeping e.g. integer and string indexes distinct."""

    return (type(step_index).__name__, repr(step_index))


def _record_agy_tool_event(
    events: list[dict[str, Any]],
    indexed_events: dict[tuple[str, str], int],
    update: Mapping[str, Any],
    tool_name: Any,
    tool_status: Any,
    step_index: Any,
) -> None:
    """Add an AGY tool update or fold it into the same step lifecycle."""

    action = str(tool_name or "unknown_tool")
    key = _step_identity(step_index)
    existing = indexed_events.get(key)
    if existing is None:
        _tool_event(
            events,
            action,
            tool_status,
            dict(update),
            step_index=step_index,
        )
        indexed_events[key] = len(events) - 1
        return

    event = events[existing]
    if tool_name is not None:
        event["action"] = action
    event["status"] = str(tool_status).lower() if tool_status is not None else event["status"]
    event["output"] = _merge_tool_snapshots(event["output"], dict(update))


def _usage(
    provider: str,
    raw: Mapping[str, Any],
    *,
    scope: str,
    semantics: str,
) -> dict[str, Any]:
    """Retain raw usage while making continuation accounting explicit.

    Codex reports usage for the completed turn, so it is a delta.  Agy reports
    usage for the native conversation, so it is cumulative.  No synthetic
    subtraction is attempted because the parser receives no prior phase
    usage.
    """

    cumulative = dict(raw) if semantics == "cumulative" else None
    delta = dict(raw) if semantics == "delta" else None
    return {
        "provider": provider,
        "scope": scope,
        "semantics": semantics,
        "is_cumulative": semantics == "cumulative",
        "delta": delta,
        "cumulative": cumulative,
        "raw": dict(raw),
    }


def _codex_message_texts(
    message_order: list[str], message_texts: dict[str, str], item: Mapping[str, Any]
) -> None:
    text = item.get("text")
    if not isinstance(text, str) or not text.strip():
        return
    identifier = item.get("id")
    key = str(identifier) if identifier is not None else f"anonymous-{len(message_order)}"
    if key not in message_texts:
        message_order.append(key)
    # Codex item.updated events carry the current message text.  Replacing by
    # id avoids duplicating an item when its completed form follows updates.
    message_texts[key] = text.strip()


def _parse_codex(execution: Execution) -> dict[str, Any]:
    result = _base_result(execution)
    records, malformed, incomplete_tail = _json_records(execution.stdout)
    result["native_events"] = records

    message_order: list[str] = []
    message_texts: dict[str, str] = {}
    provider_errors: list[Any] = []
    turn_completed = False
    usage_raw: Mapping[str, Any] | None = None

    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        kind = raw.get("type")
        if kind == "thread.started":
            thread_id = raw.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                result["native_session_id"] = thread_id
            else:
                malformed += 1
        elif kind in {"item.started", "item.updated", "item.completed"}:
            item = raw.get("item")
            if not isinstance(item, Mapping):
                malformed += 1
                continue
            item_type = item.get("type")
            if item_type == "agent_message":
                _codex_message_texts(message_order, message_texts, item)
            elif item_type in _CODEX_TOOL_ITEMS:
                _tool_event(
                    result["events"],
                    str(item_type),
                    item.get("status")
                    or (
                        "completed"
                        if kind == "item.completed"
                        else kind.removeprefix("item.")
                    ),
                    dict(item),
                )
            elif item_type == "error":
                provider_errors.append(item.get("message") or item)
        elif kind == "turn.completed":
            if turn_completed:
                malformed += 1
                continue
            turn_completed = True
            candidate = raw.get("usage")
            if not isinstance(candidate, Mapping):
                malformed += 1
            else:
                usage_raw = candidate
        elif kind == "turn.failed":
            provider_errors.append(raw.get("error") or raw.get("message") or raw)
        elif kind == "error":
            provider_errors.append(raw.get("message") or raw.get("error") or raw)

    result["transcript"] = [
        {"role": "assistant", "content": message_texts[key]} for key in message_order
    ]
    if message_order:
        result["final_report"] = message_texts[message_order[-1]]
    if usage_raw is not None:
        result["usage"] = _usage(
            "codex", usage_raw, scope="turn", semantics="delta"
        )

    # A wall deadline is a normal budget outcome for native scoring.  Preserve
    # all parseable partial evidence and ignore a truncated final JSON line.
    # An explicit provider error is stronger evidence than the outer deadline.
    if execution.timed_out:
        if provider_errors:
            result.update(
                status="error",
                end_reason="provider_error",
                error=_error_message(*provider_errors, stderr=execution.stderr),
            )
            return result
        if malformed and not (malformed == 1 and incomplete_tail):
            result.update(
                status="error",
                end_reason="protocol_error",
                error=_error_message(
                    "codex emitted malformed or invalid event data",
                    stderr=execution.stderr,
                ),
            )
            return result
        result.update(
            status="budget_exhausted",
            end_reason="wall_clock",
            error=None,
        )
        return result
    if provider_errors:
        result.update(
            status="error",
            end_reason="provider_error",
            error=_error_message(*provider_errors, stderr=execution.stderr),
        )
        return result
    if execution.exit_code < 0:
        result.update(
            status="error",
            end_reason="interrupted",
            error=_error_message(
                f"native codex process terminated by signal {-execution.exit_code}",
                stderr=execution.stderr,
            ),
        )
        return result
    if execution.exit_code != 0:
        result.update(
            status="error",
            end_reason="nonzero_exit",
            error=_error_message(
                f"native codex exited with status {execution.exit_code}",
                *provider_errors,
                stderr=execution.stderr,
            ),
        )
        return result
    if malformed:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                f"codex emitted {malformed} malformed or invalid event line(s)",
                stderr=execution.stderr,
            ),
        )
        return result
    if not turn_completed:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                "codex output has no turn.completed event",
                stderr=execution.stderr,
            ),
        )
        return result
    if not result["final_report"]:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                "codex turn completed without an agent report",
                stderr=execution.stderr,
            ),
        )
        return result
    if result["native_session_id"] is None:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                "codex turn completed without thread.started.thread_id",
                stderr=execution.stderr,
            ),
        )
        return result
    result.update(status="completed", end_reason="completed")
    return result


def _agy_result_reason(status: str) -> str:
    normalized = status.upper()
    if normalized == "ERROR":
        return "provider_error"
    if normalized in {"CANCELED", "CANCELLED", "INTERRUPTED"}:
        return "interrupted"
    if normalized == "INVALID":
        return "protocol_error"
    if normalized in {"WAITING", "RUNNING"}:
        return "incomplete"
    return "provider_error"


def _agy_error_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("message") or value.get("error") or value
    return value


def _agy_success_schema_error(terminal: Mapping[str, Any] | None) -> str | None:
    """Validate fields required by a complete AGY SUCCESS result."""

    if terminal is None or str(terminal.get("status") or "").upper() != "SUCCESS":
        return None
    response = terminal.get("response")
    if not isinstance(response, str) or not response.strip():
        return "agy SUCCESS result has no response"
    conversation_id = terminal.get("conversation_id")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return "agy SUCCESS result has no conversation_id"
    usage = terminal.get("usage")
    if not isinstance(usage, Mapping):
        return "agy SUCCESS result has no usage metadata"
    return None


def _parse_agy(execution: Execution) -> dict[str, Any]:
    result = _base_result(execution)
    records, malformed, incomplete_tail = _json_records(execution.stdout)
    result["native_events"] = records

    text_parts: list[str] = []
    provider_errors: list[Any] = []
    terminal: Mapping[str, Any] | None = None
    indexed_tool_events: dict[tuple[str, str], int] = {}

    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        kind = raw.get("event")
        if kind == "init":
            conversation_id = raw.get("conversation_id")
            if isinstance(conversation_id, str) and conversation_id.strip():
                result["native_session_id"] = conversation_id
            else:
                malformed += 1
        elif kind == "step_update":
            update = raw.get("step_update")
            if not isinstance(update, Mapping):
                malformed += 1
                continue
            conversation_id = update.get("conversation_id")
            if (
                result["native_session_id"] is None
                and isinstance(conversation_id, str)
                and conversation_id.strip()
            ):
                result["native_session_id"] = conversation_id
            step_type = str(update.get("step_type") or "").lower()
            if (
                step_type == "tool"
                or update.get("tool_name") is not None
                or update.get("tool_info") is not None
            ):
                tool_info = update.get("tool_info")
                tool_name = update.get("tool_name")
                if tool_name is None and isinstance(tool_info, Mapping):
                    tool_name = tool_info.get("name")
                tool_status = update.get("state") or (
                    tool_info.get("status") if isinstance(tool_info, Mapping) else None
                )
                if "step_index" in update and update.get("step_index") is not None:
                    _record_agy_tool_event(
                        result["events"],
                        indexed_tool_events,
                        update,
                        tool_name,
                        tool_status,
                        update["step_index"],
                    )
                else:
                    _tool_event(
                        result["events"],
                        str(tool_name or "unknown_tool"),
                        tool_status,
                        dict(update),
                    )
            text = update.get("text_delta")
            if isinstance(text, str) and text:
                text_parts.append(text)
            step_error = update.get("error")
            if step_error:
                provider_errors.append(_agy_error_value(step_error))
        elif kind == "result":
            if terminal is not None:
                malformed += 1
                continue
            candidate = raw.get("result")
            if not isinstance(candidate, Mapping):
                malformed += 1
                continue
            terminal = candidate
            conversation_id = candidate.get("conversation_id")
            if isinstance(conversation_id, str) and conversation_id.strip():
                result["native_session_id"] = conversation_id
            candidate_error = candidate.get("error")
            if candidate_error:
                provider_errors.append(_agy_error_value(candidate_error))
            usage_raw = candidate.get("usage")
            if isinstance(usage_raw, Mapping):
                result["usage"] = _usage(
                    "agy", usage_raw, scope="conversation", semantics="cumulative"
                )
            elif str(candidate.get("status", "")).upper() == "SUCCESS":
                malformed += 1
        # Unknown event types remain in native_events.  Provider versions can
        # add informational events without making a valid terminal result
        # unusable.

    if text_parts:
        result["transcript"] = [
            {"role": "assistant", "content": "".join(text_parts).strip()}
        ]
    if terminal is not None:
        response = terminal.get("response")
        if isinstance(response, str) and response.strip():
            result["final_report"] = response.strip()
            result["transcript"] = [{"role": "assistant", "content": response.strip()}]
        elif result["transcript"]:
            result["final_report"] = result["transcript"][-1]["content"]
    elif result["transcript"]:
        # A wall timeout can end after response deltas but before AGY emits
        # its terminal result.  Keep that partial report judgeable.
        result["final_report"] = result["transcript"][-1]["content"]

    terminal_status = (
        str(terminal.get("status") or "").upper() if terminal is not None else ""
    )
    success_schema_error = _agy_success_schema_error(terminal)

    # As with Codex, the outer wall deadline is a judgeable partial outcome;
    # an explicit provider error or terminal non-success turns it into
    # infrastructure/protocol failure.
    if execution.timed_out:
        if provider_errors:
            result.update(
                status="error",
                end_reason="provider_error",
                error=_error_message(*provider_errors, stderr=execution.stderr),
            )
            return result
        if terminal is not None and terminal_status != "SUCCESS":
            result.update(
                status="error",
                end_reason=_agy_result_reason(terminal_status),
                error=_error_message(
                    f"agy returned status {terminal_status or '<missing>'}",
                    stderr=execution.stderr,
                ),
            )
            return result
        if success_schema_error is not None:
            result.update(
                status="error",
                end_reason="protocol_error",
                error=_error_message(success_schema_error, stderr=execution.stderr),
            )
            return result
        if malformed and not (malformed == 1 and incomplete_tail):
            result.update(
                status="error",
                end_reason="protocol_error",
                error=_error_message(
                    "agy emitted malformed or invalid event data",
                    stderr=execution.stderr,
                ),
            )
            return result
        result.update(
            status="budget_exhausted",
            end_reason="wall_clock",
            error=None,
        )
        return result
    if provider_errors:
        result.update(
            status="error",
            end_reason="provider_error",
            error=_error_message(*provider_errors, stderr=execution.stderr),
        )
        return result
    if execution.exit_code < 0:
        result.update(
            status="error",
            end_reason="interrupted",
            error=_error_message(
                f"native agy process terminated by signal {-execution.exit_code}",
                stderr=execution.stderr,
            ),
        )
        return result
    if execution.exit_code != 0:
        result.update(
            status="error",
            end_reason="nonzero_exit",
            error=_error_message(
                f"native agy exited with status {execution.exit_code}",
                *provider_errors,
                stderr=execution.stderr,
            ),
        )
        return result
    if malformed:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                f"agy emitted {malformed} malformed or invalid event line(s)",
                stderr=execution.stderr,
            ),
        )
        return result
    if terminal is None:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                "agy output has no result event",
                stderr=execution.stderr,
            ),
        )
        return result

    status = terminal_status
    if status != "SUCCESS":
        result.update(
            status="error",
            end_reason=_agy_result_reason(status),
            error=_error_message(
                f"agy returned status {status or '<missing>'}",
                *provider_errors,
                stderr=execution.stderr,
            ),
        )
        return result
    if success_schema_error is not None:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(success_schema_error, stderr=execution.stderr),
        )
        return result
    if not result["final_report"]:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                "agy SUCCESS result has no response",
                stderr=execution.stderr,
            ),
        )
        return result
    if result["usage"] is None:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message(
                "agy SUCCESS result has no usage metadata",
                stderr=execution.stderr,
            ),
        )
        return result
    if result["native_session_id"] is None:
        result.update(
            status="error",
            end_reason="protocol_error",
            error=_error_message("agy result has no conversation_id", stderr=execution.stderr),
        )
        return result
    result.update(status="completed", end_reason="completed")
    return result


def parse_native_execution(harness: str, execution: Execution) -> dict[str, Any]:
    """Parse one captured native CLI execution into the scoring record.

    Provider failures, malformed protocol output, signals and outer timeout
    are all returned as ``status='error'`` with a distinct ``end_reason``.
    They must be stopped by the native runner before judging; they are never a
    low-scoring model answer.  Partial transcript/events/session/usage fields
    are retained whenever the process emitted them before failure.
    """

    normalized = str(harness).strip().lower()
    if normalized == "codex":
        return _parse_codex(execution)
    if normalized == "agy":
        return _parse_agy(execution)
    raise ValueError(f"unsupported native harness: {harness!r}")
