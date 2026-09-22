"""Packaged engineering-v1 benchmark cases.

The case data is intentionally kept outside Python source.  A case has a
public ``repo/`` tree and a private ``hidden/`` tree.  The latter contains
reference, partial, and wrong answer anchors and is loaded only into the
private case object consumed by the runner/judge.  :func:`public_case_snapshot`
is the single boundary used by public artifacts.

The loader resolves resources through ``importlib.resources`` so callers do
not need to run from the checkout root.  A wheel normally exposes the data as
ordinary package files.  The small materialization fallback also handles
zip-style importers while keeping the resulting fixture directory alive for
the run.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.resources as resources
import json
import shutil
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = [
    "CaseDataError",
    "load_case",
    "load_suite",
    "public_case_snapshot",
]

_PACKAGE_NAME = "patchmud.scoring"
_DEFAULT_SUITE = "engineering-v1"
_CATEGORIES = frozenset({"repair", "diagnosis", "scope", "testing", "recovery", "audit"})
_DIMENSIONS = ("fulfillment", "evidence", "constraints", "verification")
_DEPTH_LIMITS = {1: (8, 600), 2: (16, 1200), 3: (24, 1800)}
_IGNORED_TREE_PARTS = frozenset({".git", "__pycache__", ".pytest_cache"})
_MATERIALIZED: dict[str, Path] = {}
_MATERIALIZE_LOCK = threading.Lock()


class CaseDataError(ValueError):
    """Raised when packaged case data is missing or fails closed validation."""


def load_suite(name: str = _DEFAULT_SUITE) -> dict[str, Any]:
    """Load a complete suite, including private anchors for internal use.

    Every invocation recomputes content hashes from the public and hidden
    bytes.  This is deliberate: changing a hidden anchor must invalidate a
    baseline instead of silently reusing a stale suite object.
    """

    root = _suite_root(name)
    manifest = _read_json(root / "suite.json", f"{name}/suite.json")
    if manifest.get("id") != name:
        raise CaseDataError(f"suite id mismatch: {manifest.get('id')!r} != {name!r}")
    case_ids = manifest.get("case_ids")
    if not isinstance(case_ids, list) or not case_ids or any(not isinstance(v, str) for v in case_ids):
        raise CaseDataError("suite.json case_ids must be a non-empty string list")
    if len(set(case_ids)) != len(case_ids):
        raise CaseDataError("suite.json contains duplicate case ids")

    cases = [load_case(case_id, suite=name, _root=root) for case_id in case_ids]
    suite_payload = {
        "id": manifest["id"],
        "version": _require_string(manifest, "version", "suite"),
        "rubric_version": _require_string(manifest, "rubric_version", "suite"),
        "case_ids": case_ids,
        "case_hashes": [case["case_hash"] for case in cases],
    }
    suite_hash = _sha256(_canonical(suite_payload))
    declared = manifest.get("suite_hash")
    if declared is not None and declared != suite_hash:
        raise CaseDataError("suite hash mismatch; packaged case data changed")
    return {
        "id": manifest["id"],
        "version": suite_payload["version"],
        "rubric_version": suite_payload["rubric_version"],
        "suite_hash": suite_hash,
        "cases": cases,
    }


def load_case(
    case_id: str,
    suite: str = _DEFAULT_SUITE,
    *,
    _root: Path | None = None,
) -> dict[str, Any]:
    """Load one case by id and return an independent mutable dictionary."""

    if (
        not isinstance(case_id, str)
        or not case_id
        or case_id in {".", ".."}
        or "/" in case_id
        or "\\" in case_id
    ):
        raise CaseDataError(f"invalid case id: {case_id!r}")
    root = _root or _suite_root(suite)
    case_root = root / case_id
    if not case_root.is_dir():
        raise KeyError(f"unknown case {case_id!r} in suite {suite!r}")
    metadata = _read_json(case_root / "case.json", f"{suite}/{case_id}/case.json")
    if metadata.get("id") != case_id:
        raise CaseDataError(f"case id mismatch in {case_id}/case.json")
    case = _validate_metadata(metadata, case_id)

    repo = case_root / "repo"
    hidden = case_root / "hidden"
    if repo.is_symlink() or hidden.is_symlink() or not repo.is_dir() or not hidden.is_dir():
        raise CaseDataError(f"{case_id}: each case must contain repo/ and hidden/")
    public_files = _read_tree(repo, case_id, "repo")
    hidden_files = _read_tree(hidden, case_id, "hidden")
    anchors = _load_anchors(hidden, hidden_files, case_id)

    for rel in case["allowed_paths"]:
        _clean_relpath(rel, f"{case_id}/allowed_paths")
        if not any(token in rel for token in ("*", "?", "[")) and rel not in public_files:
            raise CaseDataError(f"{case_id}: allowed path is absent from public repo: {rel}")

    case_hash_payload = {
        "metadata": metadata | {"case_hash": None},
        "public_files": public_files,
        "hidden_files": {path: _bytes_digest(text.encode("utf-8")) for path, text in hidden_files.items()},
    }
    computed_hash = _sha256(_canonical(case_hash_payload))
    declared_hash = metadata.get("case_hash")
    if declared_hash is not None and declared_hash != computed_hash:
        raise CaseDataError(f"{case_id}: case hash mismatch; public or private bytes changed")

    case.update(
        {
            "fixture_dir": str(case_root.resolve()),
            "public_files": public_files,
            "anchors": anchors,
            "case_hash": computed_hash,
        }
    )
    return copy.deepcopy(case)


def public_case_snapshot(case: dict[str, Any]) -> dict[str, Any]:
    """Return the model/report-visible case object.

    The output is assembled from an allowlist rather than filtering unknown
    keys.  This makes adding a private field fail closed: it cannot leak into
    public artifacts by accident.
    """

    required = (
        "id",
        "category",
        "depth",
        "title",
        "prompt",
        "requirements",
        "allowed_paths",
        "max_turns",
        "wall_seconds",
        "rubric",
        "stages",
        "test_argv",
        "public_files",
        "case_hash",
    )
    missing = [key for key in required if key not in case]
    if missing:
        raise CaseDataError(f"case snapshot missing fields: {', '.join(missing)}")
    return copy.deepcopy({key: case[key] for key in required})


def _suite_root(name: str) -> Path:
    if name != _DEFAULT_SUITE:
        raise KeyError(f"unknown suite {name!r}")
    traversable = resources.files(_PACKAGE_NAME).joinpath("data", name)
    # Source checkouts and normal wheels use a real filesystem path.  Avoid
    # ``str(Traversable)`` for zip importers: it is not necessarily readable.
    try:
        candidate: Path | None = Path(traversable)  # type: ignore[arg-type]
    except TypeError:
        candidate = None
    if candidate is not None and candidate.is_dir():
        return candidate.resolve()
    with _MATERIALIZE_LOCK:
        cached = _MATERIALIZED.get(name)
        if cached is not None and cached.is_dir():
            return cached
        target = Path(tempfile.mkdtemp(prefix=f"patchmud-{name}-")) / name
        _copy_traversable(traversable, target)
        _MATERIALIZED[name] = target
        return target


def _copy_traversable(source: Any, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            _copy_traversable(child, destination)
        else:
            destination.write_bytes(child.read_bytes())


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaseDataError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CaseDataError(f"{label} must contain a JSON object")
    return value


def _read_tree(root: Path, case_id: str, tree_name: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if any(part in _IGNORED_TREE_PARTS for part in path.relative_to(root).parts):
            continue
        if path.is_symlink():
            raise CaseDataError(f"{case_id}: symlink in {tree_name}: {path}")
        if path.is_dir():
            continue
        if path.suffix == ".pyc":
            continue
        rel = path.relative_to(root).as_posix()
        _clean_relpath(rel, f"{case_id}/{tree_name}")
        try:
            result[rel] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CaseDataError(f"{case_id}: non-text {tree_name} file {rel}: {exc}") from exc
    return result


def _load_anchors(hidden: Path, hidden_files: dict[str, str], case_id: str) -> dict[str, Any]:
    raw = _read_json(hidden / "anchors.json", f"{case_id}/hidden/anchors.json")
    for key in ("reference", "partial", "wrong"):
        entry = raw.get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("final_report"), str):
            raise CaseDataError(f"{case_id}: anchor {key} needs a final_report string")
        entry = dict(entry)
        patch_file = entry.get("patch_file")
        if patch_file is not None:
            if not isinstance(patch_file, str) or not patch_file.startswith("hidden/"):
                raise CaseDataError(f"{case_id}: anchor {key} patch_file must be under hidden/")
            hidden_rel = patch_file.removeprefix("hidden/")
            if hidden_rel not in hidden_files:
                raise CaseDataError(f"{case_id}: missing anchor patch {patch_file}")
            entry["final_diff"] = hidden_files[hidden_rel]
        elif entry.get("final_diff") is not None and not isinstance(entry["final_diff"], str):
            raise CaseDataError(f"{case_id}: anchor {key} final_diff must be text")
        quality = entry.get("expected_quality")
        if not isinstance(quality, dict) or tuple(quality) != _DIMENSIONS:
            raise CaseDataError(f"{case_id}: anchor {key} expected_quality must cover all dimensions")
        if any(not isinstance(value, int) or value < 0 or value > 4 for value in quality.values()):
            raise CaseDataError(f"{case_id}: anchor {key} expected_quality values must be 0..4")
        raw[key] = entry
    if raw.get("kind") not in {"patch", "answer"}:
        raise CaseDataError(f"{case_id}: anchor kind must be patch or answer")
    if not isinstance(raw.get("outcome_class"), str) or not raw["outcome_class"]:
        raise CaseDataError(f"{case_id}: anchor outcome_class is required")
    quality_totals = {
        name: sum(raw[name]["expected_quality"].values()) for name in ("reference", "partial", "wrong")
    }
    if not quality_totals["reference"] > quality_totals["partial"] > quality_totals["wrong"]:
        raise CaseDataError(f"{case_id}: expected quality must order reference > partial > wrong")
    # anchors.json is private metadata; ensure its own path is not a usable
    # answer path accidentally selected by a case author.
    raw.pop("_comment", None)
    return raw


def _validate_metadata(metadata: dict[str, Any], case_id: str) -> dict[str, Any]:
    category = metadata.get("category")
    if category not in _CATEGORIES:
        raise CaseDataError(f"{case_id}: invalid category {category!r}")
    depth = metadata.get("depth")
    if depth not in _DEPTH_LIMITS:
        raise CaseDataError(f"{case_id}: depth must be 1, 2, or 3")
    if (metadata.get("max_turns"), metadata.get("wall_seconds")) != _DEPTH_LIMITS[depth]:
        raise CaseDataError(f"{case_id}: depth budget does not match pinned suite limits")
    for key in ("title", "prompt"):
        _require_string(metadata, key, case_id)
    for key in ("requirements", "allowed_paths"):
        value = metadata.get(key)
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
            raise CaseDataError(f"{case_id}: {key} must be a string list")
        if key == "requirements" and not value:
            raise CaseDataError(f"{case_id}: requirements must be non-empty")
        if key == "allowed_paths" and not value and category not in {"diagnosis", "audit"}:
            raise CaseDataError(f"{case_id}: allowed_paths cannot be empty for this category")
    rubric = metadata.get("rubric")
    if not isinstance(rubric, dict) or tuple(rubric) != _DIMENSIONS:
        raise CaseDataError(f"{case_id}: rubric dimensions must be {_DIMENSIONS}")
    for dimension in _DIMENSIONS:
        item = rubric[dimension]
        if not isinstance(item, dict) or not isinstance(item.get("instructions"), str):
            raise CaseDataError(f"{case_id}: rubric {dimension} instructions missing")
        criteria = item.get("criteria")
        if not isinstance(criteria, list) or len(criteria) != 5 or not all(isinstance(v, str) and v for v in criteria):
            raise CaseDataError(f"{case_id}: rubric {dimension} must have five criteria")
    stages = metadata.get("stages", [])
    if not isinstance(stages, list) or not all(isinstance(stage, dict) for stage in stages):
        raise CaseDataError(f"{case_id}: stages must be a list of objects")
    previous = 0
    for stage in stages:
        turn = stage.get("after_turn")
        if not isinstance(turn, int) or turn <= previous or turn >= metadata["max_turns"]:
            raise CaseDataError(f"{case_id}: stages must have increasing in-budget after_turn")
        if not isinstance(stage.get("message"), str) or not stage["message"].strip():
            raise CaseDataError(f"{case_id}: stage message missing")
        previous = turn
    argv = metadata.get("test_argv", ["python3", "-m", "pytest", "-q"])
    if not isinstance(argv, list) or not all(isinstance(v, str) and v for v in argv):
        raise CaseDataError(f"{case_id}: test_argv must be a string list")
    if not argv and category not in {"diagnosis", "audit"}:
        raise CaseDataError(f"{case_id}: only analysis/audit cases may omit test_argv")
    return copy.deepcopy({**metadata, "test_argv": argv})


def _clean_relpath(value: str, label: str) -> None:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "" in path.parts:
        raise CaseDataError(f"{label}: unsafe relative path {value!r}")


def _require_string(mapping: dict[str, Any], key: str, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise CaseDataError(f"{label}: {key} must be a non-empty string")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _bytes_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(value: bytes) -> str:
    return _bytes_digest(value)
