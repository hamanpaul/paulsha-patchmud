"""Run/report 身分欄位：只輸出內容 digest，不洩漏 deck 檔案內容或本機路徑。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

__all__ = ["build_run_report_metadata"]

_UNMEASURED_ROLE_REASONS = {
    "planner": {"state": "unknown", "reason": "role-not-assessed"},
    "reviewer": {"state": "unknown", "reason": "role-not-assessed"},
}


def build_run_report_metadata(
    encounter_dir: Path,
    *,
    role: str = "builder",
    benchmark_type: str = "issue-resolution",
) -> dict[str, object]:
    """在 run 建立時 pin role、coverage、deck 與 evaluator revision。"""
    encounter_dir = Path(encounter_dir)
    deck_id, deck_digest, encounters = _deck_identity(encounter_dir)
    return {
        "role": role,
        "benchmark_type": benchmark_type,
        "measured_dimensions": ["clear", "power", "protocol"],
        "unmeasured_dimensions": {
            name: dict(value) for name, value in _UNMEASURED_ROLE_REASONS.items()
        },
        "deck_id": deck_id,
        "deck_digest": deck_digest,
        "deck_encounters": list(encounters),
        "evaluator_revision": _evaluator_revision(),
    }


def _deck_identity(encounter_dir: Path) -> tuple[str, str, tuple[str, ...]]:
    if not (encounter_dir / "card.yaml").is_file():
        raise ValueError("encounter 缺 card.yaml，無法固定 report coverage")
    deck_root = encounter_dir.parent
    candidates = sorted(
        child
        for child in deck_root.iterdir()
        if child.is_dir() and (child / "card.yaml").is_file()
    )
    if not candidates:
        candidates = [encounter_dir]
    entries = [
        {"encounter": path.name, "digest": _tree_digest(path)} for path in candidates
    ]
    if encounter_dir.name not in {entry["encounter"] for entry in entries}:
        entries.append(
            {"encounter": encounter_dir.name, "digest": _tree_digest(encounter_dir)}
        )
        entries.sort(key=lambda entry: entry["encounter"])
    deck_bytes = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(deck_bytes).hexdigest()
    return deck_root.name, digest, tuple(entry["encounter"] for entry in entries)


def _evaluator_revision() -> str:
    package_root = Path(__file__).resolve().parent
    files = sorted(package_root.rglob("*.py"))
    digest = _files_digest(package_root, files)
    return "sha256:" + digest


def _tree_digest(root: Path) -> str:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    return _files_digest(root, files)


def _files_digest(root: Path, files: list[Path]) -> str:
    manifest = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in files
    ]
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
