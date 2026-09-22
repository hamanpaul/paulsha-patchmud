"""Immutable on-disk storage for model-score runs and public Markdown reports."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["ScoreStore", "ScoreStoreError"]

SCHEMA_VERSION = 1
_DIGEST_KEYS = frozenset({"content_digest", "content_sha256"})
_PRIVATE_KEYS = frozenset(
    {
        "anchor",
        "anchors",
        "fixture",
        "fixture_dir",
        "fixture_path",
        "hidden",
        "hidden_assets",
        "private",
        "private_data",
        "secret",
        "secrets",
    }
)
_PRIVATE_PREFIXES = ("hidden_", "private_")
_PATH_METADATA_KEYS = frozenset(
    {
        "path",
        "paths",
        "dir",
        "directory",
        "file",
        "files",
        "cwd",
        "target",
        "fixture_dir",
        "fixture_path",
        "repo_dir",
        "repo_path",
        "worktree",
    }
)
_PUBLIC_CONTENT_FIELDS = frozenset(
    {
        "public_files",
        "public_repo",
        "transcript",
        "events",
        "native_events",
        "test_results",
        "final_report",
        "final_diff",
        "rubric",
    }
)
_MONETARY_KEYS = frozenset(
    {
        "amount",
        "cost",
        "input_cost",
        "output_cost",
        "price",
        "total_cost",
        "unit_price",
    }
)


class ScoreStoreError(ValueError):
    """Raised when a score archive is malformed, private, or immutable."""


def _canonical_json(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ScoreStoreError(f"record is not finite JSON: {exc}") from exc
    return text.encode("utf-8")


def _content_digest(record: Mapping[str, Any]) -> str:
    payload = {key: value for key, value in record.items() if key not in _DIGEST_KEYS}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _safe_run_id(value: Any) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ScoreStoreError("run_id must be a non-empty path component")
    if Path(value).name != value or "/" in value or "\\" in value or ".." in value:
        raise ScoreStoreError("run_id must not contain path traversal")
    return value


def _validate_created_at(value: Any) -> None:
    if not isinstance(value, str):
        raise ScoreStoreError("created_at must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScoreStoreError("created_at is not valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ScoreStoreError("created_at must carry UTC timezone")


def _is_private_metadata_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _PRIVATE_KEYS or lowered.startswith(_PRIVATE_PREFIXES)


def _assert_public(value: Any, *, path: str = "record", _content: bool = False) -> None:
    """Reject private metadata while preserving arbitrary public evidence.

    Only known schema boundaries are inspected for private/path metadata.  A
    source file name such as ``secret_manager.py`` or a transcript field named
    ``anchor`` is public evidence and must survive unchanged.
    """

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.casefold()
            if not _content and _is_private_metadata_key(key):
                raise ScoreStoreError(f"private field is not allowed at {path}.{key}")
            if (
                not _content
                and lowered in _PATH_METADATA_KEYS
                and isinstance(child, str)
                and _is_absolute_path(child)
            ):
                raise ScoreStoreError(f"absolute path is not allowed at {path}.{key}")
            if (
                not _content
                and lowered in _PATH_METADATA_KEYS
                and isinstance(child, str)
                and "/hidden/" in child.replace("\\", "/")
            ):
                raise ScoreStoreError(f"hidden path is not allowed at {path}.{key}")
            _assert_public(
                child,
                path=f"{path}.{key}",
                _content=_content or lowered in _PUBLIC_CONTENT_FIELDS,
            )
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_public(child, path=f"{path}[{index}]", _content=_content)
        return
    if isinstance(value, str):
        return


def _assert_money_strings(value: Any, *, path: str = "record") -> None:
    """Keep actual billing/provider fields decimal-safe.

    This helper is called only for pricing and provider usage schemas.  Public
    case files and transcripts may legitimately contain application fields
    named ``cost`` or ``amount`` with ordinary numeric values.
    """

    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.casefold()
            if lowered in _MONETARY_KEYS and child is not None and not isinstance(child, str):
                raise ScoreStoreError(f"monetary field must be a decimal string or null at {path}.{key}")
            _assert_money_strings(child, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _assert_money_strings(child, path=f"{path}[{index}]")


def _is_absolute_path(value: str) -> bool:
    return value.startswith(("/", "\\")) or (len(value) >= 3 and value[1] == ":" and value[2] in "/\\")


def _validate_record(record: Any, *, verify_digest: bool = True) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise ScoreStoreError("run archive must be a JSON object")
    result = copy.deepcopy(dict(record))
    if result.get("schema_version") != SCHEMA_VERSION:
        raise ScoreStoreError("unknown score archive schema_version")
    required = {
        "run_id",
        "created_at",
        "role",
        "profile",
        "fingerprint",
        "suite",
        "judge_model",
        "repeat",
        "status",
        "cases",
        "summary",
    }
    missing = sorted(required - result.keys())
    if missing:
        raise ScoreStoreError(f"score archive missing fields: {', '.join(missing)}")
    _safe_run_id(result["run_id"])
    _validate_created_at(result["created_at"])
    if result["role"] not in {"base", "target"}:
        raise ScoreStoreError("role must be base or target")
    if not isinstance(result["profile"], Mapping):
        raise ScoreStoreError("profile must be an object")
    if not isinstance(result["fingerprint"], str) or not result["fingerprint"]:
        raise ScoreStoreError("fingerprint must be a non-empty string")
    if not isinstance(result["suite"], Mapping):
        raise ScoreStoreError("suite must be an object")
    suite = result["suite"]
    for key in ("id", "version", "suite_hash", "rubric_version"):
        if not isinstance(suite.get(key), str) or not suite[key]:
            raise ScoreStoreError(f"suite.{key} must be a non-empty string")
    if not isinstance(suite.get("case_ids"), list) or not suite["case_ids"] or not all(isinstance(item, str) and item for item in suite["case_ids"]):
        raise ScoreStoreError("suite.case_ids must be a non-empty string list")
    if len(set(suite["case_ids"])) != len(suite["case_ids"]):
        raise ScoreStoreError("suite.case_ids must not contain duplicates")
    if result["judge_model"] != "jev-1.13.0":
        raise ScoreStoreError("judge_model must be pinned jev-1.13.0")
    if isinstance(result["repeat"], bool) or not isinstance(result["repeat"], int) or result["repeat"] < 1:
        raise ScoreStoreError("repeat must be a positive integer")
    if result["status"] not in {"complete", "partial", "error"}:
        raise ScoreStoreError("status must be complete, partial, or error")
    if not isinstance(result["cases"], list):
        raise ScoreStoreError("cases must be a list")
    if not isinstance(result["summary"], Mapping):
        raise ScoreStoreError("summary must be an object")
    provenance = result.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, Mapping):
            raise ScoreStoreError("provenance must be an object")
        if provenance.get("kind") == "derived-rejudgment-v1":
            required_provenance = {
                "source_run_id",
                "source_created_at",
                "source_content_digest",
                "source_engine_digest",
                "judge_engine_digest",
                "reused_execution",
                "baseline_eligible",
            }
            missing_provenance = sorted(required_provenance - provenance.keys())
            if missing_provenance:
                raise ScoreStoreError(
                    "derived provenance missing fields: " + ", ".join(missing_provenance)
                )
            _safe_run_id(provenance["source_run_id"])
            _validate_created_at(provenance["source_created_at"])
            if not isinstance(provenance["source_content_digest"], str) or len(provenance["source_content_digest"]) != 64:
                raise ScoreStoreError("derived source_content_digest is malformed")
            source_engine = provenance["source_engine_digest"]
            if source_engine is not None and (not isinstance(source_engine, str) or len(source_engine) != 64):
                raise ScoreStoreError("derived source_engine_digest is malformed")
            if result.get("engine_digest") != source_engine:
                raise ScoreStoreError("derived engine_digest disagrees with source engine")
            if not isinstance(provenance["judge_engine_digest"], str) or len(provenance["judge_engine_digest"]) != 64:
                raise ScoreStoreError("derived judge_engine_digest is malformed")
            if result.get("judge_engine_digest") != provenance["judge_engine_digest"]:
                raise ScoreStoreError("derived judge_engine_digest disagrees with record")
            if "judge_protocol_version" in result or "judge_protocol_version" in provenance:
                if not result.get("judge_protocol_version") or result.get("judge_protocol_version") != provenance.get("judge_protocol_version"):
                    raise ScoreStoreError("derived judge_protocol_version disagrees with record")
            if provenance["reused_execution"] is not True:
                raise ScoreStoreError("derived reused_execution must be true")
            if provenance["baseline_eligible"] is not False:
                raise ScoreStoreError("derived baseline_eligible must be false")
    # Billing provenance is validated only in the fields whose schemas carry
    # provider/pricing data.  Public case files and transcripts are arbitrary
    # evidence and may contain application values called ``cost`` or
    # ``amount``.
    for field in ("cost", "pricing_snapshot"):
        if field in result:
            _assert_money_strings(result[field], path=field)
    summary = result.get("summary")
    if isinstance(summary, Mapping):
        for field in ("cost", "pricing_snapshot"):
            if field in summary:
                _assert_money_strings(summary[field], path=f"summary.{field}")
    for index, row in enumerate(result["cases"]):
        if not isinstance(row, Mapping):
            raise ScoreStoreError(f"case row {index} must be an object")
        if "case" not in row or not isinstance(row["case"], Mapping):
            raise ScoreStoreError(f"case row {index} is missing public case snapshot")
        case_id = row["case"].get("id")
        if not isinstance(case_id, str) or case_id not in suite["case_ids"]:
            raise ScoreStoreError(f"case row {index} has unknown public case id")
        if "case_id" in row and row["case_id"] != case_id:
            raise ScoreStoreError(f"case row {index} case_id disagrees with public snapshot")
        _assert_public(row["case"], path=f"cases[{index}].case")
        if "execution" in row:
            _assert_public(row["execution"], path=f"cases[{index}].execution")
        if "judgment" in row:
            _assert_public(row["judgment"], path=f"cases[{index}].judgment")
        for artifact_name in ("execution", "judgment"):
            artifact = row.get(artifact_name)
            if isinstance(artifact, Mapping) and isinstance(artifact.get("usage"), (Mapping, list, tuple)):
                _assert_money_strings(
                    artifact["usage"],
                    path=f"cases[{index}].{artifact_name}.usage",
                )
    if verify_digest:
        stored = result.get("content_digest")
        if not isinstance(stored, str) or len(stored) != 64:
            raise ScoreStoreError("content_digest is missing or malformed")
        if stored != _content_digest(result):
            raise ScoreStoreError("score archive content digest mismatch")
        if "content_sha256" in result and result["content_sha256"] != stored:
            raise ScoreStoreError("content_sha256 does not match content_digest")
    return result


def _full_coverage(record: Mapping[str, Any]) -> bool:
    if record.get("status") != "complete":
        return False
    if record.get("phase") is not None and record.get("phase") != "formal":
        return False
    if record.get("evaluation_complete") is False:
        return False
    summary = record.get("summary")
    suite = record.get("suite")
    if not isinstance(summary, Mapping) or not isinstance(suite, Mapping):
        return False
    if summary.get("complete") is not True:
        return False
    coverage = summary.get("coverage")
    case_ids = suite.get("case_ids")
    repeat = record.get("repeat")
    if not isinstance(coverage, Mapping) or not isinstance(case_ids, list) or not isinstance(repeat, int):
        return False
    expected_slots = len(case_ids) * repeat
    if coverage.get("expected") != expected_slots or coverage.get("scored") != expected_slots:
        return False
    rows = record.get("cases")
    if not isinstance(rows, list):
        return False
    seen: set[tuple[str, int]] = set()
    for row in rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("case"), Mapping):
            return False
        case_id = row["case"].get("id")
        repetition = row.get("repetition", 1)
        judgment = row.get("judgment")
        if "case_id" in row and row["case_id"] != case_id:
            return False
        if not isinstance(case_id, str) or case_id not in case_ids:
            return False
        if isinstance(repetition, bool) or not isinstance(repetition, int) or not 1 <= repetition <= repeat:
            return False
        key = (case_id, repetition)
        if key in seen:
            return False
        seen.add(key)
        if not isinstance(judgment, Mapping) or judgment.get("status") != "scored" or judgment.get("model") != "jev-1.13.0":
            return False
        if not isinstance(judgment.get("score"), (int, float)) or isinstance(judgment.get("score"), bool):
            return False
        dimensions = judgment.get("dimensions")
        if not isinstance(dimensions, Mapping) or set(dimensions) != {
            "fulfillment",
            "evidence",
            "constraints",
            "verification",
        }:
            return False
        scores: list[float] = []
        for dimension in ("fulfillment", "evidence", "constraints", "verification"):
            answer = dimensions[dimension]
            if not isinstance(answer, Mapping):
                return False
            score = answer.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(float(score)) or not 0 <= float(score) <= 4:
                return False
            scores.append(float(score))
        if not math.isfinite(float(judgment["score"])) or not 0 <= float(judgment["score"]) <= 100:
            return False
        if not math.isclose(float(judgment["score"]), 25 * sum(scores) / 4, rel_tol=0.0, abs_tol=1e-6):
            return False
    if seen != {(case_id, repetition) for case_id in case_ids for repetition in range(1, repeat + 1)}:
        return False

    # Recompute all arithmetic from the immutable rows.  A forged summary or
    # a score that disagrees with its four native dimensions must never seed a
    # reusable baseline.
    from .aggregation import aggregate_results

    expected_cases = [
        {"id": case_id, "category": next(
            row["case"].get("category", "unknown")
            for row in rows
            if row["case"].get("id") == case_id
        )}
        for case_id in case_ids
    ]
    recomputed = aggregate_results(rows, expected_cases, repeat=repeat)
    if not recomputed.get("complete"):
        return False
    if summary.get("complete") is not True or summary.get("coverage") != recomputed.get("coverage"):
        return False
    if not _same_number(summary.get("total"), recomputed.get("total")):
        return False
    declared_cases = summary.get("cases")
    recomputed_cases = recomputed.get("cases", {})
    if not isinstance(declared_cases, Mapping) or not isinstance(recomputed_cases, Mapping):
        return False
    for case_id, expected_case in recomputed_cases.items():
        declared_case = declared_cases.get(case_id)
        if not isinstance(declared_case, Mapping):
            return False
        if not _same_number(declared_case.get("score"), expected_case.get("score")):
            return False
        if declared_case.get("complete") is not True or expected_case.get("complete") is not True:
            return False
    declared_categories = summary.get("categories")
    if not isinstance(declared_categories, Mapping):
        return False
    recomputed_categories = recomputed.get("categories", {})
    for category, expected_category in recomputed_categories.items():
        declared = declared_categories.get(category)
        if not isinstance(declared, Mapping) or not _same_number(declared.get("score"), expected_category.get("score")):
            return False
    return True


def _same_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return math.isfinite(float(left)) and math.isfinite(float(right)) and math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-6)


class ScoreStore:
    """Persist immutable score runs below ``root/runs``."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_root = self.root / "runs"
        self.report_path = self.root / "models-score.md"
        self.lock_path = self.root / ".models-score.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def save_run(self, record: dict[str, Any]) -> Path:
        """Write one immutable, digest-addressed run archive atomically."""

        if not isinstance(record, Mapping):
            raise ScoreStoreError("run record must be an object")
        if any(key in record for key in _DIGEST_KEYS):
            raise ScoreStoreError("caller must not supply content digest fields")
        validated = _validate_record(record, verify_digest=False)
        run_id = _safe_run_id(validated["run_id"])
        run_dir = self.runs_root / run_id
        destination = run_dir / "run.json"
        if run_dir.exists() or destination.exists():
            raise ScoreStoreError(f"immutable run already exists: {run_id}")
        payload = dict(validated)
        digest = _content_digest(payload)
        payload["content_digest"] = digest
        payload["content_sha256"] = digest
        run_dir.mkdir(parents=True, exist_ok=False)
        temp_fd, temp_name = tempfile.mkstemp(prefix=".run-", suffix=".tmp", dir=run_dir)
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                handle.write(_canonical_json(payload).decode("utf-8"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            # A hard-link publish fails atomically if another writer claimed
            # the same run id between the existence check and this point.
            os.link(temp_name, destination)
            os.unlink(temp_name)
            directory_fd = os.open(run_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            # A failed write must never masquerade as an archived run.
            destination.unlink(missing_ok=True)
            if run_dir.exists():
                try:
                    run_dir.rmdir()
                except OSError:
                    # A concurrent immutable writer may own this directory.
                    pass
            raise
        return destination

    def load_run(self, path: Path) -> dict[str, Any]:
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScoreStoreError(f"cannot read score archive {path}") from exc
        return _validate_record(raw, verify_digest=True)

    def find_baseline(self, fingerprint: str) -> dict[str, Any] | None:
        """Find the newest complete, full-coverage matching archive."""

        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        matches: list[dict[str, Any]] = []
        if not self.runs_root.is_dir():
            return None
        for path in sorted(self.runs_root.glob("*/run.json")):
            try:
                record = self.load_run(path)
            except ScoreStoreError:
                continue
            provenance = record.get("provenance")
            if isinstance(provenance, Mapping) and (
                provenance.get("kind") == "derived-rejudgment-v1"
                or provenance.get("baseline_eligible") is False
            ):
                continue
            if record.get("fingerprint") == fingerprint and _full_coverage(record):
                matches.append(record)
        if not matches:
            return None
        return max(matches, key=lambda item: (item["created_at"], item["run_id"]))

    def render_report(self) -> Path:
        """Render all valid immutable histories under an exclusive file lock."""

        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                records: list[dict[str, Any]] = []
                invalid: list[str] = []
                for path in sorted(self.runs_root.glob("*/run.json")):
                    try:
                        records.append(self.load_run(path))
                    except ScoreStoreError:
                        invalid.append(path.parent.name)
                records.sort(key=lambda item: (item["created_at"], item["run_id"]))
                markdown = self._render_markdown(records, invalid)
                self._atomic_write_report(markdown)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return self.report_path

    def _atomic_write_report(self, markdown: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=".models-score-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(markdown)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.report_path)
            directory_fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

    @staticmethod
    def _render_markdown(records: list[Mapping[str, Any]], invalid: list[str]) -> str:
        """Render a readable public report without allowing content to escape fences."""

        def inline(value: Any) -> str:
            return str(value).replace("`", "'").replace("\n", " ").strip()

        def json_text(value: Any) -> str:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).replace(
                "```", r"\u0060\u0060\u0060"
            )

        def fence(value: Any, language: str = "text") -> list[str]:
            text = "" if value is None else str(value)
            if not isinstance(value, str):
                text = json_text(value)
            return [f"```{language}", text.replace("```", r"\u0060\u0060\u0060"), "```"]

        def profile_lines(profile: Any) -> list[str]:
            if not isinstance(profile, Mapping):
                return [f"- Profile: `{inline(profile)}`"]
            result = ["- Profile:"]
            for name in ("requested", "resolved", "observed"):
                if name in profile:
                    result.append(f"  - {name}: `{inline(json_text(profile[name]))}`")
            for name in ("harness_version", "execution_mode", "protocol_version"):
                if name in profile:
                    result.append(f"  - {name}: `{inline(profile[name])}`")
            return result

        lines = [
            "# PatchMUD model scores",
            "",
            "This report is generated from immutable, digest-verified run archives.",
            "JEV scores are quality scores; usage, timing, and cost remain separate provenance.",
            "",
        ]
        if invalid:
            lines.extend(["## Invalid archives", "", "The following archives were omitted after validation failed:", ""])
            lines.extend(f"- `{inline(run_id)}`" for run_id in invalid)
            lines.append("")
        if not records:
            lines.extend(["No valid runs have been archived.", ""])
            return "\n".join(lines)

        for record in records:
            run_id = record["run_id"]
            summary = record.get("summary", {})
            lines.extend(
                [
                    f"## Run `{inline(run_id)}`",
                    "",
                    f"- Created: `{inline(record['created_at'])}`",
                    f"- Role: `{inline(record['role'])}`",
                    f"- Phase: `{inline(record.get('phase', 'unknown'))}`",
                    f"- Status: `{inline(record['status'])}`",
                    f"- Fingerprint: `{inline(record['fingerprint'])}`",
                    f"- Suite: `{inline(json_text(record.get('suite', {})))}`",
                    f"- Judge: `{inline(record['judge_model'])}`",
                    f"- Judge protocol: `{inline(record.get('judge_protocol_version', 'full-state-v1'))}`",
                    f"- Repetitions: `{inline(record['repeat'])}`",
                    f"- Total: `{inline(summary.get('total') if isinstance(summary, Mapping) else None)}`",
                    f"- Coverage: `{inline(json_text(summary.get('coverage', {}) if isinstance(summary, Mapping) else {}))}`",
                ]
            )
            lines.extend(profile_lines(record.get("profile", {})))
            provenance = record.get("provenance")
            if isinstance(provenance, Mapping) and provenance.get("kind") == "derived-rejudgment-v1":
                lines.extend(
                    [
                        "- Derived rejudgment: `derived-rejudgment-v1`",
                        f"- Source run: `{inline(provenance.get('source_run_id'))}`",
                        f"- Source date: `{inline(provenance.get('source_created_at'))}`",
                        f"- Source content digest: `{inline(provenance.get('source_content_digest'))}`",
                        f"- Source engine digest: `{inline(provenance.get('source_engine_digest'))}`",
                        f"- Judge engine digest: `{inline(provenance.get('judge_engine_digest'))}`",
                        f"- Source judge protocol: `{inline(provenance.get('source_judge_protocol_version', 'full-state-v1'))}`",
                        f"- Reused execution: `{inline(provenance.get('reused_execution'))}`",
                        f"- Baseline eligible: `{inline(provenance.get('baseline_eligible'))}`",
                    ]
                )
            cost = record.get("cost")
            if cost is None and isinstance(summary, Mapping):
                cost = summary.get("cost")
            if cost is None and record.get("pricing_snapshot") is None:
                lines.append("- Cost: `unknown (no pricing snapshot)`")
            else:
                lines.append(f"- Cost: `{inline(cost)}`")

            lines.extend(["", "### Categories", "", "| Category | Score | Cases | Complete |", "|---|---:|---:|:---:|"])
            categories = summary.get("categories", {}) if isinstance(summary, Mapping) else {}
            if isinstance(categories, Mapping):
                for category, value in categories.items():
                    if isinstance(value, Mapping):
                        lines.append(
                            f"| `{inline(category)}` | `{inline(value.get('score'))}` | "
                            f"{inline(value.get('scored_cases', ''))}/{inline(value.get('expected_cases', ''))} | "
                            f"`{inline(value.get('complete'))}` |"
                        )
                    else:
                        lines.append(f"| `{inline(category)}` | `{inline(value)}` | | |")

            lines.extend(["", "### Cases", ""])
            for row in record.get("cases", []):
                case = row.get("case", {}) if isinstance(row, Mapping) else {}
                judgment = row.get("judgment", {}) if isinstance(row, Mapping) else {}
                execution = row.get("execution", {}) if isinstance(row, Mapping) else {}
                case_id = case.get("id", "unknown") if isinstance(case, Mapping) else "unknown"
                repetition = row.get("repetition", 1) if isinstance(row, Mapping) else 1
                title = case.get("title", "") if isinstance(case, Mapping) else ""
                max_turns = case.get("max_turns", "?") if isinstance(case, Mapping) else "?"
                wall_seconds = case.get("wall_seconds", "?") if isinstance(case, Mapping) else "?"
                budget_label = (f"{inline(wall_seconds)} seconds (native tool turns are not capped)"
                                if max_turns is None else f"{inline(max_turns)} turns / {inline(wall_seconds)} seconds")
                lines.extend(
                    [
                        f"#### `{inline(case_id)}` — {inline(title)}",
                        "",
                        f"- Repetition: `{inline(repetition)}`",
                        f"- Budget: `{budget_label}`",
                        f"- Prompt: {inline(case.get('prompt', '') if isinstance(case, Mapping) else '')}",
                        "- Requirements:",
                    ]
                )
                requirements = case.get("requirements", []) if isinstance(case, Mapping) else []
                if isinstance(requirements, list):
                    lines.extend(f"  - {inline(requirement)}" for requirement in requirements)
                lines.extend(["", "##### JEV dimensions", "", "| Dimension | Score (0–4) | Confidence |", "|---|---:|---:|"])
                dimensions = judgment.get("dimensions", {}) if isinstance(judgment, Mapping) else {}
                if isinstance(dimensions, Mapping):
                    for dimension in ("fulfillment", "evidence", "constraints", "verification"):
                        answer = dimensions.get(dimension, {})
                        if isinstance(answer, Mapping):
                            lines.append(f"| `{dimension}` | `{inline(answer.get('score'))}` | `{inline(answer.get('confidence'))}` |")
                lines.extend(
                    [
                        "",
                        f"- Case score: `{inline(judgment.get('score') if isinstance(judgment, Mapping) else None)}`",
                        f"- Judge wall time: `{inline(judgment.get('wall_ms') if isinstance(judgment, Mapping) else None)} ms`",
                        f"- Native usage: `{inline(json_text(judgment.get('usage') if isinstance(judgment, Mapping) else None))}`",
                        "- Test outcomes:",
                    ]
                )
                tests = execution.get("test_results", []) if isinstance(execution, Mapping) else []
                if isinstance(tests, list) and tests:
                    for test in tests:
                        if isinstance(test, Mapping):
                            test_id = test.get("id", test.get("evidence_id", "test"))
                            lines.append(f"  - `{inline(test_id)}`: `{inline(test.get('status'))}`")
                        else:
                            lines.append(f"  - `{inline(test)}`")
                else:
                    lines.append("  - `unknown`")
                lines.extend(
                    [
                        f"- Execution status: `{inline(execution.get('status') if isinstance(execution, Mapping) else None)}`; end reason: `{inline(execution.get('end_reason') if isinstance(execution, Mapping) else None)}`",
                        f"- Execution wall time: `{inline(execution.get('wall_ms') if isinstance(execution, Mapping) else None)} ms`",
                        "- Final report:",
                        *fence(execution.get("final_report") if isinstance(execution, Mapping) else "", "text"),
                        "- Final diff:",
                        *fence(execution.get("final_diff") if isinstance(execution, Mapping) else "", "diff"),
                    ]
                )
                if isinstance(case, Mapping):
                    lines.extend(["", "<details>", "<summary>Public case materials and rubric</summary>", "", "Prompt:", *fence(case.get("prompt", ""), "text"), "Public files:"])
                    public_files = case.get("public_files", {})
                    if isinstance(public_files, Mapping):
                        for path, content in public_files.items():
                            lines.extend([f"### `{inline(path)}`", *fence(content, "text")])
                    lines.extend(["Rubric:", *fence(case.get("rubric", {}), "json"), "</details>", ""])
                lines.extend(["<details>", "<summary>Transcript, tool events, distributions, and raw usage</summary>", "", "Transcript:", *fence(execution.get("transcript", []) if isinstance(execution, Mapping) else [], "json"), "Events:", *fence(execution.get("events", []) if isinstance(execution, Mapping) else [], "json"), "Score distributions:", *fence(dimensions, "json"), "Raw execution:", *fence(execution, "json"), "Raw judgment:", *fence(judgment, "json"), "</details>", ""])

            if "comparison" in record:
                comparison = record["comparison"]
                lines.extend(
                    [
                        "### Comparison",
                        "",
                        f"- Base run: `{inline(comparison.get('base_run_id') if isinstance(comparison, Mapping) else None)}`",
                        f"- Base created: `{inline(comparison.get('base_created_at') if isinstance(comparison, Mapping) else None)}`",
                        f"- Reused: `{inline(comparison.get('reused') if isinstance(comparison, Mapping) else None)}`",
                    ]
                )
                deltas = comparison.get("deltas", {}) if isinstance(comparison, Mapping) else {}
                total_delta = deltas.get("total", {}) if isinstance(deltas, Mapping) else {}
                if isinstance(total_delta, Mapping):
                    lines.extend(["", "| Total target | Base | Delta |", "|---:|---:|---:|", f"| `{inline(total_delta.get('target'))}` | `{inline(total_delta.get('base'))}` | `{inline(total_delta.get('delta'))}` |"])
                category_deltas = deltas.get("categories", {}) if isinstance(deltas, Mapping) else {}
                if isinstance(category_deltas, Mapping) and category_deltas:
                    lines.extend(["", "| Category | Delta |", "|---|---:|"])
                    for category, delta in category_deltas.items():
                        value = delta.get("delta") if isinstance(delta, Mapping) else delta
                        lines.append(f"| `{inline(category)}` | `{inline(value)}` |")
                case_deltas = deltas.get("cases", {}) if isinstance(deltas, Mapping) else {}
                if isinstance(case_deltas, Mapping) and case_deltas:
                    lines.extend(["", "| Case | Delta |", "|---|---:|"])
                    for case_id, delta in case_deltas.items():
                        value = delta.get("delta") if isinstance(delta, Mapping) else delta
                        lines.append(f"| `{inline(case_id)}` | `{inline(value)}` |")
                lines.extend(["", "<details>", "<summary>Raw comparison</summary>", "", *fence(comparison, "json"), "</details>", ""])

            lines.extend(
                [
                    f"[Immutable run archive](runs/{inline(run_id)}/run.json)",
                    "",
                    "<details>",
                    "<summary>Immutable archive record</summary>",
                    "",
                    *fence(record, "json"),
                    "</details>",
                    "",
                ]
            )
        return "\n".join(lines)
