"""paulsha-cortex#842：vendored persona 契約、codex argv 形狀與 catalog effort 預設。"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from patchmud.adapters.cortex_catalog import cortex_adapter_identity, load_catalog_provenance
from patchmud.cortex_dispatch.codex_argv import CodexArgvError, build_cortex_builder_codex_argv
from patchmud.cortex_dispatch.persona import (
    PERSONAS_FILENAME,
    PERSONAS_PROVENANCE_FILENAME,
    CortexPersonaError,
    load_persona_contract,
    load_personas_provenance,
    render_contract_prompt,
)

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = REPO / "patchmud" / "cortex_dispatch" / "data"


def test_vendored_personas_match_pinned_provenance():
    provenance = load_personas_provenance()
    data = (DATA_DIR / PERSONAS_FILENAME).read_bytes()
    assert hashlib.sha256(data).hexdigest() == provenance["sha256"]
    assert provenance["source_repo"] == "hamanpaul/paulsha-cortex"
    assert provenance["source_path"] == "paulsha_cortex/persona/personas.yaml"
    assert provenance["consumed_roles"] == ["builder"]


def test_personas_and_catalog_pin_the_same_cortex_revision_as_ci():
    """CI conformance 測試只 checkout 一個 Cortex revision：所有 vendored 資料必須同一個。"""
    persona_revision = load_personas_provenance()["source_revision"]
    assert persona_revision == load_catalog_provenance()["source_revision"]
    workflow = (REPO / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert f"ref: {persona_revision}" in workflow


def test_edited_personas_without_provenance_update_fail_closed(tmp_path):
    for name in (PERSONAS_FILENAME, PERSONAS_PROVENANCE_FILENAME):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    path = tmp_path / PERSONAS_FILENAME
    path.write_text(
        path.read_text(encoding="utf-8").replace('"git commit"', '"git push"', 1),
        encoding="utf-8",
    )
    with pytest.raises(CortexPersonaError, match="SHA-256"):
        load_persona_contract("builder", data_dir=tmp_path)


def test_only_declared_roles_are_consumed():
    with pytest.raises(CortexPersonaError, match="role"):
        load_persona_contract("reviewer")


def test_builder_contract_prompt_carries_commit_obligation():
    text = render_contract_prompt(load_persona_contract("builder"))
    assert text.startswith("[PERSONA CONTRACT — role: builder (v")
    assert text.endswith("[END PERSONA CONTRACT]")
    assert "  - git commit\n" in text
    assert "git status --porcelain" in text


def test_catalog_effort_default_mirrors_cortex_model_defaults():
    identity = cortex_adapter_identity("codex")
    assert identity.default_effort is None
    assert identity.default_effort_for("gpt-6-luna") == "max"
    assert identity.default_effort_for("gpt-5.3-codex-spark") == "xhigh"
    assert identity.default_effort_for("unlisted-model") is None
    assert cortex_adapter_identity("copilot").default_effort_for("anything") == "xhigh"


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_codex_argv_is_cortex_commit_required_builder_row(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    argv = build_cortex_builder_codex_argv(
        codex="codex",
        prompt="PROMPT",
        model="gpt-6-luna",
        effort="max",
        last_message_path="/codex-home/last.json",
        worktree="/workspace/repo",
        host_worktree=repo,
    )
    assert argv == [
        "codex", "exec", "--ignore-user-config", "PROMPT", "--json",
        "--sandbox", "danger-full-access",
        "--model", "gpt-6-luna",
        "-c", 'model_reasoning_effort="max"',
        "-o", "/codex-home/last.json",
        "-C", "/workspace/repo",
    ]


def test_linked_worktree_is_rejected_because_cortex_would_add_git_dirs(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    (repo / "README").write_text("x\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null"}
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-qm", "init"],
        check=True, env=env,
    )
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(linked)], check=True, env=env)
    with pytest.raises(CodexArgvError, match="linked worktree"):
        build_cortex_builder_codex_argv(
            codex="codex", prompt="P", model="m", effort="max",
            last_message_path="/x", worktree="/workspace/repo", host_worktree=linked,
        )


@pytest.mark.parametrize("worktree", ["relative/path", ""])
def test_codex_argv_requires_absolute_worktree(worktree):
    with pytest.raises(CodexArgvError):
        build_cortex_builder_codex_argv(
            codex="codex", prompt="P", model="m", effort="max",
            last_message_path="/x", worktree=worktree,
        )
