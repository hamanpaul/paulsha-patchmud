"""Offline derived rejudgment tests.

These tests inject a fake judge.  Rejudgment must only consume the immutable
public archive and must never invoke a case executor or native agent.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from patchmud.scoring.aggregation import aggregate_results
from patchmud.scoring.rejudge import rejudge_run, rejudge_runs
from patchmud.scoring.store import ScoreStore, ScoreStoreError


def _case(case_id: str) -> dict:
    criteria = [f"level {index}" for index in range(5)]
    return {
        "id": case_id,
        "category": "repair",
        "title": f"Case {case_id}",
        "prompt": "Fix the public issue.",
        "requirements": ["show evidence"],
        "allowed_paths": ["src/main.py"],
        "rubric": {
            dimension: {"instructions": dimension, "criteria": criteria}
            for dimension in ("fulfillment", "evidence", "constraints", "verification")
        },
        "public_files": {"src/main.py": "print('public')\n"},
    }


def _judgment(score: float = 75.0) -> dict:
    dimensions = {
        dimension: {
            "type": "score",
            "score": 3,
            "confidence": 1.0,
            "legend": {str(index): f"level {index}" for index in range(5)},
            "probabilities": {str(index): float(index == 3) for index in range(5)},
        }
        for dimension in ("fulfillment", "evidence", "constraints", "verification")
    }
    return {
        "status": "scored",
        "model": "jev-1.13.0",
        "score": score,
        "dimensions": dimensions,
        "evidence_refs": ["ev-1"],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }


def _row(case_id: str, *, execution_status: str = "completed") -> dict:
    execution = {
        "case_id": case_id,
        "status": execution_status,
        "end_reason": "commit" if execution_status != "error" else "execution_error",
        "transcript": [{"role": "assistant", "content": "public transcript"}],
        "events": [{"evidence_id": "ev-1", "kind": "test", "status": "passed"}],
        "final_report": "public report",
        "final_diff": "diff --git a/src/main.py b/src/main.py\n",
        "test_results": [{"id": "public-test", "status": "passed"}],
    }
    if execution_status == "error":
        execution["error"] = "candidate process failed"
    return {
        "case_id": case_id,
        "repetition": 1,
        "case": _case(case_id),
        "execution": execution,
        "judgment": _judgment() if execution_status != "error" else {
            "status": "error",
            "model": "jev-1.13.0",
            "score": None,
            "dimensions": {},
            "error": "candidate process failed",
        },
    }


def _record(run_id: str = "source", *, engine_digest: str = "a" * 64) -> dict:
    rows = [_row("repair-1"), _row("repair-error", execution_status="error")]
    suite = {
        "id": "engineering-v1",
        "version": "1",
        "suite_hash": "suite-hash",
        "rubric_version": "rubric-v1",
        "case_ids": ["repair-1", "repair-error"],
    }
    summary = aggregate_results([rows[0]], [rows[0]["case"], rows[1]["case"]])
    return {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": "2026-09-21T01:02:03Z",
        "role": "target",
        "phase": "formal",
        "profile": {"harness": "codex", "model": "gpt-test", "effort": "max"},
        "fingerprint": f"source-{run_id}",
        "suite": suite,
        "judge_model": "jev-1.13.0",
        "repeat": 1,
        "status": "partial",
        "engine_digest": engine_digest,
        "protocol_version": "native-engineering-v1",
        "environment_digest": "environment",
        "tool_cohort": "native-tools",
        "budget": {"repair-1": {"wall_seconds": 600}},
        "cases": rows,
        "summary": summary,
    }


class FakeJudge:
    def __init__(self, calls: list[tuple[dict, dict]], *, interrupt_after: int | None = None) -> None:
        self.calls = calls
        self.interrupt_after = interrupt_after

    def evaluate(self, case: dict, execution: dict) -> dict:
        self.calls.append((copy.deepcopy(case), copy.deepcopy(execution)))
        if self.interrupt_after is not None and len(self.calls) > self.interrupt_after:
            raise KeyboardInterrupt
        return _judgment(75.0)


def test_rejudge_reuses_exact_public_execution_and_skips_execution_errors(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source = _record()
    source_path = store.save_run(source)
    source = store.load_run(source_path)
    source_bytes = source_path.read_bytes()
    calls: list[tuple[dict, dict]] = []
    judge = FakeJudge(calls)

    records = rejudge_runs(
        ["source"],
        output_dir=tmp_path,
        judge_factory=lambda model: judge,
        judge_engine_digest="b" * 64,
    )

    assert len(records) == 1
    derived = records[0]
    assert derived["run_id"] != source["run_id"]
    assert len(derived["content_digest"]) == 64
    assert derived["engine_digest"] == source["engine_digest"]
    assert derived["judge_engine_digest"] == "b" * 64
    assert derived["judge_protocol_version"] == "dimension-evidence-v1"
    assert derived["provenance"]["judge_protocol_version"] == "dimension-evidence-v1"
    assert derived["provenance"]["source_judge_protocol_version"] == "full-state-v1"
    assert derived["provenance"]["kind"] == "derived-rejudgment-v1"
    assert derived["provenance"]["source_run_id"] == "source"
    assert derived["provenance"]["source_content_digest"] == source["content_digest"]
    assert derived["provenance"]["reused_execution"] is True
    assert derived["provenance"]["baseline_eligible"] is False
    assert len(calls) == 1
    assert derived["cases"][0]["case"] == source["cases"][0]["case"]
    assert derived["cases"][0]["execution"] == source["cases"][0]["execution"]
    assert derived["cases"][1]["execution"] == source["cases"][1]["execution"]
    assert derived["cases"][1]["judgment"] == source["cases"][1]["judgment"]
    assert source_path.read_bytes() == source_bytes
    report = (tmp_path / "models-score.md").read_text()
    assert "Judge protocol: `dimension-evidence-v1`" in report
    assert "Source judge protocol: `full-state-v1`" in report


def test_execution_error_only_source_never_constructs_a_judge(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source = _record()
    for row in source["cases"]:
        row["execution"]["status"] = "error"
        row["execution"]["end_reason"] = "execution_error"
        row["execution"]["error"] = "candidate process failed"
        row["judgment"] = {
            "status": "error",
            "model": "jev-1.13.0",
            "score": None,
            "dimensions": {},
            "error": "candidate process failed",
        }
    source_path = store.save_run(source)
    loaded = store.load_run(source_path)

    def forbidden_factory(model: str) -> FakeJudge:
        raise AssertionError("judge must not be constructed for execution errors")

    derived = rejudge_run(
        "source",
        output_dir=tmp_path,
        judge_factory=forbidden_factory,
        judge_engine_digest="b" * 64,
    )

    assert derived["cases"] == loaded["cases"]


def test_rejudge_validates_all_sources_before_first_judge_call(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source_path = store.save_run(_record())
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    raw["cases"][0]["execution"]["final_report"] = "tampered"
    source_path.write_text(json.dumps(raw), encoding="utf-8")
    calls: list[tuple[dict, dict]] = []

    with pytest.raises(ScoreStoreError, match="digest"):
        rejudge_runs(
            ["source"],
            output_dir=tmp_path,
            judge_factory=lambda model: FakeJudge(calls),
            judge_engine_digest="b" * 64,
        )

    assert calls == []
    assert len(list((tmp_path / "runs").glob("*/run.json"))) == 1


def test_rejudge_interrupt_archives_partial_derived_record(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source_path = store.save_run(_record())
    source = store.load_run(source_path)
    calls: list[tuple[dict, dict]] = []

    records = rejudge_runs(
        ["source"],
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge(calls, interrupt_after=0),
        judge_engine_digest="b" * 64,
    )

    assert len(records) == 1
    assert records[0]["status"] == "partial"
    assert records[0]["interrupted"] is True
    assert records[0]["end_reason"] == "interrupted"
    assert records[0]["provenance"]["reused_execution"] is True
    assert "interrupted" in records[0]["error"]
    assert len(records[0]["cases"]) == len(source["cases"])
    assert records[0]["cases"][0]["execution"] == source["cases"][0]["execution"]
    assert records[0]["cases"][1]["execution"] == source["cases"][1]["execution"]
    assert records[0]["cases"][0]["judgment"]["status"] == "error"
    assert records[0]["cases"][1]["judgment"] == source["cases"][1]["judgment"]
    assert len(list((tmp_path / "runs").glob("*/run.json"))) == 2


def test_factory_failure_preserves_all_rows_and_marks_only_unjudged_rows(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source = _record("factory-failure")
    source_path = store.save_run(source)
    source = store.load_run(source_path)

    def failing_factory(model: str) -> FakeJudge:
        raise RuntimeError("judge unavailable")

    derived = rejudge_run(
        "factory-failure",
        output_dir=tmp_path,
        judge_factory=failing_factory,
        judge_engine_digest="b" * 64,
    )

    assert len(derived["cases"]) == len(source["cases"])
    assert [row["case"] for row in derived["cases"]] == [row["case"] for row in source["cases"]]
    assert [row["execution"] for row in derived["cases"]] == [row["execution"] for row in source["cases"]]
    assert [row["repetition"] for row in derived["cases"]] == [row["repetition"] for row in source["cases"]]
    assert derived["cases"][0]["judgment"]["status"] == "error"
    assert derived["cases"][0]["judgment"]["score"] is None
    assert derived["cases"][1]["judgment"] == source["cases"][1]["judgment"]
    assert derived["status"] == "partial"


def test_middle_interrupt_preserves_later_rows_as_unjudged(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source = _record("middle-interrupt")
    third = _row("repair-third")
    source["cases"].append(third)
    source["suite"]["case_ids"].append("repair-third")
    source["summary"] = aggregate_results(
        source["cases"], [row["case"] for row in source["cases"]]
    )
    source["status"] = "partial"
    source_path = store.save_run(source)
    source = store.load_run(source_path)
    calls: list[tuple[dict, dict]] = []

    derived = rejudge_run(
        "middle-interrupt",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge(calls, interrupt_after=1),
        judge_engine_digest="b" * 64,
    )

    assert len(derived["cases"]) == len(source["cases"])
    assert [row["case"] for row in derived["cases"]] == [row["case"] for row in source["cases"]]
    assert [row["execution"] for row in derived["cases"]] == [row["execution"] for row in source["cases"]]
    assert derived["cases"][0]["judgment"]["status"] == "scored"
    assert derived["cases"][1]["judgment"]["status"] == "error"
    assert derived["cases"][2]["judgment"]["status"] == "error"
    assert derived["cases"][2]["judgment"]["score"] is None


def test_paired_derived_runs_compare_only_after_source_engine_and_judge_match(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    for run_id, role in (("base", "base"), ("target", "target")):
        source = _record(run_id, engine_digest="a" * 64)
        source["role"] = role
        source["cases"] = [source["cases"][0]]
        source["suite"]["case_ids"] = ["repair-1"]
        source["summary"] = aggregate_results(source["cases"], [source["cases"][0]["case"]])
        source["status"] = "complete"
        store.save_run(source)
    calls: list[tuple[dict, dict]] = []
    records = rejudge_runs(
        ["base", "target"],
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge(calls),
        judge_engine_digest="b" * 64,
    )

    target = next(record for record in records if record["role"] == "target")
    assert target["comparison"]["compatible"] is True
    persisted = store.load_run(tmp_path / "runs" / target["run_id"] / "run.json")
    assert persisted["comparison"]["base_run_id"] == next(
        record["run_id"] for record in records if record["role"] == "base"
    )


def test_single_target_rejudge_uses_existing_derived_base(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    base = _record("base", engine_digest="a" * 64)
    base["role"] = "base"
    base["cases"] = [base["cases"][0]]
    base["suite"]["case_ids"] = ["repair-1"]
    base["summary"] = aggregate_results(base["cases"], [base["cases"][0]["case"]])
    base["status"] = "complete"
    target = copy.deepcopy(base)
    target["run_id"] = "target"
    target["role"] = "target"
    target["comparison"] = {"base_run_id": "base"}
    store.save_run(base)
    store.save_run(target)
    digest = "b" * 64

    derived_base = rejudge_run(
        "base",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge([]),
        judge_engine_digest=digest,
    )
    derived_target = rejudge_run(
        "target",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge([]),
        judge_engine_digest=digest,
    )

    assert derived_base["role"] == "base"
    assert derived_target["comparison"]["base_run_id"] == derived_base["run_id"]


def test_single_target_rejudge_does_not_switch_to_an_unrequested_updated_base(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    sources: list[dict] = []
    for run_id in ("base-original", "base-updated"):
        base = _record(run_id, engine_digest="a" * 64)
        base["role"] = "base"
        base["cases"] = [base["cases"][0]]
        base["suite"]["case_ids"] = ["repair-1"]
        base["summary"] = aggregate_results(base["cases"], [base["cases"][0]["case"]])
        base["status"] = "complete"
        base["profile"]["model"] = run_id
        sources.append(base)
        store.save_run(base)
    target = copy.deepcopy(sources[0])
    target["run_id"] = "target-original"
    target["role"] = "target"
    target["comparison"] = {"base_run_id": "base-original"}
    target["profile"]["model"] = "target-model"
    store.save_run(target)

    digest = "b" * 64
    original_derived = rejudge_run(
        "base-original",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge([]),
        judge_engine_digest=digest,
    )
    updated_derived = rejudge_run(
        "base-updated",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge([]),
        judge_engine_digest=digest,
    )
    derived_target = rejudge_run(
        "target-original",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge([]),
        judge_engine_digest=digest,
    )

    assert derived_target["comparison"]["base_run_id"] == original_derived["run_id"]
    assert derived_target["comparison"]["base_run_id"] != updated_derived["run_id"]


def test_nonformal_source_cannot_publish_a_full_rejudgment_summary(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source = _record("pilot-source")
    source["cases"] = [source["cases"][0]]
    source["suite"]["case_ids"] = ["repair-1"]
    source["summary"] = aggregate_results(source["cases"], [source["cases"][0]["case"]])
    source["status"] = "complete"
    source["phase"] = "pilot"
    store.save_run(source)

    derived = rejudge_run(
        "pilot-source",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge([]),
        judge_engine_digest="b" * 64,
    )

    assert derived["status"] == "partial"
    assert derived["summary"]["total"] is None
    assert derived["summary"]["complete"] is False


def test_rejudgment_drops_inherited_interruption_metadata(tmp_path: Path) -> None:
    store = ScoreStore(tmp_path)
    source = _record("interrupted-source")
    source["interrupted"] = True
    source["end_reason"] = "interrupted"
    store.save_run(source)

    derived = rejudge_run(
        "interrupted-source",
        output_dir=tmp_path,
        judge_factory=lambda model: FakeJudge([]),
        judge_engine_digest="b" * 64,
    )

    assert "interrupted" not in derived
    assert "end_reason" not in derived
