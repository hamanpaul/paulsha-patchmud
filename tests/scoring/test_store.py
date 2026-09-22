"""Immutable score archive and Markdown report tests."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from patchmud.scoring.aggregation import aggregate_results
from patchmud.scoring.store import ScoreStore, ScoreStoreError


def _case(case_id: str = "repair-1") -> dict:
    return {
        "id": case_id,
        "category": "repair",
        "title": "Public parser repair",
        "prompt": "Fix the parser.",
        "requirements": ["keep the contract"],
        "allowed_paths": ["src/parser.py"],
        "public_files": {"src/parser.py": "def parse(value): ...\n"},
    }


def _case_result(case_id: str = "repair-1", score: float = 75.0) -> dict:
    return {
        "case_id": case_id,
        "repetition": 1,
        "case": _case(case_id),
        "judgment": {
            "status": "scored",
            "model": "jev-1.13.0",
            "score": score,
            "dimensions": {
                "fulfillment": {"type": "score", "score": 3, "confidence": 0.8, "legend": {"0": "level 0", "1": "level 1", "2": "level 2", "3": "level 3", "4": "level 4"}, "probabilities": {"0": 0, "1": 0, "2": 0, "3": 1, "4": 0}},
                "evidence": {"type": "score", "score": 3, "confidence": 0.8, "legend": {"0": "level 0", "1": "level 1", "2": "level 2", "3": "level 3", "4": "level 4"}, "probabilities": {"0": 0, "1": 0, "2": 0, "3": 1, "4": 0}},
                "constraints": {"type": "score", "score": 3, "confidence": 0.8, "legend": {"0": "level 0", "1": "level 1", "2": "level 2", "3": "level 3", "4": "level 4"}, "probabilities": {"0": 0, "1": 0, "2": 0, "3": 1, "4": 0}},
                "verification": {"type": "score", "score": 3, "confidence": 0.8, "legend": {"0": "level 0", "1": "level 1", "2": "level 2", "3": "level 3", "4": "level 4"}, "probabilities": {"0": 0, "1": 0, "2": 0, "3": 1, "4": 0}},
            },
            "evidence_refs": ["ev-1"],
        },
        "execution": {
            "status": "completed",
            "end_reason": "commit",
            "turns": 2,
            "events": [{"evidence_id": "ev-1", "kind": "test", "status": "passed"}],
            "final_report": "Fixed parser and added a regression test.",
            "final_diff": "diff --git a/src/parser.py b/src/parser.py\n",
            "test_results": [{"id": "public-test", "status": "passed"}],
        },
    }


def _record(
    *,
    run_id: str = "target-1",
    status: str = "complete",
    case_ids=("repair-1",),
    cases=None,
    summary=None,
    fingerprint: str = "suite-fingerprint",
) -> dict:
    cases = cases or [_case_result(case_id) for case_id in case_ids]
    if summary is None:
        summary = aggregate_results(cases, [_case(case_id) for case_id in case_ids])
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-09-21T01:02:03Z",
        "role": "target",
        "profile": {"harness": "codex", "model": "gpt-test", "effort": "max"},
        "fingerprint": fingerprint,
        "suite": {
            "id": "engineering-v1",
            "version": "1",
            "suite_hash": "suite-hash",
            "rubric_version": "rubric-v1",
            "case_ids": list(case_ids),
        },
        "judge_model": "jev-1.13.0",
        "repeat": 1,
        "status": status,
        "cases": cases,
        "summary": summary,
    }


def test_save_run_adds_digest_and_refuses_overwrite_or_tampering(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    path = store.save_run(_record())
    assert path == tmp_path / "runs" / "target-1" / "run.json"

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert len(persisted["content_digest"]) == 64
    original = path.read_bytes()
    with pytest.raises(ScoreStoreError):
        store.save_run(_record())
    assert path.read_bytes() == original

    persisted["summary"]["total"] = 1
    path.write_text(json.dumps(persisted), encoding="utf-8")
    with pytest.raises(ScoreStoreError):
        store.load_run(path)


def test_native_file_change_paths_are_public_event_content(tmp_path: Path) -> None:
    record = _record()
    event = {'type':'item.completed','item':{'type':'file_change',
             'changes':[{'path':'/tmp/public-worktree/src/parser.py','kind':'update'}]}}
    execution = record['cases'][0]['execution']
    execution['events'].append({'kind':'native_tool','output':event['item']})
    execution['native_events'] = [event]
    execution['phase_results'] = [{'native_events':[event]}]
    store = ScoreStore(tmp_path)
    path = store.save_run(record)
    saved = store.load_run(path)
    assert saved['cases'][0]['execution']['native_events'] == [event]
    assert store.render_report().is_file()


def test_baseline_requires_complete_verified_full_coverage(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    store.save_run(_record(run_id="partial", status="partial", fingerprint="fp"))
    store.save_run(_record(run_id="complete", fingerprint="fp"))

    baseline = store.find_baseline("fp")

    assert baseline is not None
    assert baseline["run_id"] == "complete"


def test_baseline_recomputes_summary_and_rejects_forged_total(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    record = _record(run_id="forged", fingerprint="fp")
    record["summary"]["total"] = 100
    store.save_run(record)

    assert store.find_baseline("fp") is None


def test_derived_rejudgment_is_excluded_from_baseline_and_rendered_with_provenance(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    record = _record(run_id="derived", fingerprint="fp")
    record.update({
        "engine_digest": "a" * 64,
        "judge_engine_digest": "b" * 64,
        "provenance": {
            "kind": "derived-rejudgment-v1",
            "source_run_id": "source",
            "source_created_at": "2026-09-20T01:02:03Z",
            "source_content_digest": "c" * 64,
            "source_engine_digest": "a" * 64,
            "judge_engine_digest": "b" * 64,
            "reused_execution": True,
            "baseline_eligible": False,
        },
    })
    store.save_run(record)

    assert store.find_baseline("fp") is None
    report = store.render_report().read_text(encoding="utf-8")
    assert "Derived rejudgment" in report
    assert "source" in report
    assert "Reused execution" in report
    assert "Baseline eligible" in report


def test_store_rejects_absolute_fixture_and_private_case_data(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    record = _record()
    record["cases"][0]["case"]["fixture_dir"] = "/private/fixture"
    with pytest.raises(ScoreStoreError):
        store.save_run(record)

    record = _record(run_id="private-2")
    record["cases"][0]["case"]["anchors"] = {"secret": "PRIVATE"}
    with pytest.raises(ScoreStoreError):
        store.save_run(record)


def test_public_evidence_names_and_numeric_application_data_are_allowed(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    record = _record(run_id="public-evidence")
    record["cases"][0]["case"]["public_files"] = {
        "secret_manager.py": {"anchor_price.json": {"secret": "visible", "cost": 7}},
        "fixtures.py": "public fixture source",
    }

    path = store.save_run(record)

    loaded = store.load_run(path)
    assert loaded["cases"][0]["case"]["public_files"]["secret_manager.py"]["anchor_price.json"]["cost"] == 7

def test_render_report_is_atomic_and_preserves_history(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    store.save_run(_record(run_id="first"))
    second = _record(run_id="second")
    second["comparison"] = {"base_run_id": "first", "reused": True}
    store.save_run(second)

    paths = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: store.render_report(), range(8)))

    assert all(path == tmp_path / "models-score.md" for path in paths)
    report_path = tmp_path / "models-score.md"
    report = report_path.read_text(encoding="utf-8")
    assert "first" in report and "second" in report
    assert "repair-1" in report
    assert "75.0" in report
    assert "PRIVATE" not in report


def test_public_file_text_and_fenced_content_cannot_break_report(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    record = _record(run_id="fenced")
    record["cases"][0]["case"]["public_files"]["README.md"] = "```text\n/path is source text\n```\n"
    record["cases"][0]["execution"]["final_report"] = "```candidate output```"
    store.save_run(record)
    report = store.render_report().read_text(encoding="utf-8")

    assert "candidate output" in report
    assert report.count("```") % 2 == 0
