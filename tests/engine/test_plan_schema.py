"""Task 12 RED：PLAN schema 逐條驗證（spec §6.3、F7、plan Task 12）。

鎖定（§6.3 逐條）：
- 五欄全必填：`requirements` / `invariants` / `files_to_inspect` / `risks` /
  `test_targets`；任一欄缺失、空列表、空字串、非字串項 → `PlanError`。
- `requirements` 必須涵蓋 card 全部 `public_requirements[].id`（可超集）。
- `files_to_inspect` 每項必須存在於 frozen repo（`repo_files` 由 loop 提供）。
- `test_targets` 每項必須是 `tests/` 下 repo-relative path；`tests/agent/**`
  允許尚不存在，其餘必須存在於 frozen repo。
- YAML 解析失敗 / 非 mapping → `PlanError`；reason 為 zh-TW 敘事（render pack）。
"""

from __future__ import annotations

from patchmud.engine.plan_schema import PlanArtifact, PlanError, validate_plan
from tests.evaluator.helpers import make_card

REPO_FILES = frozenset(
    {
        "src/snapshot.py",
        "src/helper.py",
        "tests/public/test_main.py",
        "tests/starter/test_delete.py",
    }
)

VALID_PLAN = """\
requirements: [MAIN-1]
invariants:
  - snapshot 不因刪除失敗而改變
files_to_inspect:
  - src/snapshot.py
risks:
  - KeyError 邊界條件易誤判
test_targets:
  - tests/agent/test_snapshot_delete.py
"""


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def validate(yaml_text: str) -> PlanArtifact | PlanError:
    return validate_plan(yaml_text, make_card(), repo_files=REPO_FILES)


class TestValidPlan:
    def test_valid_plan_returns_frozen_artifact(self) -> None:
        artifact = validate(VALID_PLAN)
        assert isinstance(artifact, PlanArtifact)
        assert artifact.requirements == ("MAIN-1",)
        assert artifact.invariants == ("snapshot 不因刪除失敗而改變",)
        assert artifact.files_to_inspect == ("src/snapshot.py",)
        assert artifact.risks == ("KeyError 邊界條件易誤判",)
        assert artifact.test_targets == ("tests/agent/test_snapshot_delete.py",)

    def test_requirements_superset_allowed(self) -> None:
        text = VALID_PLAN.replace(
            "requirements: [MAIN-1]", "requirements: [MAIN-1, EXTRA-9]"
        )
        assert isinstance(validate(text), PlanArtifact)

    def test_existing_public_test_target_allowed(self) -> None:
        text = VALID_PLAN.replace(
            "  - tests/agent/test_snapshot_delete.py",
            "  - tests/public/test_main.py",
        )
        assert isinstance(validate(text), PlanArtifact)


class TestSchemaFailures:
    def test_missing_field_fails(self) -> None:
        text = VALID_PLAN.replace(
            "risks:\n  - KeyError 邊界條件易誤判\n", ""
        )
        err = validate(text)
        assert isinstance(err, PlanError)
        assert _has_cjk(err.reason)

    def test_empty_list_fails(self) -> None:
        # plan 空列表 → PlanError（F7，plan Task 12 指定）
        text = VALID_PLAN.replace("requirements: [MAIN-1]", "requirements: []")
        assert isinstance(validate(text), PlanError)

    def test_empty_string_item_fails(self) -> None:
        text = VALID_PLAN.replace(
            "  - snapshot 不因刪除失敗而改變", '  - ""'
        )
        assert isinstance(validate(text), PlanError)

    def test_non_string_item_fails(self) -> None:
        text = VALID_PLAN.replace("requirements: [MAIN-1]", "requirements: [1]")
        assert isinstance(validate(text), PlanError)

    def test_requirements_missing_main_id_fails(self) -> None:
        # 漏 MAIN id → PlanError（F7，plan Task 12 指定）
        text = VALID_PLAN.replace(
            "requirements: [MAIN-1]", "requirements: [OTHER-9]"
        )
        err = validate(text)
        assert isinstance(err, PlanError)
        assert "MAIN-1" in err.reason

    def test_files_to_inspect_must_exist_in_frozen_repo(self) -> None:
        text = VALID_PLAN.replace(
            "  - src/snapshot.py", "  - src/ghost.py"
        )
        err = validate(text)
        assert isinstance(err, PlanError)
        assert "src/ghost.py" in err.reason

    def test_test_target_outside_tests_fails(self) -> None:
        text = VALID_PLAN.replace(
            "  - tests/agent/test_snapshot_delete.py", "  - src/snapshot.py"
        )
        assert isinstance(validate(text), PlanError)

    def test_nonexistent_non_agent_test_target_fails(self) -> None:
        # tests/agent/** 以外的 test target 必須存在於 frozen repo
        text = VALID_PLAN.replace(
            "  - tests/agent/test_snapshot_delete.py",
            "  - tests/public/test_ghost.py",
        )
        assert isinstance(validate(text), PlanError)

    def test_invalid_yaml_fails(self) -> None:
        assert isinstance(validate("requirements: [unclosed"), PlanError)

    def test_non_mapping_yaml_fails(self) -> None:
        assert isinstance(validate("- just\n- a list\n"), PlanError)

    def test_empty_payload_fails(self) -> None:
        # PLAY PLAN 無 PLAN: 區塊 → payload 空字串 → schema 不過（protocol 註記）
        assert isinstance(validate(""), PlanError)
