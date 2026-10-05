"""builder session 的 ``codex exec --json`` 事件解析與 provider 身分觀測。

一次 builder session 是**一次** ``codex exec``（Cortex 一張卡一個 job）：

- ``thread.started``：必須恰好一個合法 thread id，才拿去 ``thread/read``。
- ``turn.completed.usage``：一次 exec 一個 turn；usage 原樣交給 ledger 的 ``codex``
  mapper（多於一個或缺漏 → fail-closed，ledger 無從計費）。
- ``item.completed``：``command_execution`` 計數（ledger tool_calls）、最後一則
  ``agent_message`` 作為 session 摘要；``error``／``turn.failed`` 事件記成 session 失敗。

provider 身分只採 ``codex app-server`` 的 ``thread/read`` 回讀值（同 #44 規則）：
讀到 id 相符、model／reasoningEffort／modelProvider 皆為字串且 provider 是
``openai`` 才標 known；evidence 只存 thread id 的 SHA-256 與回報值。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from patchmud.adapters.codex_cli import EXPECTED_MODEL_PROVIDER, THREAD_IDENTITY_SOURCE
from patchmud.adapters.observation import RuntimeObservation

__all__ = [
    "SessionParseError",
    "SessionTranscript",
    "observe_thread",
    "parse_session",
]

_THREAD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}")
_COMMAND_PREVIEW_CHARS = 200


class SessionParseError(ValueError):
    """codex 事件串缺必要事件（fail-closed）。"""


@dataclass(frozen=True)
class SessionTranscript:
    thread_id: str | None
    usage: Mapping[str, object] | None
    last_message: str | None
    command_count: int
    commands: tuple[str, ...]
    failures: tuple[str, ...]
    event_count: int
    stdout_sha256: str
    extra: Mapping[str, object] = field(default_factory=dict)


def _iter_events(stdout: str):
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            yield parsed


def parse_session(stdout: str) -> SessionTranscript:
    thread_ids: set[str] = set()
    usages: list[Mapping[str, object]] = []
    last_message: str | None = None
    commands: list[str] = []
    failures: list[str] = []
    count = 0
    for event in _iter_events(stdout):
        count += 1
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and _THREAD_ID_RE.fullmatch(thread_id):
                thread_ids.add(thread_id)
            else:
                thread_ids.add("")
        elif kind == "turn.completed":
            usage = event.get("usage")
            if isinstance(usage, dict):
                usages.append(usage)
        elif kind in ("turn.failed", "error"):
            detail = event.get("message") or event.get("error") or kind
            failures.append(str(detail)[:300])
        elif kind == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "agent_message" and isinstance(item.get("text"), str):
                last_message = item["text"]
            elif item_type == "command_execution":
                command = item.get("command")
                commands.append(str(command)[:_COMMAND_PREVIEW_CHARS])
    unique = next(iter(thread_ids)) if len(thread_ids) == 1 else ""
    return SessionTranscript(
        thread_id=unique or None,
        usage=usages[0] if len(usages) == 1 else None,
        last_message=last_message,
        command_count=len(commands),
        commands=tuple(commands),
        failures=tuple(failures),
        event_count=count,
        stdout_sha256=hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        extra={"turn_completed_events": len(usages)},
    )


ThreadReader = Callable[[str], Mapping[str, object]]


def observe_thread(thread_id: str | None, reader: ThreadReader | None) -> RuntimeObservation:
    """以 ``thread/read`` 回讀值彙總 observed model／effort；任何缺漏維持 unknown。"""

    def unavailable(reason: str, evidence: tuple = ()) -> RuntimeObservation:
        return RuntimeObservation(
            source=THREAD_IDENTITY_SOURCE,
            model_id=None,
            effort=None,
            model_reason=reason,
            effort_reason=reason,
            evidence=evidence,
        )

    if reader is None:
        return unavailable("thread-reader-not-configured")
    if thread_id is None:
        return unavailable("thread-id-not-unique")
    try:
        thread = reader(thread_id)
    except Exception as exc:  # noqa: BLE001 - 身分觀測 fail-soft
        return unavailable(f"thread-read-failed:{type(exc).__name__}")
    values = {
        name: thread.get(name) if isinstance(thread, Mapping) else None
        for name in ("id", "model", "reasoningEffort", "modelProvider")
    }
    if values["id"] != thread_id or not all(
        isinstance(values[name], str) and values[name]
        for name in ("model", "reasoningEffort", "modelProvider")
    ):
        return unavailable("thread-identity-incomplete")
    evidence = (
        {
            "call": 1,
            "state": "observed",
            "thread_sha256": hashlib.sha256(thread_id.encode("utf-8")).hexdigest(),
            "model": values["model"],
            "reasoning_effort": values["reasoningEffort"],
            "model_provider": values["modelProvider"],
        },
    )
    if values["modelProvider"] != EXPECTED_MODEL_PROVIDER:
        return unavailable("unexpected-model-provider", evidence)
    return RuntimeObservation(
        source=THREAD_IDENTITY_SOURCE,
        model_id=str(values["model"]),
        effort=values["reasoningEffort"],
        evidence=evidence,
    )
