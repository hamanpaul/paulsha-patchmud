"""公開 golden fixture manifest 的產生與 fail-closed 驗證。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from patchmud.execution_profile import SCHEMA_VERSION as EXECUTION_PROFILE_SCHEMA_VERSION
from patchmud.report_schema import REPORT_SCHEMA_VERSION
from patchmud.store.schemas import (
    EVENT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    RUN_SCHEMA_VERSION,
)
from patchmud.usage_provenance import USAGE_EVIDENCE_SCHEMA_VERSION

__all__ = [
    "FIXTURE_ROOT",
    "FixtureManifestError",
    "build_manifest",
    "load_manifest",
    "validate_manifest",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "golden"
MANIFEST_NAME = "manifest.json"
_SOURCE_REVISION_RE = re.compile(r"[0-9a-f]{40}\Z")
SCHEMA_VERSIONS = {
    "execution_profile": EXECUTION_PROFILE_SCHEMA_VERSION,
    "usage_provenance": USAGE_EVIDENCE_SCHEMA_VERSION,
    "report": REPORT_SCHEMA_VERSION,
    "run": RUN_SCHEMA_VERSION,
    "result": RESULT_SCHEMA_VERSION,
    "event": EVENT_SCHEMA_VERSION,
    "ledger": 1,
}

# 固定白名單讓新增公開資料必須同時更新 manifest，而非被 glob 靜默收錄。
_FIXTURES = (
    ("execution-profile-v1/positive.json", "execution_profile", "positive"),
    ("execution-profile-v1/negative.json", "execution_profile", "negative"),
    ("usage-provenance-v1/positive.json", "usage_provenance", "positive"),
    ("usage-provenance-v1/negative.json", "usage_provenance", "negative"),
    ("report-v2/positive.json", "report", "positive"),
    ("report-v2/negative.json", "report", "negative"),
    ("legacy-v1/report.json", "report", "legacy_positive"),
    ("legacy-v1/run/run.yaml", "run", "legacy_positive"),
    ("legacy-v1/run/result.yaml", "result", "legacy_positive"),
    ("legacy-v1/run/events.jsonl", "event", "legacy_positive"),
    ("legacy-v1/run/ledger.jsonl", "ledger", "legacy_positive"),
    ("legacy-v1/negative/unknown-report.json", "report", "negative"),
    ("legacy-v1/negative/unknown-result.yaml", "result", "negative"),
)


class FixtureManifestError(ValueError):
    """manifest 與受版本控制的 fixture 集合不一致。"""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(source_revision: str, root: Path = FIXTURE_ROOT) -> dict:
    """以明確來源 revision 與 fixture 白名單產生 manifest 資料。"""
    if not isinstance(source_revision, str) or not _SOURCE_REVISION_RE.fullmatch(
        source_revision
    ):
        raise FixtureManifestError("source_revision must be a 40-character git SHA")
    root = Path(root)
    fixtures = []
    for relative_path, contract, case in _FIXTURES:
        path = root / relative_path
        if path.is_symlink() or not path.is_file():
            raise FixtureManifestError(f"fixture missing or not a regular file: {relative_path}")
        fixtures.append(
            {
                "path": relative_path,
                "contract": contract,
                "case": case,
                "sha256": _sha256(path),
            }
        )
    return {
        "manifest_version": 1,
        "source_revision": source_revision,
        "schema_versions": dict(SCHEMA_VERSIONS),
        "fixtures": fixtures,
    }


def load_manifest(root: Path = FIXTURE_ROOT) -> dict:
    path = Path(root) / MANIFEST_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureManifestError(f"cannot read fixture manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise FixtureManifestError("fixture manifest must be a JSON object")
    return value


def validate_manifest(root: Path = FIXTURE_ROOT) -> list[str]:
    """確認 manifest 版本、固定檔案清單與每個 fixture 的 SHA-256。"""
    root = Path(root)
    manifest = load_manifest(root)
    source_revision = manifest.get("source_revision")
    expected = build_manifest(source_revision, root)
    listed = manifest.get("fixtures")
    if not isinstance(listed, list):
        raise FixtureManifestError("fixtures must be an array")
    listed_by_path = {
        item.get("path"): item for item in listed if isinstance(item, dict)
    }
    expected_by_path = {item["path"]: item for item in expected["fixtures"]}
    for relative_path, item in expected_by_path.items():
        stored = listed_by_path.get(relative_path)
        if stored is None:
            raise FixtureManifestError(f"manifest missing fixture: {relative_path}")
        if stored.get("sha256") != item["sha256"]:
            raise FixtureManifestError(f"digest mismatch: {relative_path}")
        if stored != item:
            raise FixtureManifestError(f"manifest entry mismatch: {relative_path}")
    if set(listed_by_path) != set(expected_by_path) or len(listed) != len(expected_by_path):
        raise FixtureManifestError("manifest fixture list does not match the fixture allowlist")
    if manifest != expected:
        raise FixtureManifestError("manifest metadata or schema versions do not match")

    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != MANIFEST_NAME
    }
    if actual_files != set(expected_by_path):
        raise FixtureManifestError("fixture directory contains unmanifested or missing files")
    return []


def _current_revision() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise FixtureManifestError(f"cannot resolve source revision: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m patchmud.fixture_manifest",
        description="產生或驗證公開 golden fixture manifest。",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="依目前 HEAD 重建 manifest")
    action.add_argument("--check", action="store_true", help="驗證檔案清單與 SHA-256")
    parser.add_argument("--source-revision", help="--write 時指定 40 位 Git SHA")
    ns = parser.parse_args(argv)
    try:
        if ns.write:
            revision = ns.source_revision or _current_revision()
            manifest = build_manifest(revision)
            target = FIXTURE_ROOT / MANIFEST_NAME
            target.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(f"wrote {target.relative_to(REPO_ROOT)} ({len(manifest['fixtures'])} fixtures)")
        else:
            validate_manifest()
            print(f"golden fixture manifest valid ({len(_FIXTURES)} fixtures)")
    except FixtureManifestError as exc:
        print(f"fixture manifest error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
