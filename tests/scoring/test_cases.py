"""Quality and contract tests for the engineering-v1 JEV case suite.

These tests deliberately inspect the public/private boundary and the actual
fixture material.  A case with only a title and a changed number is not a
usable benchmark case.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from patchmud.scoring.cases import CaseDataError, load_case, load_suite, public_case_snapshot


EXPECTED_CATEGORIES = ("repair", "diagnosis", "scope", "testing", "recovery", "audit")
EXPECTED_DEPTHS = (1, 2, 3)
DIMENSIONS = ("fulfillment", "evidence", "constraints", "verification")
OWNED_CASE_IDS = (
    "repair-config-rollback",
    "repair-cache-api",
    "repair-migration-state",
    "diagnosis-retry-budget",
    "diagnosis-queue-cache",
    "diagnosis-incident-timeline",
    "scope-rate-limit",
    "scope-cli-schema",
    "scope-changing-boundary",
    "testing-parser-matrix",
    "testing-retry-fakeclock",
    "testing-schema-evolution",
)


def test_owned_cases_load_independently_of_unfinished_sibling_categories() -> None:
    """Keep the twelve owned fixtures reviewable while other workers land six cases."""

    for case_id in OWNED_CASE_IDS:
        case = load_case(case_id)
        assert case["id"] == case_id
        assert case["category"] in {"repair", "diagnosis", "scope", "testing"}
        assert case["case_hash"]
        snapshot = public_case_snapshot(case)
        assert snapshot["id"] == case_id
        assert "anchors" not in snapshot


def test_engineering_suite_is_complete_and_balanced() -> None:
    suite = load_suite()
    assert suite["id"] == "engineering-v1"
    assert suite["version"]
    assert suite["rubric_version"] == "jev-1.13.0"
    cases = suite["cases"]
    assert len(cases) == 18
    assert {case["category"] for case in cases} == set(EXPECTED_CATEGORIES)
    assert {case["depth"] for case in cases} == set(EXPECTED_DEPTHS)
    for category in EXPECTED_CATEGORIES:
        selected = [case for case in cases if case["category"] == category]
        assert [case["depth"] for case in selected] == [1, 2, 3]
    assert len({case["id"] for case in cases}) == 18
    assert suite["suite_hash"]


def test_depths_have_pinned_turn_and_wall_budgets() -> None:
    cases = load_suite()["cases"]
    expected = {1: (8, 600), 2: (16, 1200), 3: (24, 1800)}
    for case in cases:
        assert (case["max_turns"], case["wall_seconds"]) == expected[case["depth"]]


def test_each_case_has_substantive_public_and_private_material() -> None:
    for case in load_suite()["cases"]:
        fixture = Path(case["fixture_dir"])
        repo = fixture / "repo"
        hidden = fixture / "hidden"
        assert fixture.is_absolute() and fixture.is_dir()
        assert repo.is_dir() and hidden.is_dir()
        public_files = case["public_files"]
        assert len(public_files) >= 3, case["id"]
        assert sum(len(text.splitlines()) for text in public_files.values()) >= 20
        assert len(case["prompt"]) >= 240
        assert len(case["requirements"]) >= 4
        if case["category"] not in {"diagnosis", "audit"}:
            assert case["allowed_paths"]
        assert isinstance(case["test_argv"], list)
        if case["anchors"]["kind"] == "patch":
            assert case["test_argv"]

        rubric = case["rubric"]
        assert tuple(rubric) == DIMENSIONS
        for dimension in DIMENSIONS:
            assert len(rubric[dimension]["instructions"]) >= 80
            assert len(rubric[dimension]["criteria"]) == 5
            assert all(rubric[dimension]["criteria"])

        stages = case["stages"]
        assert stages == sorted(stages, key=lambda item: item["after_turn"])
        assert all(stage["after_turn"] > 0 for stage in stages)
        assert all(stage["after_turn"] < case["max_turns"] for stage in stages)

        anchors = case["anchors"]
        assert anchors["reference"]
        assert anchors["partial"]
        assert anchors["wrong"]
        assert anchors["reference"] != anchors["wrong"]
        assert "final_report" in anchors["reference"]
        assert sum(anchors["reference"]["expected_quality"].values()) > sum(
            anchors["partial"]["expected_quality"].values()
        ) > sum(anchors["wrong"]["expected_quality"].values())


def test_public_snapshot_blinds_private_assets_and_absolute_fixture_path() -> None:
    for case in load_suite()["cases"]:
        snapshot = public_case_snapshot(case)
        encoded = json.dumps(snapshot, ensure_ascii=False)
        assert "fixture_dir" not in snapshot
        assert "anchors" not in snapshot
        assert "hidden/" not in encoded
        assert "/hidden/" not in encoded
        assert "reference.patch" not in encoded
        assert "partial.patch" not in encoded
        assert "wrong.patch" not in encoded
        assert "reference-answer.md" not in encoded
        assert snapshot["public_files"] == case["public_files"]
        assert snapshot["case_hash"] == case["case_hash"]


def test_case_lookup_returns_independent_objects() -> None:
    original = load_case("repair-config-rollback")
    changed = load_case("repair-config-rollback")
    changed["requirements"].append("test mutation")
    changed["rubric"]["evidence"]["criteria"][0] = "test mutation"
    assert "test mutation" not in original["requirements"]
    assert "test mutation" not in original["rubric"]["evidence"]["criteria"]


def test_case_hash_changes_when_private_anchor_changes(tmp_path: Path) -> None:
    source = Path(load_case("repair-config-rollback")["fixture_dir"])
    root = tmp_path / "engineering-v1"
    copied = root / source.name
    shutil.copytree(source, copied)
    before = load_case(source.name, _root=root)
    anchor = copied / "hidden" / "reference.patch"
    anchor.write_text(anchor.read_text(encoding="utf-8") + "\n# private drift\n", encoding="utf-8")
    after = load_case(source.name, _root=root)
    assert after["case_hash"] != before["case_hash"]


def test_case_rejects_repo_or_hidden_symlink(tmp_path: Path) -> None:
    source = Path(load_case("repair-config-rollback")["fixture_dir"])
    root = tmp_path / "engineering-v1"
    copied = root / source.name
    shutil.copytree(source, copied)
    (copied / "repo").rename(copied / "repo-real")
    (copied / "repo").symlink_to(copied / "hidden", target_is_directory=True)
    with pytest.raises(CaseDataError, match="repo/ and hidden/"):
        load_case(source.name, _root=root)


def test_suite_includes_noop_and_insufficient_evidence_outcomes() -> None:
    cases = load_suite()["cases"]
    outcomes = {case["anchors"]["outcome_class"] for case in cases}
    assert "noop" in outcomes
    assert "insufficient_evidence" in outcomes
    assert "code_change" in outcomes
    for case in cases:
        public_contract = " ".join(
            [case["prompt"], *case["requirements"], *case["allowed_paths"]]
        ).lower()
        if case["anchors"]["outcome_class"] == "noop":
            assert (
                "no change" in public_contract
                or "no-op" in public_contract
                or (not case["allowed_paths"] and "do not" in public_contract)
            )
        if case["anchors"]["outcome_class"] == "insufficient_evidence":
            assert "evidence" in public_contract


@pytest.mark.parametrize(
    "case_id",
    [
        "repair-config-rollback",
        "repair-cache-api",
        "repair-migration-state",
        "scope-rate-limit",
        "scope-cli-schema",
        "scope-changing-boundary",
        "testing-parser-matrix",
        "testing-retry-fakeclock",
        "testing-schema-evolution",
        "recovery-checkpoint",
        "recovery-transaction-rebuild",
        "recovery-flag-rollback",
    ],
)
def test_executable_cases_have_real_public_tests_and_patch_anchors(
    case_id: str, tmp_path: Path
) -> None:
    case = load_case(case_id)
    fixture = Path(case["fixture_dir"])
    public_test_paths = [
        path for path in case["public_files"] if path.startswith("tests/")
    ]
    assert public_test_paths, case_id
    assert case["anchors"]["kind"] == "patch"
    for anchor_name in ("reference", "partial", "wrong"):
        anchor = case["anchors"][anchor_name]
        assert anchor["final_report"].strip()
        assert anchor["patch_file"].startswith("hidden/")
        assert anchor["final_diff"].strip()
        assert (fixture / anchor["patch_file"]).is_file()
        anchor_repo = tmp_path / anchor_name
        shutil.copytree(fixture / "repo", anchor_repo)
        applied = subprocess.run(
            ["git", "apply", "--check", str(fixture / anchor["patch_file"])],
            cwd=anchor_repo,
            text=True,
            capture_output=True,
            check=False,
        )
        assert applied.returncode == 0, f"{case_id}/{anchor_name}: {applied.stderr}"
    # The private reference is a real unified diff, rather than a prose claim.
    reference = case["anchors"]["reference"]["final_diff"]
    assert "diff --git a/" in reference
    assert "+++ b/" in reference


def test_analysis_cases_have_evidence_artifacts_and_answer_anchors() -> None:
    for case in load_suite()["cases"]:
        if case["anchors"]["kind"] != "answer":
            continue
        fixture = Path(case["fixture_dir"])
        public = case["public_files"]
        assert any(path.endswith((".log", ".json", ".toml", ".yaml", ".py")) for path in public)
        assert len(case["anchors"]["reference"]["final_report"].split()) >= 80
        assert len(case["anchors"]["partial"]["final_report"].split()) >= 20
        assert len(case["anchors"]["wrong"]["final_report"].split()) >= 20
        assert (fixture / "hidden" / "anchors.json").is_file()
