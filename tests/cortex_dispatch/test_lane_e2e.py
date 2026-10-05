"""paulsha-cortex#842：builder lane 端到端（真 bwrap 評分；codex session 以離線假件取代）。

假 agent 直接在 host 上的 agent workspace 改檔、commit，並回傳 codex ``--json`` 事件串與
「實際執行過」的 bwrap argv；其餘（候選讀取、評分、report、profile-binding）全走正式程式。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from patchmud.cli import (
    _ruff_argv,
    _sandbox_pytest_argv,
    _toolchain_paths,
    build_profile_binding,
    main,
)
from patchmud.cortex_dispatch.lane import DispatchLaneConfig, DispatchRunError, run_dispatch_lane
from patchmud.cortex_dispatch.sandbox import AgentOutcome, build_agent_bwrap_argv
from patchmud.cortex_dispatch.target import load_dispatch_target
from patchmud.sandbox.isolate import IsolationRunner

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests" / "fixtures" / "mini_encounter"
TARGET = REPO / "fixtures" / "cortex-dispatch" / "small-fix-subagent-build-codex-gpt-6-luna-green.json"
REFERENCE_DIFF = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")
_BWRAP = Path("/usr/bin/bwrap")
USAGE = {
    "input_tokens": 52000,
    "cached_input_tokens": 41000,
    "cache_write_input_tokens": 0,
    "output_tokens": 900,
    "reasoning_output_tokens": 400,
}
_GIT_ENV = {"PATH": "/usr/bin:/bin", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}


@pytest.fixture()
def real_capabilities():
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    caps = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP).capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


def _git(ws: Path, *args: str, stdin: str | None = None) -> None:
    subprocess.run(["git", "-C", str(ws), *args], check=True, env=_GIT_ENV, input=stdin, text=True,
                   capture_output=True)


class FakeCodex:
    """離線 builder session：依 ``behaviour`` 改 workspace，回傳 codex 事件串。"""

    def __init__(
        self, behaviour: str = "fix", *, usage: dict | None = USAGE, marker: Path | None = None
    ) -> None:
        self.behaviour = behaviour
        self.usage = usage
        self.marker = marker
        self.threads: dict[str, dict] = {}
        self.launches = []

    def __call__(self, launch) -> AgentOutcome:
        self.launches.append(launch)
        ws = launch.spec.workspace
        exit_code, timed_out = 0, False
        if self.behaviour in ("fix", "dirty", "fsmonitor"):
            _git(ws, "apply", "-", stdin=REFERENCE_DIFF)
            _git(ws, "add", "-A")
            _git(ws, "commit", "-qm", "fix negative stock")
            if self.behaviour == "dirty":
                (ws / "notes.txt").write_text("left behind\n", encoding="utf-8")
            if self.behaviour == "fsmonitor":
                _git(ws, "config", "core.fsmonitor", f"touch {self.marker}; false")
        elif self.behaviour == "uncommitted":
            _git(ws, "apply", "-", stdin=REFERENCE_DIFF)
        elif self.behaviour == "protected":
            _git(ws, "apply", "-", stdin=REFERENCE_DIFF)
            public = next((ws / "tests" / "public").glob("*.py"))
            public.write_text(public.read_text(encoding="utf-8") + "\n# weakened\n", encoding="utf-8")
            _git(ws, "add", "-A")
            _git(ws, "commit", "-qm", "fix and edit public test")
        elif self.behaviour == "timeout":
            timed_out = True
        elif self.behaviour == "crash":
            exit_code = 1
        thread_id = f"offline-thread-{len(self.threads) + 1:04d}"
        self.threads[thread_id] = {
            "id": thread_id,
            "model": "gpt-6-luna",
            "reasoningEffort": "max",
            "modelProvider": "openai",
        }
        events = [
            {"type": "thread.started", "thread_id": thread_id},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "python3 -m pytest -q tests/public"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        ]
        if self.usage is not None:
            events.append({"type": "turn.completed", "usage": self.usage})
        argv = build_agent_bwrap_argv(launch.spec, list(launch.codex_argv), egress_dir=Path("/tmp/pm-eg-fake"))
        return AgentOutcome(
            exit_code=None if timed_out else exit_code,
            timed_out=timed_out,
            stdout="\n".join(json.dumps(event) for event in events) + "\n",
            stderr_tail="",
            wall_ms=4321,
            egress_allowed={"chatgpt.com": 5},
            egress_denied={},
            sandbox_argv=tuple(argv[: argv.index("--") + 1]),
        )


def _config(fake: FakeCodex, tmp_path: Path) -> DispatchLaneConfig:
    toolchain = _toolchain_paths()
    auth = tmp_path / "fake-auth.json"
    auth.write_text("{}", encoding="utf-8")
    return DispatchLaneConfig(
        toolchain=toolchain,
        pytest_argv=_sandbox_pytest_argv(),
        ruff_argv=_ruff_argv(toolchain),
        agent_runner=fake,
        thread_reader_factory=lambda _launch: fake.threads.__getitem__,
        codex_invoke="/opt/codex/bin/codex",
        runtime_ro=(Path("/opt/codex"),),
        path_dirs=("/opt/codex/bin",),
        auth_file=auth,
    )


def _deck(tmp_path: Path) -> Path:
    deck = tmp_path / "deck-single" / "mini_encounter"
    shutil.copytree(FIXTURE, deck)
    return deck


def _run(tmp_path: Path, behaviour: str, **kwargs):
    fake = FakeCodex(behaviour, **kwargs)
    target = load_dispatch_target(TARGET)
    result = run_dispatch_lane(
        _deck(tmp_path), target, tmp_path / "runs", _config(fake, tmp_path), run_id=f"lane-{behaviour}"
    )
    return result, fake, target


def test_committed_fix_clears_with_cortex_dispatch_key(real_capabilities, tmp_path):
    result, fake, target = _run(tmp_path, "fix")
    assert (result.clear, result.end_reason, result.failure) == (1, "commit", None)
    assert result.profile_id == target.cortex_resolved_key
    assert result.actual_condition_key.startswith("epk:v1:actual:")

    launch = fake.launches[0]
    assert launch.codex_argv[1:7] == ("exec", "--ignore-user-config", launch.codex_argv[3], "--json", "--sandbox", "danger-full-access")
    prompt = launch.codex_argv[3]
    assert prompt.startswith("[PERSONA CONTRACT — role: builder")
    assert "[CARD: subagent-build]" in prompt
    assert "test_cr1_no_negative_stock" not in prompt  # hidden probe 不進 prompt

    run_dir = result.run_dir
    record = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
    assert record["loadout"] == "builder"
    assert record["model"] == "codex:gpt-6-luna"
    assert record["role"] == "builder"
    assert record["dispatch_target"]["sha256"] == target.sha256
    post_run = json.loads((run_dir / "execution_profile.json").read_text(encoding="utf-8"))
    assert all(v["state"] == "known" for v in post_run["observed"]["conditions"].values())
    assert post_run["observed"]["conditions"] == post_run["resolved"]["conditions"]
    launch_facts = post_run["observed"]["metadata"]["discovery"]["launch"]
    assert launch_facts["egress_allowed"] == {"chatgpt.com": 5}

    report_dir = tmp_path / "report"
    assert main(["report", "--runs", str(tmp_path / "runs" / "*"), "--out", str(report_dir)]) == 0
    report = json.loads((report_dir / "report.json").read_text(encoding="utf-8"))
    rows = report["leaderboards"]["clear_rate"]["rows"]
    assert len(rows) == 1
    assert rows[0]["profile_id"] == target.cortex_resolved_key
    assert rows[0]["loadout"] == "builder"
    assert rows[0]["model"] == "codex:gpt-6-luna"
    assert rows[0]["coverage_complete"] is True
    binding = build_profile_binding([run_dir])
    assert binding["profile_id"] == target.cortex_resolved_key
    assert binding["actual_condition_key"] == result.actual_condition_key


@pytest.mark.parametrize(
    ("behaviour", "end_reason", "failure"),
    [
        ("uncommitted", "failed:protocol", "no-committed-candidate"),
        ("dirty", "failed:protocol", "candidate-worktree-dirty"),
        ("protected", "failed:protocol", "candidate-rejected"),
        ("timeout", "wall_clock", "builder-session-timeout"),
        ("crash", "failed:protocol", "codex-exit-1"),
    ],
)
def test_builder_contract_violations_never_clear(real_capabilities, tmp_path, behaviour, end_reason, failure):
    result, _fake, target = _run(tmp_path, behaviour)
    assert (result.clear, result.end_reason, result.failure) == (0, end_reason, failure)
    assert result.profile_id == target.cortex_resolved_key
    outcome = yaml.safe_load((result.run_dir / "result.yaml").read_text(encoding="utf-8"))
    assert outcome["protocol_failed"] is (end_reason == "failed:protocol")
    assert outcome["dispatch"]["failure"] == failure


def test_agent_repo_config_never_executes_on_host(real_capabilities, tmp_path):
    marker = tmp_path / "fsmonitor-ran-on-host"
    result, _fake, _target = _run(tmp_path, "fsmonitor", marker=marker)
    assert result.clear == 1
    assert not marker.exists()


def test_missing_usage_leaves_run_incomplete(real_capabilities, tmp_path):
    with pytest.raises(DispatchRunError, match="turn.completed.usage"):
        _run(tmp_path, "fix", usage=None)
    run_dir = tmp_path / "runs" / "lane-fix"
    assert (run_dir / "run.yaml").is_file()
    assert not (run_dir / "result.yaml").exists()
    assert not (run_dir / "execution_profile.json").exists()
