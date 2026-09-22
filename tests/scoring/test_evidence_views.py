"""Lossless, dimension-specific public evidence views."""

from __future__ import annotations

import copy

from patchmud.scoring.evidence_views import JUDGE_PROTOCOL_VERSION, evidence_views
from patchmud.scoring.judge import _PUBLIC_EXECUTION_FIELDS


def _agy_event() -> dict:
    return {
        "evidence_id": "agy-1",
        "kind": "native_tool",
        "action": "run_command",
        "status": "done",
        "phase": 2,
        "step_index": 7,
        "output": {
            "conversation_id": "conversation-secret-id",
            "step_index": 7,
            "step_type": "tool",
            "tool_name": "run_command",
            "duration_seconds": 1.25,
            "tool_info": {
                "name": "run_command",
                "parameters": {"CommandLine": "pytest -q"},
                "output": "1 failed\n",
            },
            "error": {"kind": "none"},
            "state": {"attempt": 1},
            "provider_unknown": {"keep": True},
        },
    }


def _execution() -> dict:
    codex_event = {
        "evidence_id": "codex-1",
        "kind": "native_tool",
        "action": "command_execution",
        "status": "completed",
        "output": {
            "type": "command_execution",
            "tool_info": {"output": "codex output\n"},
            "provider_unknown": "preserve",
        },
    }
    unknown_event = {
        "evidence_id": "unknown-1",
        "kind": "provider_extension",
        "phase": "scope",
        "action": "INSPECT_CHANGES",
        "status": "failed",
        "output": {"tool_info": {"output": "outside allowed paths"}, "state": "keep"},
    }
    return {
        "case_id": "testing-evidence",
        "status": "completed",
        "end_reason": "native_completed",
        "error": None,
        "turns": 3,
        "wall_ms": 1234,
        "transcript": [
            {"role": "assistant", "content": "REPORT: preserved failure evidence"}
        ],
        "events": [_agy_event(), codex_event, unknown_event],
        "final_report": "The final report is public evidence.",
        "final_diff": "diff --git a/src/main.py b/src/main.py\n+",
        "test_results": [
            {"id": "independent-failure", "status": "failed", "stdout": "assertion output"}
        ],
        # These are deliberately outside the judge public-field contract.
        "native_events": [{"conversation_id": "raw-provider-id", "raw": True}],
        "phase_results": [{"phase": 1, "events": [{"status": "passed"}]}],
        "usage": [{"provider": "agy", "raw": {"input_tokens": 10}}],
    }


def test_views_use_public_fields_and_keep_source_unchanged() -> None:
    execution = _execution()
    original = copy.deepcopy(execution)

    views = evidence_views(execution)

    assert JUDGE_PROTOCOL_VERSION == "dimension-evidence-v1"
    assert set(views) == {"fulfillment", "evidence", "constraints", "verification"}
    public_fields = set(_PUBLIC_EXECUTION_FIELDS)
    assert set(views["evidence"]) == public_fields - {"final_diff"}
    for dimension in ("fulfillment", "constraints", "verification"):
        assert set(views[dimension]) == public_fields - {"transcript"}
    assert execution == original
    assert "native_events" not in views["evidence"]
    assert "phase_results" not in views["fulfillment"]
    assert "usage" not in views["verification"]


def test_all_views_keep_full_artifacts_events_scope_and_independent_failures() -> None:
    views = evidence_views(_execution())

    assert "final_diff" not in views["evidence"]
    for dimension in ("fulfillment", "constraints", "verification"):
        assert views[dimension]["final_diff"].startswith("diff --git")
        assert views[dimension]["test_results"][0]["status"] == "failed"
        assert len(views[dimension]["events"]) == 3
        assert views[dimension]["events"][2]["phase"] == "scope"
        assert views[dimension]["events"][2]["output"]["state"] == "keep"
    assert views["evidence"]["transcript"]
    assert views["evidence"]["final_report"].startswith("The final report")
    for dimension in ("fulfillment", "constraints", "verification"):
        assert "transcript" not in views[dimension]


def test_agy_lifecycle_identity_is_removed_only_inside_output() -> None:
    execution = _execution()
    original_agy = copy.deepcopy(execution["events"][0])

    views = evidence_views(execution)

    for view in views.values():
        event = view["events"][0]
        assert event["evidence_id"] == original_agy["evidence_id"]
        assert event["step_index"] == original_agy["step_index"]
        assert event["phase"] == original_agy["phase"]
        output = event["output"]
        assert set(output) == {"tool_info", "error", "state", "provider_unknown"}
        assert output["tool_info"] == original_agy["output"]["tool_info"]
        assert output["error"] == original_agy["output"]["error"]
        assert output["state"] == original_agy["output"]["state"]
        assert output["provider_unknown"] == original_agy["output"]["provider_unknown"]


def test_codex_and_unknown_events_are_not_filtered() -> None:
    execution = _execution()
    original_codex = copy.deepcopy(execution["events"][1])
    original_unknown = copy.deepcopy(execution["events"][2])

    views = evidence_views(execution)

    for view in views.values():
        assert view["events"][1] == original_codex
        assert view["events"][2] == original_unknown


def test_each_dimension_is_an_independent_deep_copy() -> None:
    views = evidence_views(_execution())

    views["evidence"]["events"][0]["output"]["tool_info"]["output"] = "changed"
    views["fulfillment"]["test_results"][0]["status"] = "changed"

    assert views["constraints"]["events"][0]["output"]["tool_info"]["output"] == "1 failed\n"
    assert views["verification"]["test_results"][0]["status"] == "failed"
