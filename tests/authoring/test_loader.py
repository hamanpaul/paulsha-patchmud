"""Part B Task B1：SourceSpec 契約與 load_source（fail-closed）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from patchmud.authoring import AuthoringError, load_source

MINIMAL = {
    "schema_version": 1,
    "issue_id": "demo-v1",
    "archetype": "parser-edge",
    "difficulty": "easy",
    "summary": "值裡含 = 會被切爛",
    "origin": {"source": "closed bug: example.com/#1"},
    "allowed_paths": ["src/**", "tests/agent/**"],
    "expected_paths": ["src/x.py"],
    "buggy_repo": {"src/x.py": "def f():\n    return 1\n"},
    "reference_patch": "diff --git a/src/x.py b/src/x.py\n",
    "public_tests": {"tests/public/test_x.py": "def test_x():\n    assert True\n"},
    "hidden_tests": {"test_cr1_x.py": "def test_cr1():\n    assert True\n"},
}


def _write(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "source.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p


def test_loads_minimal(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    assert spec.issue_id == "demo-v1"
    assert spec.summary == "值裡含 = 會被切爛"
    assert dict(spec.buggy_repo)["src/x.py"].startswith("def f")
    assert dict(spec.hidden_tests)["test_cr1_x.py"]
    # 預設：requirements 由 summary 生一條 MAIN-1；rubric_functional 60
    assert spec.requirements == (("MAIN-1", "值裡含 = 會被切爛"),)
    assert spec.rubric_functional == 60
    assert spec.origin_source == "closed bug: example.com/#1"


def test_explicit_requirements_and_rubric(tmp_path):
    data = dict(MINIMAL)
    data["requirements"] = [{"id": "MAIN-1", "text": "只切第一個 ="}]
    data["rubric_points"] = {"functional": 50}
    spec = load_source(_write(tmp_path, data))
    assert spec.requirements == (("MAIN-1", "只切第一個 ="),)
    assert spec.rubric_functional == 50


@pytest.mark.parametrize(
    "missing",
    ["issue_id", "reference_patch", "buggy_repo", "hidden_tests", "origin", "summary"],
)
def test_missing_field_fails(tmp_path, missing):
    data = {k: v for k, v in MINIMAL.items() if k != missing}
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))


def test_bad_schema_version_fails(tmp_path):
    data = dict(MINIMAL)
    data["schema_version"] = 2
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))


def test_path_traversal_rejected(tmp_path):
    data = dict(MINIMAL)
    data["buggy_repo"] = {"../evil.py": "x\n"}
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))


def test_hidden_test_name_must_be_bare(tmp_path):
    # hidden_tests 的 key 是檔名，不得帶目錄（builder 會放進 hidden/）
    data = dict(MINIMAL)
    data["hidden_tests"] = {"sub/x.py": "y\n"}
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))


def test_public_test_under_hidden_rejected(tmp_path):
    data = dict(MINIMAL)
    data["public_tests"] = {"hidden/sneaky.py": "z\n"}
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))
