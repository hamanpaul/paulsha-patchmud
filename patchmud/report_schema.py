"""report v2 的結構驗證；不依賴 Cortex 或外部 schema 套件。"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from patchmud.usage_provenance import (
    USAGE_FIELDS,
    UsageEvidenceError,
    validate_usage_evidence,
)

__all__ = ["REPORT_SCHEMA_VERSION", "ReportSchemaError", "validate_report_v2"]

REPORT_SCHEMA_VERSION = 2
_REPORT_KEYS = {
    "schema_version",
    "producer",
    "generated_at",
    "runs_included",
    "runs_skipped",
    "runs",
    "leaderboards",
    "report_fingerprint",
}
_RUN_KEYS = {
    "run_id",
    "model",
    "loadout",
    "encounter",
    "role",
    "benchmark_type",
    "profile_id",
    "deck_id",
    "deck_digest",
    "evaluator_revision",
    "measured_dimensions",
    "unmeasured_dimensions",
    "deck_coverage",
    "measured_at",
    "clear",
    "end_reason",
    "protocol_failed",
    "failure_reason",
    "failure_source",
    "power_total",
    "cost",
    "work_tokens",
    "observable_tokens",
    "usage_provenance",
    "run_digest",
    "artifact_digests",
    "control",
    "ftr",
    "tau_uncalibrated",
}
_BOARDS = {
    "clear_rate",
    "cost_per_clear",
    "tokens_per_clear",
    "qaty",
    "eutb",
    "power",
    "control",
    "ftr",
}
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_GROUP_FIELDS = {
    "cohort_id",
    "cohort_identity_complete",
    "role",
    "benchmark_type",
    "profile_id",
    "deck_digest",
    "evaluator_revision",
    "coverage_expected_encounters",
    "coverage_observed_encounters",
    "coverage_complete",
}


class ReportSchemaError(ValueError):
    """report v2 結構或穩定 fingerprint 違反契約。"""


def validate_report_v2(report: object) -> None:
    """驗證 report v2 的固定外框、run/usage 欄位與 fingerprint。"""
    if not isinstance(report, dict):
        raise ReportSchemaError("report 必須是 object")
    if set(report) != _REPORT_KEYS:
        raise ReportSchemaError("report 欄位集合不符 report v2 schema")
    if type(report["schema_version"]) is not int or report["schema_version"] != 2:
        raise ReportSchemaError("schema_version 必須是 2")
    producer = report["producer"]
    if (
        not isinstance(producer, dict)
        or set(producer) != {"name", "version"}
        or not all(isinstance(producer[key], str) and producer[key] for key in producer)
    ):
        raise ReportSchemaError("producer 必須含非空 name/version")
    if not isinstance(report["generated_at"], str) or not _TIMESTAMP_RE.fullmatch(
        report["generated_at"]
    ):
        raise ReportSchemaError("generated_at 必須是 UTC RFC3339 秒精度時間")
    runs = report["runs"]
    if not isinstance(runs, list) or type(report["runs_included"]) is not int:
        raise ReportSchemaError("runs/runs_included 型別不符")
    if report["runs_included"] != len(runs):
        raise ReportSchemaError("runs_included 與 runs 筆數不符")
    skipped = report["runs_skipped"]
    if not isinstance(skipped, list) or any(
        not isinstance(item, dict)
        or set(item) != {"run_id", "reason"}
        or not all(isinstance(item[key], str) for key in item)
        for item in skipped
    ):
        raise ReportSchemaError("runs_skipped 欄位不符")
    for row in runs:
        _validate_run(row)
    leaderboards = report["leaderboards"]
    if not isinstance(leaderboards, dict) or set(leaderboards) != _BOARDS:
        raise ReportSchemaError("leaderboards 欄位集合不符")
    for name, board in leaderboards.items():
        _validate_board(name, board)
    fingerprint = report["report_fingerprint"]
    if not isinstance(fingerprint, str) or not _DIGEST_RE.fullmatch(fingerprint):
        raise ReportSchemaError("report_fingerprint 格式不符")
    stable = {
        key: value
        for key, value in report.items()
        if key not in ("generated_at", "report_fingerprint")
    }
    encoded = _canonical_json(stable)
    expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
    if fingerprint != expected:
        raise ReportSchemaError("report_fingerprint 與穩定 report 內容不符")
    _canonical_json(report)


def _validate_run(row: object) -> None:
    if not isinstance(row, dict) or set(row) != _RUN_KEYS:
        raise ReportSchemaError("runs[] 欄位集合不符 report v2 schema")
    string_keys = _RUN_KEYS - {
        "measured_dimensions",
        "unmeasured_dimensions",
        "deck_coverage",
        "clear",
        "protocol_failed",
        "failure_reason",
        "failure_source",
        "power_total",
        "cost",
        "work_tokens",
        "observable_tokens",
        "usage_provenance",
        "artifact_digests",
        "control",
        "ftr",
        "tau_uncalibrated",
    }
    if any(not isinstance(row[key], str) or not row[key] for key in string_keys):
        raise ReportSchemaError("runs[] identity/time 欄位必須是非空字串")
    if type(row["clear"]) is not int or row["clear"] not in (0, 1):
        raise ReportSchemaError("runs[].clear 必須是 0/1")
    if not isinstance(row["protocol_failed"], bool):
        raise ReportSchemaError("runs[].protocol_failed 必須是 bool")
    if row["failure_reason"] is not None and not isinstance(row["failure_reason"], str):
        raise ReportSchemaError("runs[].failure_reason 必須是字串或 null")
    if row["failure_source"] is not None and not isinstance(row["failure_source"], str):
        raise ReportSchemaError("runs[].failure_source 必須是字串或 null")
    for name in ("power_total", "control", "ftr"):
        if type(row[name]) not in (int, float):
            raise ReportSchemaError(f"runs[].{name} 必須是有限數值")
    cost = row["cost"]
    if not isinstance(cost, str):
        raise ReportSchemaError("runs[].cost 必須是十進位字串或 NA")
    if cost != "NA":
        try:
            parsed_cost = Decimal(cost)
        except InvalidOperation as exc:
            raise ReportSchemaError("runs[].cost 必須是十進位字串或 NA") from exc
        if not parsed_cost.is_finite() or parsed_cost < 0:
            raise ReportSchemaError("runs[].cost 必須是非負有限金額")
    for name in ("work_tokens", "observable_tokens"):
        value = row[name]
        if value != "NA" and (type(value) is not int or value < 0):
            raise ReportSchemaError(f"runs[].{name} 必須是非負整數或 NA")
    dimensions = row["measured_dimensions"]
    if not isinstance(dimensions, list) or not all(isinstance(item, str) for item in dimensions):
        raise ReportSchemaError("runs[].measured_dimensions 必須是字串陣列")
    if not isinstance(row["unmeasured_dimensions"], dict):
        raise ReportSchemaError("runs[].unmeasured_dimensions 必須是 object")
    coverage = row["deck_coverage"]
    if (
        not isinstance(coverage, dict)
        or set(coverage)
        != {"expected_encounters", "observed_encounters", "complete"}
        or not all(
            isinstance(coverage[key], list)
            and all(isinstance(item, str) for item in coverage[key])
            for key in ("expected_encounters", "observed_encounters")
        )
        or (
            coverage["complete"] is not None
            and not isinstance(coverage["complete"], bool)
        )
    ):
        raise ReportSchemaError("runs[].deck_coverage 欄位不符")
    if not isinstance(row["tau_uncalibrated"], bool):
        raise ReportSchemaError("runs[].tau_uncalibrated 必須是 bool")
    for name in ("run_digest", "deck_digest", "evaluator_revision"):
        if row[name] == "legacy/unknown" and name in (
            "deck_digest",
            "evaluator_revision",
        ):
            continue
        if not isinstance(row[name], str) or not _DIGEST_RE.fullmatch(row[name]):
            raise ReportSchemaError(f"runs[].{name} digest 格式不符")
    artifacts = row["artifact_digests"]
    if not isinstance(artifacts, dict) or any(
        not isinstance(path, str)
        or path.startswith("/")
        or ".." in path.split("/")
        or not isinstance(digest, str)
        or not _DIGEST_RE.fullmatch(digest)
        for path, digest in artifacts.items()
    ):
        raise ReportSchemaError("runs[].artifact_digests 欄位不符")
    _validate_aggregate_usage(row["usage_provenance"])


def _validate_aggregate_usage(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "quantity_kind",
        "source",
        "coverage",
        "fields",
        "calls",
    }:
        raise ReportSchemaError("usage_provenance 外框不符")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ReportSchemaError("usage_provenance schema_version 不支援")
    if value["quantity_kind"] not in ("usage_delta", "usage_total", "mixed"):
        raise ReportSchemaError("usage_provenance quantity_kind 不支援")
    if not isinstance(value["source"], dict) or not all(
        isinstance(value["source"].get(key), str)
        for key in ("source_id", "source_schema", "adapter_version")
    ):
        raise ReportSchemaError("usage_provenance source 不符")
    coverage = value["coverage"]
    if (
        not isinstance(coverage, dict)
        or set(coverage) != {"state", "gaps"}
        or coverage["state"] not in ("complete", "partial", "unknown")
        or not isinstance(coverage["gaps"], list)
        or any(
            not isinstance(gap, dict)
            or set(gap) != {"scope", "reason"}
            or not isinstance(gap["scope"], str)
            or not isinstance(gap["reason"], str)
            for gap in coverage["gaps"]
        )
    ):
        raise ReportSchemaError("usage_provenance coverage 不符")
    fields = value["fields"]
    if not isinstance(fields, dict) or set(fields) != set(USAGE_FIELDS):
        raise ReportSchemaError("usage_provenance fields 欄位集合不符")
    for name, field in fields.items():
        if not isinstance(field, dict) or field.get("state") not in (
            "observed",
            "estimated",
            "unknown",
        ):
            raise ReportSchemaError(f"usage field {name} state 不符")
        state = field["state"]
        if state == "unknown":
            if "value" in field or not isinstance(field.get("reason"), str) or not field["reason"]:
                raise ReportSchemaError(f"usage field {name}: unknown 不可帶 value")
        elif type(field.get("value")) is not int or field["value"] < 0:
            raise ReportSchemaError(f"usage field {name}: {state} 必須帶非負整數 value")
        if field.get("method") not in ("executor_usage", "estimate", "legacy"):
            raise ReportSchemaError(f"usage field {name} method 不符")
        if state == "estimated" and field["method"] != "estimate":
            raise ReportSchemaError(f"usage field {name}: estimated method 不符")
        if field.get("unit_ref") != {
            "state": "known",
            "value": {"unit_id": "token", "version": "1"},
        }:
            raise ReportSchemaError(f"usage field {name} unit_ref 不符")
        if not isinstance(field.get("semantics"), dict):
            raise ReportSchemaError(f"usage field {name} semantics 不符")
        if state == "unknown" and field["method"] == "estimate":
            raise ReportSchemaError(f"usage field {name}: unknown 不得標 estimate")
    calls = value["calls"]
    if not isinstance(calls, list):
        raise ReportSchemaError("usage_provenance calls 必須是陣列")
    for call in calls:
        try:
            validate_usage_evidence(call, require_seq=True)
        except UsageEvidenceError as exc:
            raise ReportSchemaError(f"usage call evidence 不符：{exc}") from exc


def _validate_board(name: str, board: object) -> None:
    if (
        not isinstance(board, dict)
        or board.get("status") not in ("ok", "skipped", "unavailable")
        or (
            "rows" not in board
            and board.get("status") != "skipped"
        )
        or (
            "rows" in board
            and not isinstance(board["rows"], list)
        )
    ):
        raise ReportSchemaError(f"leaderboards.{name} 欄位不符")
    for row in board.get("rows", []):
        if not isinstance(row, dict) or not _GROUP_FIELDS.issubset(row):
            raise ReportSchemaError(f"leaderboards.{name}.rows[] cohort 欄位不符")
        if not isinstance(row["cohort_id"], str) or not isinstance(
            row["cohort_identity_complete"], bool
        ):
            raise ReportSchemaError(f"leaderboards.{name}.rows[] cohort 型別不符")


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReportSchemaError(f"report 不是有限 JSON 資料：{exc}") from exc
