"""source.yaml → SourceSpec 載入與驗證（fail-closed）。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import yaml

from patchmud.authoring.model import (
    SOURCE_SCHEMA_VERSION,
    AuthoringError,
    SourceSpec,
)

__all__ = ["load_source"]

_REQUIRED_FIELDS = (
    "schema_version",
    "issue_id",
    "archetype",
    "difficulty",
    "summary",
    "origin",
    "allowed_paths",
    "expected_paths",
    "buggy_repo",
    "reference_patch",
    "public_tests",
    "hidden_tests",
)

_HIDDEN_PREFIX = "hidden/"


def load_source(path: Path) -> SourceSpec:
    """讀 source.yaml，任何契約違反一律 raise AuthoringError（fail-closed）。"""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AuthoringError(f"source.yaml 解析失敗：{exc}") from exc
    if not isinstance(data, dict):
        raise AuthoringError("source.yaml 頂層必須是 mapping")

    missing = [key for key in _REQUIRED_FIELDS if key not in data]
    if missing:
        raise AuthoringError(f"source.yaml 缺必填欄位：{', '.join(missing)}")

    if data["schema_version"] != SOURCE_SCHEMA_VERSION:
        raise AuthoringError(
            f"schema_version 不支援：{data['schema_version']}"
            f"（僅支援 {SOURCE_SCHEMA_VERSION}）"
        )

    origin = data["origin"]
    if not isinstance(origin, dict) or "source" not in origin:
        raise AuthoringError("origin 必須是含 source 的 mapping")

    buggy_repo = _file_map(data["buggy_repo"], "buggy_repo")
    public_tests = _file_map(data["public_tests"], "public_tests")
    for rel, _content in public_tests:
        if rel.startswith(_HIDDEN_PREFIX):
            raise AuthoringError(f"public_tests 不得落在 hidden/：{rel}")
    hidden_tests = _hidden_map(data["hidden_tests"])

    for field in ("allowed_paths", "expected_paths"):
        for pattern in _str_list(data[field], field):
            _require_clean_relpath(pattern, field)

    requirements = _requirements(data.get("requirements"), str(data["summary"]))
    rubric_functional = _rubric_functional(data.get("rubric_points"))

    return SourceSpec(
        schema_version=int(data["schema_version"]),
        issue_id=str(data["issue_id"]),
        archetype=str(data["archetype"]),
        difficulty=str(data["difficulty"]),
        summary=str(data["summary"]),
        origin_source=str(origin["source"]),
        origin_fixed_at=(
            str(origin["fixed_at"]) if origin.get("fixed_at") is not None else None
        ),
        allowed_paths=_str_tuple(data["allowed_paths"], "allowed_paths"),
        expected_paths=_str_tuple(data["expected_paths"], "expected_paths"),
        buggy_repo=buggy_repo,
        reference_patch=str(data["reference_patch"]),
        public_tests=public_tests,
        hidden_tests=hidden_tests,
        requirements=requirements,
        rubric_functional=rubric_functional,
    )


def _file_map(value: object, field: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or not value:
        raise AuthoringError(f"{field} 必須是非空 mapping（path: 內容）")
    items: list[tuple[str, str]] = []
    for rel, content in value.items():
        _require_clean_relpath(str(rel), f"{field} key")
        items.append((str(rel), str(content)))
    return tuple(items)


def _hidden_map(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or not value:
        raise AuthoringError("hidden_tests 必須是非空 mapping（檔名: 內容）")
    items: list[tuple[str, str]] = []
    for name, content in value.items():
        name = str(name)
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise AuthoringError(
                f"hidden_tests 的 key 必須是純檔名（不得含目錄）：{name!r}"
            )
        items.append((name, str(content)))
    return tuple(items)


def _requirements(value: object, summary: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return (("MAIN-1", summary),)
    if not isinstance(value, list) or not value:
        raise AuthoringError("requirements 必須是非空 list")
    items: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or "id" not in item or "text" not in item:
            raise AuthoringError(f"requirements 項目必須含 id 與 text：{item!r}")
        items.append((str(item["id"]), str(item["text"])))
    return tuple(items)


def _rubric_functional(value: object) -> int:
    if value is None:
        return 60
    if not isinstance(value, dict) or "functional" not in value:
        raise AuthoringError("rubric_points 必須是含 functional 的 mapping")
    return int(value["functional"])


def _require_clean_relpath(path: str, field: str) -> None:
    """path 必須是 normalized POSIX 相對路徑：拒絕絕對、`.`/`..`、空段、反斜線。"""
    if not path or path != path.strip():
        raise AuthoringError(f"{field} 路徑非法（空白／前後空格）：{path!r}")
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise AuthoringError(f"{field} 不得為絕對路徑：{path!r}")
    if any(part in ("", ".", "..") for part in pure.parts):
        raise AuthoringError(f"{field} 不得含 `.`／`..`／空段：{path!r}")
    if "\\" in path:
        raise AuthoringError(f"{field} 不得含反斜線：{path!r}")


def _str_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list):
        raise AuthoringError(f"{field} 必須是 list：{value!r}")
    return [str(item) for item in value]


def _str_tuple(value: object, field: str) -> tuple[str, ...]:
    return tuple(_str_list(value, field))
