"""欄位級 token usage provenance；封存正規化值，不封存 provider payload。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from patchmud.ledger.tokens import LedgerEntry, map_usage

__all__ = [
    "USAGE_EVIDENCE_SCHEMA_VERSION",
    "USAGE_FIELDS",
    "UsageEvidenceError",
    "map_usage_with_provenance",
    "legacy_usage_evidence",
    "validate_usage_evidence",
]

USAGE_EVIDENCE_SCHEMA_VERSION = 1
USAGE_FIELDS = (
    "input_uncached",
    "input_cached",
    "output_visible",
    "reasoning",
    "billed_input_total",
    "billed_output_total",
    "unallocated",
)
_METHODS = frozenset(("executor_usage", "estimate", "legacy"))
_PROVIDERS = {
    "anthropic": "anthropic-usage/v1",
    "openai": "openai-usage/v1",
    "codex": "codex-usage/v1",
    "agy": "agy-usage/v1",
    "human": "human-usage/v1",
}
_RAW_FIELDS = {
    "anthropic": {
        "input_uncached": ("input_tokens",),
        "input_cached": ("cache_read_input_tokens",),
        "output_visible": ("output_tokens", "reasoning_tokens"),
        "reasoning": ("thinking_tokens",),
        "billed_input_total": (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ),
        "billed_output_total": ("output_tokens",),
        "unallocated": ("cache_creation_input_tokens",),
    },
    "openai": {
        "input_uncached": ("prompt_tokens", "prompt_tokens_details.cached_tokens"),
        "input_cached": ("prompt_tokens_details.cached_tokens",),
        "output_visible": (
            "completion_tokens",
            "completion_tokens_details.reasoning_tokens",
        ),
        "reasoning": ("completion_tokens_details.reasoning_tokens",),
        "billed_input_total": ("prompt_tokens",),
        "billed_output_total": ("completion_tokens",),
        "unallocated": (),
    },
    "codex": {
        "input_uncached": ("input_tokens", "cached_input_tokens"),
        "input_cached": ("cached_input_tokens",),
        "output_visible": ("output_tokens", "reasoning_output_tokens"),
        "reasoning": ("reasoning_output_tokens",),
        "billed_input_total": ("input_tokens",),
        "billed_output_total": ("output_tokens",),
        "unallocated": ("cache_write_input_tokens",),
    },
    "agy": {
        "input_uncached": ("input_tokens", "cache_read_tokens"),
        "input_cached": ("cache_read_tokens",),
        "output_visible": ("output_tokens", "thinking_tokens"),
        "reasoning": ("thinking_tokens",),
        "billed_input_total": ("input_tokens",),
        "billed_output_total": ("output_tokens",),
        "unallocated": (),
    },
    "human": {name: () for name in USAGE_FIELDS},
}
_SEMANTICS = {
    "input_uncached": {
        "relation": "partition_component",
        "partition": "input",
        "aggregation": "sum_usage_deltas",
    },
    "input_cached": {
        "relation": "subset",
        "subset_of": "billed_input_total",
        "partition": "input",
        "aggregation": "sum_usage_deltas",
    },
    "output_visible": {
        "relation": "partition_component",
        "partition": "output",
        "aggregation": "sum_usage_deltas",
    },
    "reasoning": {
        "relation": "subset",
        "subset_of": "billed_output_total",
        "partition": "output",
        "aggregation": "sum_usage_deltas",
    },
    "billed_input_total": {
        "relation": "total",
        "aggregation": "sum_usage_deltas",
    },
    "billed_output_total": {
        "relation": "total",
        "aggregation": "sum_usage_deltas",
    },
    "unallocated": {
        "relation": "residual",
        "aggregation": "sum_usage_deltas",
    },
}
_OPERATIONS = {
    "input_uncached": {
        "anthropic": "copy",
        "openai": "subtract_subset",
        "codex": "subtract_subset",
        "agy": "subtract_subset",
    },
    "input_cached": {name: "copy" for name in _PROVIDERS},
    "output_visible": {
        "openai": "subtract_subset",
        "codex": "subtract_subset",
        "agy": "subtract_subset",
    },
    "reasoning": {name: "copy" for name in _PROVIDERS},
    "billed_input_total": {
        "anthropic": "sum_reported_components",
        "openai": "copy_total",
        "codex": "copy_total",
        "agy": "copy_total",
    },
    "billed_output_total": {
        name: "copy_total" for name in _PROVIDERS
    },
    "unallocated": {name: "copy_residual" for name in _PROVIDERS},
}
_UNKNOWN_REASONS = {
    ("anthropic", "output_visible"): "provider-does-not-separate-reasoning",
    ("anthropic", "reasoning"): "provider-does-not-report-reasoning",
    ("human", "*"): "human-run-has-no-token-measurement",
}


class UsageEvidenceError(ValueError):
    """Usage evidence wire contract 違反。"""


@dataclass(frozen=True)
class _Source:
    source_id: str
    source_schema: str
    adapter_version: str


def map_usage_with_provenance(
    provider: str,
    usage: dict,
    *,
    annotations: Mapping[str, Mapping[str, object]] | None = None,
    adapter_version: str = "unknown",
    quantity_kind: str = "usage_delta",
    turn: int = 0,
    role: str = "author",
    tool_calls: int = 0,
    wall_clock_ms: int = 0,
    prompt_bytes: int = 0,
    generated_bytes: int = 0,
    pricing_snapshot_ref: str = "",
) -> tuple[LedgerEntry, dict[str, object]]:
    """映射 usage 並產生不含 provider 原始值的正規化 provenance 紀錄。"""
    entry = map_usage(
        provider,
        usage,
        turn=turn,
        role=role,
        tool_calls=tool_calls,
        wall_clock_ms=wall_clock_ms,
        prompt_bytes=prompt_bytes,
        generated_bytes=generated_bytes,
        pricing_snapshot_ref=pricing_snapshot_ref,
    )
    if provider not in _PROVIDERS:
        raise UsageEvidenceError("unknown_provider")
    annotation_map = _validate_annotations(annotations or {})
    version = adapter_version.strip() if isinstance(adapter_version, str) else ""
    if not version:
        version = "unknown"
    source = _Source(provider, _PROVIDERS[provider], version)

    normalized = {
        "input_uncached": entry.input_uncached,
        "input_cached": entry.input_cached,
        "output_visible": entry.output_visible,
        "reasoning": entry.reasoning,
        "billed_input_total": entry.billed_input_total,
        "billed_output_total": entry.billed_output_total,
        "unallocated": entry.unallocated,
    }
    fields: dict[str, dict[str, object]] = {}
    gaps: list[dict[str, str]] = []
    for name in USAGE_FIELDS:
        raw_fields = _RAW_FIELDS[provider][name]
        value = normalized[name]
        reason = _unknown_reason(provider, name, raw_fields, usage)
        relevant_annotations = [
            annotation_map[raw_name]
            for raw_name in raw_fields
            if raw_name in annotation_map
        ]
        estimated = next(
            (annotation for annotation in relevant_annotations if annotation["state"] == "estimated"),
            None,
        )
        if value is None:
            state = "unknown"
            method = "executor_usage"
            field_reason = reason
            gaps.append({"scope": name, "reason": field_reason})
        elif estimated is not None:
            state = "estimated"
            method = "estimate"
            field_reason = str(estimated["reason"])
        else:
            state = "observed"
            method = "executor_usage"
            field_reason = ""

        operation = _OPERATIONS[name].get(provider, "unavailable")
        if estimated is not None and operation in ("copy", "copy_total", "copy_residual"):
            operation = str(estimated["calculation"])
        field: dict[str, object] = {
            "state": state,
            "unit_ref": {
                "state": "known",
                "value": {"unit_id": "token", "version": "1"},
            },
            "method": method,
            "source": {
                "source_id": source.source_id,
                "source_schema": source.source_schema,
                "adapter_version": source.adapter_version,
            },
            "calculation": {
                "operation": operation,
                "source_fields": list(raw_fields),
            },
            "semantics": dict(_SEMANTICS[name]),
        }
        if state == "unknown":
            field["reason"] = field_reason
        else:
            field["value"] = value
            if state == "estimated":
                field["reason"] = field_reason
        fields[name] = field

    known_count = sum(field["state"] != "unknown" for field in fields.values())
    coverage_state = (
        "unknown" if known_count == 0 else "complete" if known_count == len(fields) else "partial"
    )
    evidence: dict[str, object] = {
        "schema_version": USAGE_EVIDENCE_SCHEMA_VERSION,
        "turn": turn,
        "role": role,
        "quantity_kind": quantity_kind,
        "coverage": {"state": coverage_state, "gaps": gaps},
        "fields": fields,
    }
    validate_usage_evidence(evidence, require_seq=False)
    return entry, evidence


def legacy_usage_evidence(
    *, turn: int, role: str, adapter_version: str = "unknown"
) -> dict[str, object]:
    """舊 ledger 無來源資訊時，以 legacy/unknown 表示，絕不升格為 observed。"""
    source = {
        "source_id": "legacy",
        "source_schema": "legacy-usage/unknown",
        "adapter_version": adapter_version or "unknown",
    }
    fields: dict[str, dict[str, object]] = {}
    gaps: list[dict[str, str]] = []
    for name in USAGE_FIELDS:
        reason = "legacy-provenance-unavailable"
        gaps.append({"scope": name, "reason": reason})
        fields[name] = {
            "state": "unknown",
            "reason": reason,
            "unit_ref": {
                "state": "known",
                "value": {"unit_id": "token", "version": "1"},
            },
            "method": "legacy",
            "source": dict(source),
            "calculation": {"operation": "unavailable", "source_fields": []},
            "semantics": dict(_SEMANTICS[name]),
        }
    evidence: dict[str, object] = {
        "schema_version": USAGE_EVIDENCE_SCHEMA_VERSION,
        "turn": turn,
        "role": role,
        "quantity_kind": "usage_delta",
        "coverage": {"state": "unknown", "gaps": gaps},
        "fields": fields,
    }
    validate_usage_evidence(evidence, require_seq=False)
    return evidence


def validate_usage_evidence(
    record: object, *, require_seq: bool = True
) -> None:
    """驗證 append-only usage evidence 的固定 schema 與三態互斥欄位。"""
    if not isinstance(record, dict):
        raise UsageEvidenceError("record_not_mapping")
    required = {
        "schema_version",
        "turn",
        "role",
        "quantity_kind",
        "coverage",
        "fields",
    }
    allowed = required | {"seq"}
    if set(record) - allowed or not required.issubset(record):
        raise UsageEvidenceError("record_keys_invalid")
    if record["schema_version"] != USAGE_EVIDENCE_SCHEMA_VERSION:
        raise UsageEvidenceError("schema_version_unsupported")
    if require_seq and (type(record.get("seq")) is not int or record["seq"] < 1):
        raise UsageEvidenceError("seq_invalid")
    if not require_seq and "seq" in record:
        raise UsageEvidenceError("caller_seq_forbidden")
    if type(record["turn"]) is not int or record["turn"] < 0:
        raise UsageEvidenceError("turn_invalid")
    if record["role"] not in ("author", "reviewer"):
        raise UsageEvidenceError("role_invalid")
    if record["quantity_kind"] not in ("usage_delta", "usage_total"):
        raise UsageEvidenceError("quantity_kind_invalid")
    _validate_coverage(record["coverage"])
    fields = record["fields"]
    if not isinstance(fields, dict) or set(fields) != set(USAGE_FIELDS):
        raise UsageEvidenceError("fields_invalid")
    for name in USAGE_FIELDS:
        _validate_field(name, fields[name])


def _validate_annotations(
    annotations: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, str]]:
    copied: dict[str, dict[str, str]] = {}
    for name, annotation in annotations.items():
        if not isinstance(name, str) or not isinstance(annotation, Mapping):
            raise UsageEvidenceError("annotation_invalid")
        if annotation.get("state") != "estimated" or annotation.get("method") != "estimate":
            raise UsageEvidenceError("annotation_state_invalid")
        calculation = annotation.get("calculation")
        reason = annotation.get("reason")
        if not isinstance(calculation, str) or not calculation:
            raise UsageEvidenceError("annotation_calculation_invalid")
        if not isinstance(reason, str) or not reason:
            raise UsageEvidenceError("annotation_reason_invalid")
        copied[name] = {
            "state": "estimated",
            "method": "estimate",
            "calculation": calculation,
            "reason": reason,
        }
    return copied


def _unknown_reason(provider: str, name: str, raw_fields: tuple[str, ...], usage: dict) -> str:
    explicit = _UNKNOWN_REASONS.get((provider, name))
    if explicit:
        return explicit
    if (provider, "*") in _UNKNOWN_REASONS:
        return _UNKNOWN_REASONS[(provider, "*")]
    if not raw_fields:
        return "provider-does-not-report-field"
    return "provider-field-not-reported"


def _validate_coverage(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"state", "gaps"}:
        raise UsageEvidenceError("coverage_invalid")
    if value["state"] not in ("complete", "partial", "unknown"):
        raise UsageEvidenceError("coverage_state_invalid")
    gaps = value["gaps"]
    if not isinstance(gaps, list):
        raise UsageEvidenceError("coverage_gaps_invalid")
    for gap in gaps:
        if (
            not isinstance(gap, dict)
            or set(gap) != {"scope", "reason"}
            or not isinstance(gap["scope"], str)
            or not isinstance(gap["reason"], str)
        ):
            raise UsageEvidenceError("coverage_gap_invalid")


def _validate_field(name: str, value: object) -> None:
    if not isinstance(value, dict):
        raise UsageEvidenceError("field_not_mapping")
    state = value.get("state")
    expected = {
        "state",
        "unit_ref",
        "method",
        "source",
        "calculation",
        "semantics",
        "reason" if state == "unknown" else "value",
    }
    if state == "estimated":
        expected.add("reason")
    if set(value) != expected:
        raise UsageEvidenceError("field_keys_invalid")
    if state not in ("observed", "estimated", "unknown"):
        raise UsageEvidenceError("field_state_invalid")
    if state != "unknown":
        if type(value.get("value")) is not int or value["value"] < 0:
            raise UsageEvidenceError("field_value_invalid")
    elif not isinstance(value.get("reason"), str) or not value["reason"]:
        raise UsageEvidenceError("field_reason_invalid")
    if value.get("method") not in _METHODS:
        raise UsageEvidenceError("field_method_invalid")
    if state == "estimated" and value["method"] != "estimate":
        raise UsageEvidenceError("estimate_method_invalid")
    if state == "unknown" and value["method"] == "estimate":
        raise UsageEvidenceError("unknown_method_invalid")
    unit_ref = value.get("unit_ref")
    if unit_ref != {
        "state": "known",
        "value": {"unit_id": "token", "version": "1"},
    }:
        raise UsageEvidenceError("unit_ref_invalid")
    source = value.get("source")
    if not isinstance(source, dict) or set(source) != {
        "source_id",
        "source_schema",
        "adapter_version",
    }:
        raise UsageEvidenceError("source_invalid")
    if any(not isinstance(source[key], str) or not source[key] for key in source):
        raise UsageEvidenceError("source_value_invalid")
    calculation = value.get("calculation")
    if not isinstance(calculation, dict) or set(calculation) != {
        "operation",
        "source_fields",
    }:
        raise UsageEvidenceError("calculation_invalid")
    if not isinstance(calculation["operation"], str) or not calculation["operation"]:
        raise UsageEvidenceError("calculation_operation_invalid")
    if not isinstance(calculation["source_fields"], list) or not all(
        isinstance(item, str) for item in calculation["source_fields"]
    ):
        raise UsageEvidenceError("calculation_sources_invalid")
    semantics = value.get("semantics")
    if not isinstance(semantics, dict) or semantics != _SEMANTICS[name]:
        raise UsageEvidenceError("semantics_invalid")
