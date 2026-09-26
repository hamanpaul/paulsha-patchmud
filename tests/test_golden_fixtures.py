"""#37 PR3：公開 golden fixtures、manifest、migration 與 CLI 能力契約。"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from patchmud import cli
from patchmud.execution_profile import (
    ExecutionProfileError,
    parse_descriptor,
    parse_profile,
)
from patchmud.report_schema import ReportSchemaError, validate_report_v2
from patchmud.store.run_store import RunStore
from patchmud.store.schemas import StoreError
from patchmud.usage_provenance import UsageEvidenceError, validate_usage_evidence

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_ROOT = REPO_ROOT / "fixtures" / "golden"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_is_generated_from_pinned_schema_revision_and_validates_all_digests():
    from patchmud.fixture_manifest import build_manifest, load_manifest, validate_manifest

    manifest = load_manifest(GOLDEN_ROOT)
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["source_revision"])
    assert manifest == build_manifest(manifest["source_revision"], GOLDEN_ROOT)
    assert validate_manifest(GOLDEN_ROOT) == []
    assert manifest["schema_versions"] == {
        "execution_profile": 1,
        "usage_provenance": 1,
        "report": 2,
        "run": 1,
        "result": 1,
        "event": 1,
        "ledger": 1,
    }


def test_manifest_rejects_fixture_edits_until_digest_is_regenerated(tmp_path: Path):
    from patchmud.fixture_manifest import FixtureManifestError, validate_manifest

    copy_root = tmp_path / "golden"
    shutil.copytree(GOLDEN_ROOT, copy_root)
    fixture = copy_root / "usage-provenance-v1" / "positive.json"
    fixture.write_bytes(fixture.read_bytes() + b" ")
    with pytest.raises(FixtureManifestError, match="digest mismatch"):
        validate_manifest(copy_root)


def test_execution_profile_positive_and_negative_golden_fixtures():
    positive = _json(GOLDEN_ROOT / "execution-profile-v1" / "positive.json")
    descriptor = parse_descriptor(positive["descriptor"])
    for plane in ("requested", "resolved", "observed"):
        assert parse_profile(positive[plane], descriptor).plane == plane
    assert positive["profile_id"] == positive["resolved_key"]

    negative = _json(GOLDEN_ROOT / "execution-profile-v1" / "negative.json")
    with pytest.raises(ExecutionProfileError):
        parse_profile(negative["profile"], parse_descriptor(negative["descriptor"]))


def test_usage_provenance_positive_and_negative_golden_fixtures():
    positive = _json(GOLDEN_ROOT / "usage-provenance-v1" / "positive.json")
    validate_usage_evidence(positive, require_seq=False)
    assert positive["coverage"]["state"] == "partial"
    assert positive["fields"]["billed_input_total"]["state"] == "observed"
    assert positive["fields"]["billed_output_total"]["state"] == "unknown"

    negative = _json(GOLDEN_ROOT / "usage-provenance-v1" / "negative.json")
    with pytest.raises(UsageEvidenceError):
        validate_usage_evidence(negative, require_seq=False)


def test_report_v2_positive_and_negative_golden_fixtures():
    from patchmud.report_schema import read_report_document

    positive = _json(GOLDEN_ROOT / "report-v2" / "positive.json")
    validate_report_v2(positive)
    assert read_report_document(positive)["schema_version"] == 2
    row = positive["runs"][0]
    assert row["profile_id"].startswith("epk:v1:resolved:")
    assert row["deck_coverage"]["complete"] is False
    assert row["usage_provenance"]["schema_version"] == 1

    negative = _json(GOLDEN_ROOT / "report-v2" / "negative.json")
    with pytest.raises(ReportSchemaError):
        read_report_document(negative)


def test_legacy_v1_run_result_and_report_are_read_without_promoting_unknowns():
    from patchmud.cli import _load_report_run, _load_watch_result
    from patchmud.report_schema import read_report_document

    legacy_dir = GOLDEN_ROOT / "legacy-v1" / "run"
    before = {
        path.name: path.read_bytes()
        for path in legacy_dir.iterdir()
        if path.is_file()
    }
    store = RunStore.open(legacy_dir)
    assert store.load_events()[0]["schema_version"] == 1
    assert not store.load_usage_evidence()
    assert _load_watch_result(legacy_dir)["schema_version"] == 1

    legacy_report = _json(GOLDEN_ROOT / "legacy-v1" / "report.json")
    assert read_report_document(legacy_report)["schema_version"] == 1
    card_path = REPO_ROOT / "tests" / "fixtures" / "mini_encounter" / "card.yaml"
    row = _load_report_run(legacy_dir, None, card_path=card_path)
    assert row.profile_id == "legacy/unknown"
    assert row.usage_provenance["source"]["source_id"] == "legacy"
    assert all(
        field["state"] == "unknown" and "value" not in field
        for field in row.usage_provenance["fields"].values()
    )
    assert all((legacy_dir / name).read_bytes() == content for name, content in before.items())


def test_legacy_unknown_schema_fixture_fails_closed():
    from patchmud.report_schema import read_report_document
    import tempfile

    path = GOLDEN_ROOT / "legacy-v1" / "negative" / "unknown-report.json"
    with pytest.raises(ReportSchemaError, match="schema_version"):
        read_report_document(_json(path))

    record = yaml.safe_load(
        (GOLDEN_ROOT / "legacy-v1" / "negative" / "unknown-result.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert record["schema_version"] == 2
    # 使用 read-only fixture 的 schema 判斷；寫入暫存副本以走正式 result reader。
    with tempfile.TemporaryDirectory() as temporary:
        tmp = Path(temporary)
        (tmp / "result.yaml").write_text(
            (path.parent / "unknown-result.yaml").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        with pytest.raises(StoreError, match="schema_version"):
            cli._load_watch_result(tmp)


def test_public_golden_fixtures_contain_no_hidden_bytes_credentials_or_personal_paths():
    files = sorted(path for path in GOLDEN_ROOT.rglob("*") if path.is_file())
    fixture_blobs = {path: path.read_bytes() for path in files}
    hidden_roots = [
        hidden
        for base in (REPO_ROOT / "decks", REPO_ROOT / "tests" / "fixtures")
        if base.exists()
        for hidden in base.rglob("hidden")
        if hidden.is_dir()
    ]
    hidden_bytes = [
        path.read_bytes()
        for hidden_root in hidden_roots
        for path in hidden_root.rglob("*")
        if path.is_file() and path.stat().st_size >= 8
    ]
    forbidden_patterns = (
        re.compile(rb"(?i)(?:sk-ant-api|sk-proj-|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_)[A-Za-z0-9_-]*"),
        re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|authorization)\s*[:=]\s*[^\s\"']{8,}"),
        re.compile(rb"(?:/home/[^/\s\"']+|/Users/[^/\s\"']+|[A-Za-z]:\\Users\\[^\\\s]+)"),
        re.compile(rb"(?i)paul_chen"),
    )
    for path, blob in fixture_blobs.items():
        for secret in hidden_bytes:
            assert secret not in blob, f"hidden bytes leaked in {path.relative_to(GOLDEN_ROOT)}"
        for pattern in forbidden_patterns:
            assert pattern.search(blob) is None, f"unsafe public fixture: {path.relative_to(GOLDEN_ROOT)}"


def test_cli_schema_json_declares_supported_contract_versions(capsys):
    assert cli.main(["schema", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["schemas"]["execution_profile"] == {
        "read_versions": [1],
        "write_versions": [1],
    }
    assert payload["schemas"]["usage_provenance"] == {
        "read_versions": [1],
        "write_versions": [1],
    }
    assert payload["schemas"]["report"] == {
        "read_versions": [1, 2],
        "write_versions": [2],
    }
    assert payload["fixtures"]["manifest"] == "fixtures/golden/manifest.json"
