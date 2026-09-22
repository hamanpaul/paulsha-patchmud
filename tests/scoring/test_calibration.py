"""Tests for the private engineering-v1 anchor calibration harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from patchmud.scoring.calibration import (
    ANCHOR_NAMES,
    DIMENSIONS,
    CalibrationError,
    calibrate_anchors,
    main,
)


def _case(case_id: str = "repair-anchor") -> dict:
    rubric = {
        name: {
            "instructions": f"Score {name}.",
            "criteria": [f"{name} {level}" for level in range(5)],
        }
        for name in DIMENSIONS
    }
    anchors = {
        "kind": "patch",
        "outcome_class": "code_change",
        "reference": {
            "final_report": "Reference report with tests and evidence.",
            "final_diff": "diff --git a/src/main.py b/src/main.py\n",
            "expected_quality": {name: 4 for name in DIMENSIONS},
        },
        "partial": {
            "final_report": "Partial report with incomplete evidence.",
            "final_diff": "diff --git a/src/main.py b/src/main.py\n",
            "expected_quality": {
                "fulfillment": 2,
                "evidence": 2,
                "constraints": 2,
                "verification": 1,
            },
        },
        "wrong": {
            "final_report": "Wrong report claims completion without proof.",
            "final_diff": "diff --git a/src/main.py b/src/main.py\n",
            "expected_quality": {name: 0 for name in DIMENSIONS},
        },
    }
    return {
        "id": case_id,
        "category": "repair",
        "depth": 1,
        "title": "Anchor case",
        "prompt": "Repair the fixture and report evidence.",
        "requirements": ["Preserve behavior", "Run tests"],
        "allowed_paths": ["src/main.py"],
        "max_turns": 8,
        "wall_seconds": 600,
        "fixture_dir": "/private/fixture/repo-and-hidden",
        "public_files": {"src/main.py": "def value():\n    return 1\n"},
        "rubric": rubric,
        "stages": [{"after_turn": 2, "message": "Inspect the boundary."}],
        "test_argv": ["python3", "-m", "pytest", "-q"],
        "case_hash": f"hash-{case_id}",
        "anchors": anchors,
    }


def _suite(*cases: dict) -> dict:
    selected = list(cases) or [_case()]
    return {
        "id": "engineering-v1",
        "version": "1.0.0",
        "suite_hash": "suite-hash",
        "rubric_version": "jev-1.13.0",
        "cases": selected,
    }


def _execution(case: dict, adapter, **kwargs) -> dict:
    """A controlled-runner-shaped fake; it records only delivered messages."""

    replies = list(adapter.replies)
    report = next(message.split("\nREPORT:\n", 1)[1] for message in replies if "\nREPORT:\n" in message)
    return {
        "case_id": case["id"],
        "status": "completed",
        "end_reason": "commit",
        "turns": len(replies),
        "wall_ms": 1,
        "transcript": [
            {"role": "assistant", "content": message} for message in replies
        ]
        + [{"role": "user", "content": "Inspect the boundary."}],
        "events": [
            {"evidence_id": "ev-inspect", "action": "INSPECT", "kind": "inspect"},
            {"evidence_id": "ev-test", "action": "RUN_TEST", "kind": "test", "status": "passed"},
        ],
        "final_report": report,
        "final_diff": "diff --git a/src/main.py b/src/main.py\n",
        "test_results": [{"status": "passed"}],
        "usage": [],
    }


class _FakeAdapter:
    def __init__(self, replies):
        self.replies = list(replies)


class _FakeJudge:
    def __init__(self, model: str):
        self.model = model
        self.calls = []

    def evaluate(self, case, execution):
        self.calls.append((case, execution))
        dimensions = {
            name: {
                "type": "score",
                "score": {"reference": 4, "partial": 2, "wrong": 0}[execution["final_report"].split()[0].lower()],
                "confidence": 0.9,
            }
            for name in DIMENSIONS
        }
        score = sum(item["score"] for item in dimensions.values()) / 4 * 25
        return {
            "status": "scored",
            "model": self.model,
            "dimensions": dimensions,
            "score": score,
            "evidence_refs": ["ev-inspect", "ev-test"],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "wall_ms": 1,
            "error": None,
        }


def test_offline_calibration_executes_all_anchors_and_keeps_live_status_unrun(tmp_path: Path):
    output = tmp_path / "private" / "anchors.json"
    calls = []

    def executor(case, adapter, **kwargs):
        calls.append((case, adapter, kwargs))
        return _execution(case, adapter, **kwargs)

    result = calibrate_anchors(
        output=output,
        offline=True,
        suite_loader=lambda name: _suite(_case()),
        executor=executor,
        adapter_factory=_FakeAdapter,
    )

    assert [next(message for message in entry[1].replies if "\nREPORT:\n" in message).split()[3].lower()
            for entry in calls] == list(ANCHOR_NAMES)
    assert result["mode"] == "offline"
    assert result["live_judge_status"] == "not_run"
    assert result["summary"]["calibration_status"] == "offline_unjudged"
    assert result["judge_protocol_version"] is None
    assert result["summary"]["live_anchor_order_passed"] is None
    assert result["summary"]["expected_anchor_order_passed"] is True
    assert result["cases"][0]["anchors"]["wrong"]["prompt_injection_control"]["semantic_status"] == "not_run"
    assert result["cases"][0]["anchors"]["wrong"]["prompt_injection_control"]["semantic_pass"] is None
    assert output.stat().st_mode & 0o777 == 0o600
    assert output.parent.stat().st_mode & 0o777 == 0o700
    stored = json.loads(output.read_text(encoding="utf-8"))
    assert stored["cases"][0]["anchors"]["reference"]["expected_quality"]["fulfillment"] == 4
    assert "/private/fixture" not in repr(stored["cases"][0].get("public_case", {}))


def test_live_calibration_uses_sanitized_case_and_preserves_dimension_info(tmp_path: Path):
    output = tmp_path / "anchors.json"
    judge = _FakeJudge("jev-1.13.0")

    result = calibrate_anchors(
        output=output,
        suite_loader=lambda name: _suite(_case()),
        executor=_execution,
        adapter_factory=_FakeAdapter,
        judge=judge,
    )

    assert result["live_judge_status"] == "complete"
    assert result["summary"]["live_anchor_order_passed"] is True
    row = result["cases"][0]
    assert row["anchors"]["reference"]["dimensions"]["expected"]["verification"] == 4
    assert row["anchors"]["reference"]["dimensions"]["observed"]["verification"]["score"] == 4
    assert all("anchors" not in case for case, _execution in judge.calls)
    assert all("fixture_dir" not in case for case, _execution in judge.calls)
    injection = row["anchors"]["wrong"]["prompt_injection_control"]
    assert injection["semantic_status"] == "scored"
    assert injection["objective_evidence_preserved"] is True
    assert injection["remains_below_reference"] is True
    assert len(judge.calls) == 4  # three anchors plus the wrong-report probe


def test_live_calibration_uses_native_public_case_but_records_scripted_replay(tmp_path: Path):
    case = _case("repair-native-contract")
    case["max_turns"] = 24
    case["stages"] = [
        {"after_turn": 8, "message": "Authoritative phase one update."},
        {"after_turn": 16, "message": "Authoritative phase two update."},
    ]
    judge = _FakeJudge("jev-1.13.0")

    result = calibrate_anchors(
        output=tmp_path / "native-contract.json",
        suite_loader=lambda name: _suite(case),
        executor=_execution,
        adapter_factory=_FakeAdapter,
        judge=judge,
    )

    public_case = judge.calls[0][0]
    assert public_case["max_turns"] is None
    assert public_case["execution_policy"]["protocol"] == "native-engineering-v1"
    assert public_case["execution_policy"]["phases"] == (
        "sequential-native-conversation-equal-wall-shares-v1"
    )
    assert public_case["stages"] == [
        {"phase": 2, "message": "Authoritative phase one update."},
        {"phase": 3, "message": "Authoritative phase two update."},
    ]
    assert result["replay_mode"] == "scripted-anchor-replay"
    assert result["judge_protocol_version"] == "dimension-evidence-v1"
    assert result["execution_mode"] == "scripted-controlled-runner"
    assert result["native_cli"] is False


def test_case_selection_is_bounded_and_unknown_case_fails_closed(tmp_path: Path):
    cases = [_case("repair-a"), _case("repair-b")]
    result = calibrate_anchors(
        output=tmp_path / "selected.json",
        offline=True,
        case_ids=["repair-b"],
        suite_loader=lambda name: _suite(*cases),
        executor=_execution,
        adapter_factory=_FakeAdapter,
    )
    assert result["selected_case_ids"] == ["repair-b"]
    assert result["summary"]["expected_cases"] == 2
    assert result["summary"]["selected_cases"] == 1

    with pytest.raises(CalibrationError, match="unknown case"):
        calibrate_anchors(
            output=tmp_path / "unknown.json",
            offline=True,
            case_ids=["missing"],
            suite_loader=lambda name: _suite(*cases),
            executor=_execution,
            adapter_factory=_FakeAdapter,
        )


def test_failure_is_recorded_per_anchor_and_live_error_does_not_become_zero(tmp_path: Path):
    def failing(case, adapter, **kwargs):
        return {
            "case_id": case["id"],
            "status": "error",
            "end_reason": "provider_error",
            "error": "provider unavailable",
            "turns": 0,
            "wall_ms": 0,
            "transcript": [],
            "events": [],
            "final_report": None,
            "final_diff": None,
            "test_results": [],
            "usage": [],
        }

    result = calibrate_anchors(
        output=tmp_path / "failed.json",
        suite_loader=lambda name: _suite(_case()),
        executor=failing,
        adapter_factory=_FakeAdapter,
        judge=_FakeJudge("jev-1.13.0"),
    )
    assert result["live_judge_status"] == "error"
    assert result["summary"]["live_anchor_order_passed"] is None
    for anchor in result["cases"][0]["anchors"].values():
        assert anchor["judgment"]["score"] is None
        assert anchor["failures"]


def test_live_without_credential_fails_before_any_anchor_execution(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    calls = []

    def executor(case, adapter, **kwargs):
        calls.append(case["id"])
        return _execution(case, adapter, **kwargs)

    with pytest.raises(CalibrationError, match="TYPESAFE_API_KEY"):
        calibrate_anchors(
            output=tmp_path / "missing-key.json",
            suite_loader=lambda name: _suite(_case()),
            executor=executor,
            adapter_factory=_FakeAdapter,
        )
    assert calls == []


def test_runner_error_stops_remaining_anchors_and_cases_but_archives_partial(tmp_path: Path):
    calls = []

    def executor(case, adapter, **kwargs):
        calls.append(case["id"])
        return {
            "case_id": case["id"],
            "status": "error",
            "end_reason": "interrupted",
            "error": "interrupted",
            "turns": 1,
            "wall_ms": 1,
            "transcript": [],
            "events": [],
            "final_report": None,
            "final_diff": None,
            "test_results": [],
            "usage": [],
        }

    result = calibrate_anchors(
        output=tmp_path / "partial.json",
        offline=True,
        suite_loader=lambda name: _suite(_case("repair-a"), _case("repair-b")),
        executor=executor,
        adapter_factory=_FakeAdapter,
    )
    assert calls == ["repair-a"]
    assert result["cases"][0]["aborted"] is True
    assert set(result["cases"][0]["anchors"]) == set(ANCHOR_NAMES)
    assert result["cases"][0]["anchors"]["partial"]["execution"] is None
    assert result["selected_case_ids"] == ["repair-a", "repair-b"]
    assert result["coverage"] == {"expected": 2, "selected": 2}
    assert result["summary"]["case_failures"]


def test_cli_offline_writes_private_result_without_invoking_live_judge(tmp_path: Path, monkeypatch):
    output = tmp_path / "cli.json"
    monkeypatch.setattr(
        "patchmud.scoring.calibration.calibrate_anchors",
        lambda **kwargs: {
            "live_judge_status": "not_run",
            "summary": {
                "calibration_status": "offline_unjudged",
                "expected_anchor_order_passed": True,
                "case_failures": [],
            },
        },
    )
    assert main(["--output", str(output), "--offline"]) == 0


def test_public_archive_paths_are_rejected(tmp_path: Path):
    with pytest.raises(CalibrationError, match="private calibration output"):
        calibrate_anchors(
            output=tmp_path / "runs" / "run.json",
            offline=True,
            suite_loader=lambda name: _suite(_case()),
            executor=_execution,
            adapter_factory=_FakeAdapter,
        )
