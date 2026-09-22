"""Lossless public execution views for dimension-specific JEV requests."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .judge import _PUBLIC_EXECUTION_FIELDS

__all__ = ["JUDGE_PROTOCOL_VERSION", "evidence_views"]

JUDGE_PROTOCOL_VERSION = "dimension-evidence-v1"
_DIMENSIONS = ("fulfillment", "evidence", "constraints", "verification")
_AGY_LIFECYCLE_FIELDS = frozenset(
    {"conversation_id", "step_index", "step_type", "tool_name", "duration_seconds"}
)
_AGY_IDENTITY_FIELDS = frozenset(
    {"conversation_id", "step_index", "step_type", "tool_name", "duration_seconds"}
)


def _is_agy_lifecycle_event(event: Mapping[str, Any], output: Any) -> bool:
    """Recognize the known AGY wrapper without filtering provider extensions."""

    return (
        event.get("kind") == "native_tool"
        and isinstance(output, Mapping)
        and "tool_info" in output
        and bool(_AGY_IDENTITY_FIELDS.intersection(output))
    )


def _view_event(event: Any) -> Any:
    copied = copy.deepcopy(event)
    if not isinstance(copied, Mapping):
        return copied
    output = copied.get("output")
    if not _is_agy_lifecycle_event(copied, output):
        return copied
    copied["output"] = {
        str(key): copy.deepcopy(value)
        for key, value in output.items()
        if str(key) not in _AGY_LIFECYCLE_FIELDS
    }
    return copied


def _copy_public_execution(execution: Mapping[str, Any], *, omit: frozenset[str]) -> dict[str, Any]:
    view = {
        field: copy.deepcopy(execution[field])
        for field in _PUBLIC_EXECUTION_FIELDS
        if field in execution and field not in omit
    }
    events = view.get("events")
    if isinstance(events, list):
        view["events"] = [_view_event(event) for event in events]
    return view


def evidence_views(execution: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return independent, lossless public execution views for four JEV dimensions.

    ``evidence`` omits only the final diff.  The other three dimensions omit
    only the transcript.  All remaining public fields and event payloads are
    copied exactly, with the known AGY lifecycle identity duplicated inside a
    ``native_tool`` event's ``output`` removed while its outer evidence and
    complete ``tool_info`` remain available.
    """

    if not isinstance(execution, Mapping):
        raise TypeError("execution must be a mapping")
    return {
        "fulfillment": _copy_public_execution(execution, omit=frozenset({"transcript"})),
        "evidence": _copy_public_execution(execution, omit=frozenset({"final_diff"})),
        "constraints": _copy_public_execution(execution, omit=frozenset({"transcript"})),
        "verification": _copy_public_execution(execution, omit=frozenset({"transcript"})),
    }
