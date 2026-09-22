"""RED tests for the opt-in native coding-agent isolation surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from patchmud.sandbox.isolate import Execution, IsolationRunner, build_bwrap_argv


_ARITY = {
    "--bind": 2,
    "--ro-bind": 2,
    "--symlink": 2,
    "--setenv": 2,
    "--tmpfs": 1,
    "--proc": 1,
    "--dev": 1,
    "--chdir": 1,
}


def _parse_bwrap_argv(argv: list[str]) -> tuple[list[tuple[str, tuple[str, ...]]], list[str]]:
    ops: list[tuple[str, tuple[str, ...]]] = []
    index = 1
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return ops, argv[index + 1 :]
        arity = _ARITY.get(token, 0)
        ops.append((token, tuple(argv[index + 1 : index + 1 + arity])))
        index += 1 + arity
    raise AssertionError("bwrap argv missing -- separator")


def _fake_bwrap(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/python3\n" + body)
    path.chmod(0o755)
    return path


def test_native_options_allow_network_explicit_mounts_and_env() -> None:
    argv = build_bwrap_argv(
        Path("/work/repo"),
        [Path("/usr")],
        ["native-agent"],
        network_access=True,
        ro_bindings=[(Path("/auth"), Path("/sandbox-home/.codex"))],
        rw_bindings=[(Path("/state"), Path("/sandbox-home/.state"))],
        sandbox_env={
            "HOME": "/sandbox-home",
            "CODEX_HOME": "/sandbox-home/.codex",
            "XDG_CONFIG_HOME": "/sandbox-home/.config",
            "PATH": "/usr/bin:/bin",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    )

    ops, command = _parse_bwrap_argv(argv)
    assert "--unshare-net" not in argv
    assert "--unshare-pid" in argv
    assert command == ["native-agent"]
    assert ("--ro-bind", ("/auth", "/sandbox-home/.codex")) in ops
    assert ("--bind", ("/state", "/sandbox-home/.state")) in ops
    setenv = {args[0]: args[1] for flag, args in ops if flag == "--setenv"}
    assert setenv["HOME"] == "/sandbox-home"
    assert setenv["CODEX_HOME"] == "/sandbox-home/.codex"
    assert setenv["XDG_CONFIG_HOME"] == "/sandbox-home/.config"
    assert setenv["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert setenv["GIT_CONFIG_SYSTEM"] == "/dev/null"


@pytest.mark.parametrize(
    "key",
    ["TYPESAFE_API_KEY", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONHOME"],
)
def test_native_sandbox_env_rejects_credentials_and_loader_injection(key: str) -> None:
    with pytest.raises(ValueError, match="sandbox_env"):
        build_bwrap_argv(
            Path("/work/repo"), [Path("/usr")], ["native-agent"], sandbox_env={key: "x"}
        )


def test_native_run_passes_prompt_to_fake_namespace(tmp_path: Path) -> None:
    received = tmp_path / "received.txt"
    fake = _fake_bwrap(
        tmp_path / "fake-bwrap",
        (
            "import pathlib, subprocess, sys\n"
            f"pathlib.Path({str(received)!r}).write_text(sys.stdin.read())\n"
            "cmd = sys.argv[sys.argv.index('--') + 1:]\n"
            "raise SystemExit(subprocess.call(cmd))\n"
        ),
    )
    worktree = tmp_path / "repo"
    worktree.mkdir()
    runner = IsolationRunner(worktree, [Path("/usr")], bwrap_path=fake)

    result = runner.run(
        ["/usr/bin/python3", "-c", "raise SystemExit(0)"],
        cwd=worktree,
        timeout_s=5,
        input_text="native prompt",
    )

    assert result.exit_code == 0
    assert received.read_text() == "native prompt"


def test_native_run_none_input_keeps_old_popen_contract(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[dict, float | None]] = []

    class FakeProcess:
        pid = 321
        returncode = 0

        def communicate(self, timeout=None):
            calls.append(({}, timeout))
            return "out", "err"

    def fake_popen(argv, **kwargs):
        calls.append((kwargs, None))
        return FakeProcess()

    monkeypatch.setattr("patchmud.sandbox.isolate.subprocess.Popen", fake_popen)
    runner = IsolationRunner(tmp_path, bwrap_path="fake-bwrap")
    result = runner.run(["true"], cwd=tmp_path, timeout_s=3)

    assert result.stdout == "out"
    assert "stdin" not in calls[0][0]
    assert calls[1][1] == 3


def test_keyboard_interrupt_attaches_partial_execution(monkeypatch, tmp_path: Path) -> None:
    events: list[object] = []

    class FakeProcess:
        pid = 654
        returncode = -9

        def communicate(self, timeout=None):
            events.append(("communicate", timeout))
            if timeout is not None:
                raise KeyboardInterrupt
            return "partial stdout", "partial stderr"

    monkeypatch.setattr(
        "patchmud.sandbox.isolate.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "patchmud.sandbox.isolate._kill_process_group",
        lambda pid: events.append(("kill", pid)),
    )
    runner = IsolationRunner(tmp_path, bwrap_path="fake-bwrap")

    with pytest.raises(KeyboardInterrupt) as caught:
        runner.run(["true"], cwd=tmp_path, timeout_s=3)

    result = getattr(caught.value, "execution_result")
    assert isinstance(result, Execution)
    assert result.exit_code == -9
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert result.timed_out is False
    assert events == [("communicate", 3), ("kill", 654), ("communicate", None)]
