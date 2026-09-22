"""Rejudge immutable score archives without rerunning candidate execution.

The rejudge path is deliberately separate from the scoring runner.  It reads
only digest-verified public archives, deep-copies their case and execution
evidence, and calls the injected JEV judge.  No suite is rebuilt and no
candidate executor or native adapter is reachable from this module.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .aggregation import aggregate_results, compare_results
from .cli import engine_content_digest
from .evidence_views import JUDGE_PROTOCOL_VERSION
from .judge import JEV_MODEL, JevJudge
from .store import ScoreStore, ScoreStoreError

__all__ = [
    "RejudgeError",
    "parse_args",
    "rejudge_run",
    "rejudge_runs",
    "main",
]

_DERIVED_KIND = "derived-rejudgment-v1"


class RejudgeError(ValueError):
    """Raised when an immutable source cannot be used for rejudgment."""


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    secret = os.environ.get("TYPESAFE_API_KEY")
    if secret:
        message = message.replace(secret, "[redacted]")
    return f"{type(exc).__name__}: {message[:500]}"


def _utc_now(now: Callable[[], datetime] | None = None) -> datetime:
    value = now() if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_path(store: ScoreStore, run_id: str) -> Path:
    if (
        not isinstance(run_id, str)
        or not run_id
        or run_id in {".", ".."}
        or Path(run_id).name != run_id
        or "/" in run_id
        or "\\" in run_id
        or ".." in run_id
    ):
        raise RejudgeError("source run id must be one archive directory name")
    return store.runs_root / run_id / "run.json"


def _expected_cases(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    suite = source.get("suite")
    case_ids = suite.get("case_ids", []) if isinstance(suite, Mapping) else []
    by_id: dict[str, dict[str, Any]] = {}
    for row in source.get("cases", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("case"), Mapping):
            continue
        case = row["case"]
        case_id = case.get("id")
        if isinstance(case_id, str) and case_id not in by_id:
            by_id[case_id] = copy.deepcopy(dict(case))
    result: list[dict[str, Any]] = []
    for case_id in case_ids:
        if case_id in by_id:
            result.append(by_id[case_id])
        else:
            # A partial source may have no row for a suite slot.  The missing
            # placeholder lets aggregation report the slot without loading or
            # rebuilding the private case fixture.
            result.append({"id": case_id, "category": "unknown"})
    return result


def _error_judgment(message: str) -> dict[str, Any]:
    return {
        "status": "error",
        "score": None,
        "dimensions": {},
        "model": JEV_MODEL,
        "error": message,
        "usage": None,
    }


def _derived_fingerprint(
    *,
    run_id: str,
    source: Mapping[str, Any],
    judge_engine_digest: str,
) -> str:
    """Create a unique fingerprint for a report-only derived archive."""

    payload = {
        'source_run_id': source.get('run_id'),
        'source_content_digest': source.get('content_digest'),
        'source_engine_digest': source.get('engine_digest'),
        'judge_engine_digest': judge_engine_digest,
        'judge_protocol_version': JUDGE_PROTOCOL_VERSION,
        'derived_run_id': run_id,
    }
    return f"{_DERIVED_KIND}:{_canonical_digest(payload)}"


def _new_run_id(source: Mapping[str, Any], created_at: str, store: ScoreStore) -> str:
    source_id = str(source.get("run_id", "source"))
    # The source id is useful to an operator, while the UUID keeps immutable
    # archive names collision-free when a source is rejudged repeatedly.
    stem = f"{created_at.replace(':', '').replace('-', '')[:15]}-derived-{source_id}"
    while True:
        candidate = f"{stem}-{uuid.uuid4().hex[:12]}"
        if not (store.runs_root / candidate).exists():
            return candidate


def _judge_instance(factory: Callable[..., Any] | None) -> Any:
    if factory is None:
        return JevJudge(model=JEV_MODEL)
    return factory(model=JEV_MODEL)


def _is_execution_error(row: Mapping[str, Any]) -> bool:
    execution = row.get("execution")
    return isinstance(execution, Mapping) and execution.get("status") == "error"


def _mark_unjudged(row: dict[str, Any], message: str) -> None:
    """Mark a non-execution-error row as unjudged without changing evidence."""

    if not _is_execution_error(row):
        row["judgment"] = _error_judgment(message)


def _source_engine(record: Mapping[str, Any]) -> Any:
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping) and provenance.get("kind") == _DERIVED_KIND:
        return provenance.get("source_engine_digest")
    return record.get("engine_digest")


def _make_record(
    source: Mapping[str, Any],
    *,
    run_id: str,
    created_at: str,
    judge_engine_digest: str,
) -> dict[str, Any]:
    record = copy.deepcopy(dict(source))
    # The source's immutable digest belongs to the source archive.  ScoreStore
    # computes a fresh digest after validating this new record.
    record.pop("content_digest", None)
    record.pop("content_sha256", None)
    record.pop("comparison", None)
    record.pop("error", None)
    record.pop("partial_artifacts", None)
    record.pop("interrupted", None)
    record.pop("end_reason", None)
    record.update(
        {
            "run_id": run_id,
            "created_at": created_at,
            "fingerprint": _derived_fingerprint(
                run_id=run_id,
                source=source,
                judge_engine_digest=judge_engine_digest,
            ),
            # This remains the execution engine recorded by the source.  The
            # judge engine is separate metadata; a derived run did not execute
            # the candidate again.
            "engine_digest": source.get("engine_digest"),
            "judge_engine_digest": judge_engine_digest,
            "judge_protocol_version": JUDGE_PROTOCOL_VERSION,
            "status": "partial",
            "cases": [],
            "summary": {"total": None, "complete": False},
            "provenance": {
                "kind": _DERIVED_KIND,
                "source_run_id": source.get("run_id"),
                "source_created_at": source.get("created_at"),
                "source_content_digest": source.get("content_digest"),
                "source_engine_digest": source.get("engine_digest"),
                "judge_engine_digest": judge_engine_digest,
                "judge_protocol_version": JUDGE_PROTOCOL_VERSION,
                "source_judge_protocol_version": source.get("judge_protocol_version", "full-state-v1"),
                "reused_execution": True,
                "baseline_eligible": False,
            },
        }
    )
    return record


def _rejudge_source(
    source: Mapping[str, Any],
    *,
    store: ScoreStore,
    judge_factory: Callable[..., Any] | None,
    judge_engine_digest: str,
    now: Callable[[], datetime] | None,
) -> dict[str, Any]:
    created_at = _utc_now(now).isoformat().replace("+00:00", "Z")
    run_id = _new_run_id(source, created_at, store)
    record = _make_record(
        source,
        run_id=run_id,
        created_at=created_at,
        judge_engine_digest=judge_engine_digest,
    )
    rows = source.get("cases", [])
    if not isinstance(rows, list):
        raise RejudgeError("source cases must be a list")

    errors: list[str] = []
    judge: Any = None
    # Keep every source row in the derived snapshot from the beginning.  Only
    # the judgment field may change; this preserves case, execution, and
    # repetition evidence even when judging stops halfway through the source.
    record["cases"] = copy.deepcopy(rows)
    needs_judge = any(
        isinstance(source_row, Mapping) and not _is_execution_error(source_row)
        for source_row in rows
    )
    if not needs_judge:
        # A source containing only execution failures is copied as evidence;
        # even constructing a provider judge is unnecessary in this case.
        pass
    else:
        try:
            judge = _judge_instance(judge_factory)
        except KeyboardInterrupt:
            record["interrupted"] = True
            record["end_reason"] = "interrupted"
            message = "rejudgment interrupted before judge construction"
            errors.append(message)
            for row in record["cases"]:
                if isinstance(row, dict):
                    _mark_unjudged(row, message)
        except Exception as exc:
            message = _safe_error(exc)
            errors.append(message)
            for row in record["cases"]:
                if isinstance(row, dict):
                    _mark_unjudged(row, f"judge construction failed: {message}")

    if judge is not None:
        for row_index, row in enumerate(record["cases"]):
            if not isinstance(row, dict):
                errors.append(f"source case row {row_index} is not an object")
                continue
            if _is_execution_error(row):
                # Preserve the source error judgment byte-for-byte and do not
                # spend a judge call on an execution that produced no score.
                continue
            case = row.get("case")
            execution = row.get("execution")
            if not isinstance(case, Mapping) or not isinstance(execution, Mapping):
                message = "source row has no public case/execution snapshot"
                _mark_unjudged(row, message)
                errors.append(message)
                continue
            try:
                judgment = judge.evaluate(copy.deepcopy(case), copy.deepcopy(execution))
                if not isinstance(judgment, Mapping):
                    raise RejudgeError("judge returned a non-object judgment")
                row["judgment"] = copy.deepcopy(dict(judgment))
            except KeyboardInterrupt:
                record["interrupted"] = True
                record["end_reason"] = "interrupted"
                message = "rejudgment interrupted before this and remaining judge calls"
                _mark_unjudged(row, message)
                for remaining in record["cases"][row_index + 1 :]:
                    if isinstance(remaining, dict):
                        _mark_unjudged(remaining, message)
                errors.append(message)
                break
            except Exception as exc:
                message = _safe_error(exc)
                _mark_unjudged(row, message)
                errors.append(message)

    record["summary"] = aggregate_results(
        record["cases"],
        _expected_cases(source),
        repeat=source.get("repeat", 1),
    )
    if source.get("phase", "formal") != "formal":
        record["summary"]["total"] = None
        record["summary"]["complete"] = False
    if errors:
        record["error"] = "; ".join(errors)
    record["evaluation_complete"] = bool(record["summary"].get("complete")) and not errors
    if record["summary"].get("complete") and not errors and source.get("phase", "formal") == "formal":
        record["status"] = "complete"
    elif not record["cases"] and errors:
        record["status"] = "error"
    else:
        record["status"] = "partial"
    return record


def _attach_derived_comparison(records: list[dict[str, Any]]) -> None:
    if len(records) != 2:
        return
    base = next((record for record in records if record.get("role") == "base"), None)
    target = next((record for record in records if record.get("role") == "target"), None)
    if base is None or target is None:
        return
    base_provenance = base.get("provenance")
    target_provenance = target.get("provenance")
    if not isinstance(base_provenance, Mapping) or not isinstance(target_provenance, Mapping):
        return
    if base_provenance.get("kind") != _DERIVED_KIND or target_provenance.get("kind") != _DERIVED_KIND:
        return
    if not isinstance(_source_engine(base), str) or _source_engine(base) != _source_engine(target):
        return
    if base.get("judge_engine_digest") != target.get("judge_engine_digest"):
        return
    comparison = compare_results(target, base)
    if comparison.get("compatible") is True:
        comparison.update(
            base_run_id=base["run_id"],
            base_created_at=base["created_at"],
            reused=False,
        )
        target["comparison"] = comparison


def _attach_existing_derived_base(
    target: dict[str, Any],
    *,
    source: Mapping[str, Any],
    store: ScoreStore,
    judge_engine_digest: str,
) -> None:
    """Attach a prior derived base when target rejudgment runs separately.

    This supports the operational sequence ``--run BASE`` followed later by
    ``--run TARGET`` without asking the operator to rerun the base judgment.
    Only the source target's explicitly recorded base run may be reconnected.
    The candidate must be an immutable derived base from the same source
    execution engine and current judge engine; ordinary runs are never
    compared here.
    """

    if target.get("role") != "target" or "comparison" in target:
        return
    source_comparison = source.get("comparison")
    if not isinstance(source_comparison, Mapping):
        return
    requested_base_id = source_comparison.get("base_run_id")
    if not isinstance(requested_base_id, str) or not requested_base_id:
        return
    candidates: list[dict[str, Any]] = []
    for path in sorted(store.runs_root.glob("*/run.json")):
        try:
            candidate = store.load_run(path)
        except ScoreStoreError:
            continue
        provenance = candidate.get("provenance")
        if candidate.get("role") != "base" or not isinstance(provenance, Mapping):
            continue
        if provenance.get("kind") != _DERIVED_KIND:
            continue
        if provenance.get("source_run_id") != requested_base_id:
            continue
        if candidate.get("judge_engine_digest") != judge_engine_digest:
            continue
        if _source_engine(candidate) != _source_engine(target):
            continue
        comparison = compare_results(target, candidate)
        if comparison.get("compatible") is True:
            comparison.update(
                base_run_id=candidate["run_id"],
                base_created_at=candidate["created_at"],
                reused=False,
            )
            candidates.append({"record": candidate, "comparison": comparison})
    if candidates:
        selected = max(candidates, key=lambda item: (item["record"]["created_at"], item["record"]["run_id"]))
        target["comparison"] = selected["comparison"]


def rejudge_runs(
    run_ids: Sequence[str],
    *,
    output_dir: Path,
    judge_factory: Callable[..., Any] | None = None,
    judge_engine_digest: str | None = None,
    now: Callable[[], datetime] | None = None,
    store_factory: Callable[[Path], ScoreStore] | None = None,
) -> list[dict[str, Any]]:
    """Create one new derived archive per source run.

    All source archives are loaded and digest-verified before a judge is
    constructed.  A keyboard interrupt is represented by a saved partial
    derived record so the immutable source remains usable for later retries.
    """

    if isinstance(run_ids, (str, bytes)) or not run_ids or len(run_ids) > 2:
        raise RejudgeError("provide one or two source run ids")
    store = store_factory(Path(output_dir)) if store_factory is not None else ScoreStore(Path(output_dir))
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run_id in run_ids:
        if run_id in seen:
            raise RejudgeError(f"duplicate source run id: {run_id}")
        seen.add(run_id)
        sources.append(store.load_run(_source_path(store, run_id)))

    if judge_engine_digest is None:
        judge_engine_digest = engine_content_digest()
    if not isinstance(judge_engine_digest, str) or len(judge_engine_digest) != 64:
        raise RejudgeError("judge_engine_digest must be a 64-character digest")

    records: list[dict[str, Any]] = []
    for source in sources:
        record = _rejudge_source(
            source,
            store=store,
            judge_factory=judge_factory,
            judge_engine_digest=judge_engine_digest,
            now=now,
        )
        records.append(record)
        if record.get("interrupted") is True:
            break
    _attach_derived_comparison(records)
    if len(records) == 1:
        _attach_existing_derived_base(
            records[0],
            source=sources[0],
            store=store,
            judge_engine_digest=judge_engine_digest,
        )
    for record in records:
        archive = store.save_run(record)
        # Return the digest-verified persisted shape, including the new
        # content_digest/content_sha256 fields, rather than the pre-save
        # mutable input accepted by ScoreStore.save_run.
        archived = store.load_run(archive)
        record.clear()
        record.update(archived)
    store.render_report()
    return records


def rejudge_run(
    run_id: str,
    *,
    output_dir: Path,
    judge_factory: Callable[..., Any] | None = None,
    judge_engine_digest: str | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for rejudging one immutable source run."""

    records = rejudge_runs(
        [run_id],
        output_dir=output_dir,
        judge_factory=judge_factory,
        judge_engine_digest=judge_engine_digest,
        now=now,
    )
    return records[0]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m patchmud.scoring.rejudge",
        description="Rejudge immutable public score archives without rerunning candidates.",
        allow_abbrev=False,
    )
    parser.add_argument("--run", dest="run_ids", action="append", required=True, metavar="RUN_ID")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".config/paulsha-patchmud")
    options = parser.parse_args(argv)
    if len(options.run_ids) > 2:
        parser.error("at most two --run source ids are supported")
    options.output_dir = options.output_dir.expanduser().resolve()
    return options


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        records = rejudge_runs(options.run_ids, output_dir=options.output_dir)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"patchmud rejudge: {_safe_error(exc)}", file=sys.stderr)
        return 1
    for record in records:
        print(f"{record['run_id']}: {record['status']}")
    return 0 if records and all(record.get("status") == "complete" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
