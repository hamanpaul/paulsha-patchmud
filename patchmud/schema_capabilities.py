"""PatchMUD 可供自動化消費的 schema 版本能力宣告。"""

from __future__ import annotations

from patchmud.execution_profile import SCHEMA_VERSION as EXECUTION_PROFILE_SCHEMA_VERSION
from patchmud.report_schema import (
    REPORT_READ_SCHEMA_VERSIONS,
    REPORT_WRITE_SCHEMA_VERSIONS,
)
from patchmud.store.schemas import RESULT_SCHEMA_VERSION, RUN_SCHEMA_VERSION
from patchmud.usage_provenance import USAGE_EVIDENCE_SCHEMA_VERSION

__all__ = ["schema_capabilities"]


def schema_capabilities(producer_version: str) -> dict:
    """回傳已實作讀取與輸出的 wire schema 版本，不代表 qualification。"""
    return {
        "schema_version": 1,
        "producer": {"name": "paulsha-patchmud", "version": producer_version},
        "schemas": {
            "execution_profile": {
                "read_versions": [EXECUTION_PROFILE_SCHEMA_VERSION],
                "write_versions": [EXECUTION_PROFILE_SCHEMA_VERSION],
            },
            "usage_provenance": {
                "read_versions": [USAGE_EVIDENCE_SCHEMA_VERSION],
                "write_versions": [USAGE_EVIDENCE_SCHEMA_VERSION],
            },
            "report": {
                "read_versions": list(REPORT_READ_SCHEMA_VERSIONS),
                "write_versions": list(REPORT_WRITE_SCHEMA_VERSIONS),
            },
        },
        "legacy_archive": {
            "run_yaml_read_versions": [RUN_SCHEMA_VERSION],
            "result_yaml_read_versions": [RESULT_SCHEMA_VERSION],
            "missing_usage_evidence": "legacy_unknown",
            "missing_profile": "legacy_unknown",
        },
        "fixtures": {
            "manifest": "fixtures/golden/manifest.json",
            "manifest_version": 1,
        },
    }
