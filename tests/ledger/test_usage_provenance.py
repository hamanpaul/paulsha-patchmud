"""PR2 usage contract: normalized values only, with field-level provenance."""

from __future__ import annotations

import json

import pytest

from patchmud.ledger.tokens import LedgerEntry
from patchmud.usage_provenance import map_usage_with_provenance


def _source_fields(record: dict) -> dict:
    return record["fields"]


class TestUsageProvenanceMapping:
    def test_observed_subset_fields_keep_units_sources_and_semantics(self):
        entry, evidence = map_usage_with_provenance(
            "openai",
            {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "prompt_tokens_details": {"cached_tokens": 20},
                "completion_tokens_details": {"reasoning_tokens": 30},
                "provider_private_marker": "must-not-be-persisted",
            },
            adapter_version="openai-compat/1",
            turn=4,
            role="author",
        )

        assert isinstance(entry, LedgerEntry)
        assert entry.input_uncached == 100
        assert entry.input_cached == 20
        assert entry.output_visible == 50
        assert entry.reasoning == 30
        fields = _source_fields(evidence)
        cached = fields["input_cached"]
        assert cached["state"] == "observed"
        assert cached["value"] == 20
        assert cached["method"] == "executor_usage"
        assert cached["unit_ref"] == {
            "state": "known",
            "value": {"unit_id": "token", "version": "1"},
        }
        assert cached["source"]["source_id"] == "openai"
        assert cached["source"]["adapter_version"] == "openai-compat/1"
        assert cached["semantics"]["relation"] == "subset"
        assert cached["semantics"]["subset_of"] == "billed_input_total"
        assert evidence["quantity_kind"] == "usage_delta"
        assert "provider_private_marker" not in json.dumps(evidence)

    def test_missing_fields_are_unknown_without_numeric_zero(self):
        entry, evidence = map_usage_with_provenance(
            "openai",
            {"prompt_tokens": 100},
            adapter_version="openai-compat/1",
            turn=1,
        )

        assert entry.billed_input_total == 100
        assert entry.billed_output_total is None
        fields = _source_fields(evidence)
        assert fields["billed_input_total"]["value"] == 100
        for field_name in (
            "input_cached",
            "output_visible",
            "reasoning",
            "billed_output_total",
        ):
            field = fields[field_name]
            assert field["state"] == "unknown"
            assert "value" not in field
            assert field["reason"]
        assert evidence["coverage"]["state"] == "partial"

    def test_estimated_source_values_remain_estimated_after_normalization(self):
        entry, evidence = map_usage_with_provenance(
            "anthropic",
            {"input_tokens": 24, "output_tokens": 8},
            annotations={
                "input_tokens": {
                    "state": "estimated",
                    "method": "estimate",
                    "calculation": "prompt_characters_div_4",
                    "reason": "provider-usage-missing",
                },
                "output_tokens": {
                    "state": "estimated",
                    "method": "estimate",
                    "calculation": "response_characters_div_4",
                    "reason": "provider-usage-missing",
                },
            },
            adapter_version="claude-cli/1",
            turn=2,
        )

        assert entry.billed_input_total is None
        fields = _source_fields(evidence)
        assert fields["input_uncached"]["state"] == "estimated"
        assert fields["input_uncached"]["value"] == 24
        assert fields["input_uncached"]["method"] == "estimate"
        assert fields["output_visible"]["state"] == "unknown"
        assert fields["billed_output_total"]["state"] == "estimated"
        assert evidence["coverage"]["state"] == "partial"


class TestUsageEvidenceArchive:
    def test_run_store_appends_validated_normalized_evidence_only(self, tmp_path):
        from patchmud.store.run_store import RunStore
        from patchmud.store.schemas import StoreError

        encounter_dir = tmp_path / "encounter"
        encounter_dir.mkdir()
        store = RunStore.create(
            {
                "run_id": "usage-run",
                "frozen_sha": "frozen",
                "pricing_hash": "NA",
                "harness_prompt_version": "test",
                "schedule_ref": "NA",
                "encounter_dir": str(encounter_dir),
            },
            tmp_path / "runs",
        )
        _, evidence = map_usage_with_provenance(
            "openai",
            {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "prompt_tokens_details": {"cached_tokens": 0},
                "completion_tokens_details": {"reasoning_tokens": 0},
                "secret_provider_payload": "must-not-be-copied",
            },
            adapter_version="fake/1",
            turn=1,
        )

        store.append_usage_evidence(evidence)
        loaded = store.load_usage_evidence()
        assert len(loaded) == 1
        assert loaded[0]["seq"] == 1
        assert loaded[0]["fields"]["input_cached"]["value"] == 0
        encoded = (store.run_dir / "usage_evidence.jsonl").read_text(encoding="utf-8")
        assert "secret_provider_payload" not in encoded
        assert "must-not-be-copied" not in encoded

        with pytest.raises(StoreError, match="caller_seq_forbidden"):
            store.append_usage_evidence({**evidence, "seq": 7})
        with pytest.raises(StoreError, match="schema_version_unsupported"):
            store.append_usage_evidence({**evidence, "schema_version": 2})
