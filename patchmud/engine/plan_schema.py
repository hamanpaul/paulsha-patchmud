"""PLAN schema 驗證：`PLAY PLAN` payload → `PlanArtifact`（spec §6.3、F7）。

- 五欄全必填：``requirements`` / ``invariants`` / ``files_to_inspect`` /
  ``risks`` / ``test_targets``；任一欄缺失、空列表、空字串、非字串項、YAML
  解析失敗、非 mapping → :class:`PlanError`（fail-closed，`PLAY PLAN` 為
  illegal action、消耗 turn；判 illegal 是 turn loop 的事）。
- ``requirements`` 必須涵蓋 card 全部 ``public_requirements[].id``（可超集）。
- ``files_to_inspect`` 每項必須存在於 frozen repo；``repo_files`` 由 turn loop
  自 workspace 檔案列表提供（plan 對 pin 簽名的最小擴充：card 不含檔案清單，
  存在性檢查需要 frozen repo 真相）。
- ``test_targets`` 每項必須是 ``tests/`` 下 repo-relative path；
  ``tests/agent/**`` 允許尚不存在，其餘必須存在於 frozen repo。
- 錯誤敘事一律出自 zh-TW render pack（§5.4）；欄位名維持英文（結構化協定）。
- 通過後由 strategy enforcer 凍結（`record_plan`），後續修改一律 illegal。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Collection

import yaml

from patchmud.deck.model import IssueCard
from patchmud.engine import render_zh_tw as zh

__all__ = ["PlanArtifact", "PlanError", "validate_plan"]

#: §6.3 schema 的五個必填欄位（順序即文件順序）。
_FIELDS = (
    "requirements",
    "invariants",
    "files_to_inspect",
    "risks",
    "test_targets",
)

_AGENT_TEST_PREFIX = "tests/agent/"


@dataclass(frozen=True)
class PlanArtifact:
    """通過 §6.3 schema 的凍結 plan（作者可見 artifact；reviewer 輸入之一）。"""

    requirements: tuple[str, ...]
    invariants: tuple[str, ...]
    files_to_inspect: tuple[str, ...]
    risks: tuple[str, ...]
    test_targets: tuple[str, ...]


@dataclass(frozen=True)
class PlanError:
    """schema 不過：reason 為 zh-TW 敘事（render 給作者，動作照樣消耗 turn）。"""

    reason: str


def _string_list(value: object) -> tuple[str, ...] | None:
    """非空、逐項非空字串的列表 → tuple；否則 None（空字串／非字串皆拒）。"""
    if not isinstance(value, list) or not value:
        return None
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        items.append(item)
    return tuple(items)


def _valid_test_target(path: str, repo_files: frozenset[str]) -> bool:
    parts = path.split("/")
    if not path.startswith("tests/") or ".." in parts or "." in parts:
        return False
    if path.startswith(_AGENT_TEST_PREFIX):
        return True  # 允許尚不存在的 agent 測試路徑
    return path in repo_files


def validate_plan(
    yaml_text: str, card: IssueCard, *, repo_files: Collection[str]
) -> PlanArtifact | PlanError:
    """§6.3 逐條驗證 `PLAY PLAN` payload；`repo_files` 為 frozen repo 檔案清單。"""
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return PlanError(reason=zh.text("plan.invalid_yaml", detail=type(exc).__name__))
    if not isinstance(data, dict):
        return PlanError(reason=zh.text("plan.not_mapping"))

    fields: dict[str, tuple[str, ...]] = {}
    for field in _FIELDS:
        if field not in data:
            return PlanError(reason=zh.text("plan.field_missing", field=field))
        values = _string_list(data[field])
        if values is None:
            return PlanError(reason=zh.text("plan.field_empty", field=field))
        fields[field] = values

    required_ids = {req.id for req in card.public_requirements}
    missing = sorted(required_ids - set(fields["requirements"]))
    if missing:
        return PlanError(
            reason=zh.text("plan.requirements_missing_ids", ids=", ".join(missing))
        )

    known = frozenset(repo_files)
    for path in fields["files_to_inspect"]:
        if path not in known:
            return PlanError(reason=zh.text("plan.unknown_inspect_file", path=path))
    for path in fields["test_targets"]:
        if not _valid_test_target(path, known):
            return PlanError(reason=zh.text("plan.bad_test_target", path=path))

    return PlanArtifact(**fields)
