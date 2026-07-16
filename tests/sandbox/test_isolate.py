"""Task 2 RED：namespace 隔離執行器（spec §7）unit tests。

不啟真 namespace：以 fake bwrap 腳本注入 `IsolationRunner`，鎖定：
- `build_bwrap_argv` bind 參數只含 worktree（rw）、toolchain（ro）、tmpfs `/tmp`；
  `--unshare-net --unshare-pid --die-with-parent` 存在。
- env 只剩白名單四鍵（PATH/LANG/LC_ALL/TMPDIR，§7）。
- timeout 觸發 `timed_out=True` 且 child 被終止。
- capabilities 探測失敗回全 False（fail-closed）。
"""

import json
import os
import textwrap
from pathlib import Path

import pytest

from patchmud.sandbox.isolate import (
    Capabilities,
    Execution,
    IsolationRunner,
    build_bwrap_argv,
)

# bwrap 選項的參數個數（本測試 parser 只需涵蓋我們關心的旗標）
_ARITY = {
    "--bind": 2,
    "--bind-try": 2,
    "--ro-bind": 2,
    "--ro-bind-try": 2,
    "--dev-bind": 2,
    "--dev-bind-try": 2,
    "--symlink": 2,
    "--setenv": 2,
    "--tmpfs": 1,
    "--proc": 1,
    "--dev": 1,
    "--chdir": 1,
    "--unsetenv": 1,
}


def _parse_bwrap_argv(argv: list[str]) -> tuple[list[tuple[str, tuple[str, ...]]], list[str]]:
    """把 bwrap argv 拆成 (旗標, 參數) 序列與 `--` 之後的 command。"""
    ops: list[tuple[str, tuple[str, ...]]] = []
    i = 1  # argv[0] 是 bwrap 路徑
    while i < len(argv):
        token = argv[i]
        if token == "--":
            return ops, argv[i + 1 :]
        arity = _ARITY.get(token, 0)
        ops.append((token, tuple(argv[i + 1 : i + 1 + arity])))
        i += 1 + arity
    raise AssertionError("bwrap argv 缺 `--` 分隔符")


def _write_fake_bwrap(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/python3\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    return wt


# ---------------------------------------------------------------------------
# build_bwrap_argv：bind allowlist 與 namespace 旗標
# ---------------------------------------------------------------------------


def test_build_bwrap_argv_bind_allowlist_only():
    worktree = Path("/work/wt")
    toolchain = [Path("/usr"), Path("/opt/venv")]
    inner = ["python3", "-m", "pytest", "-q"]
    argv = build_bwrap_argv(worktree, toolchain, inner)
    ops, command = _parse_bwrap_argv(argv)

    rw_binds = [args for flag, args in ops if flag == "--bind"]
    assert rw_binds == [(str(worktree), str(worktree))]

    ro_binds = [args for flag, args in ops if flag == "--ro-bind"]
    assert ro_binds == [("/usr", "/usr"), ("/opt/venv", "/opt/venv")]

    tmpfs = [args for flag, args in ops if flag == "--tmpfs"]
    assert tmpfs == [("/tmp",)]

    # 不得出現任何其他 bind 類旗標（dev-bind、*-try 等）
    forbidden = {"--dev-bind", "--dev-bind-try", "--bind-try", "--ro-bind-try"}
    assert not [flag for flag, _ in ops if flag in forbidden]

    # /proc 只能是新鮮 procfs（--proc），不得 bind host view
    proc_mounts = [args for flag, args in ops if flag == "--proc"]
    assert proc_mounts == [("/proc",)]

    assert command == inner


def test_build_bwrap_argv_namespace_flags():
    argv = build_bwrap_argv(Path("/work/wt"), [Path("/usr")], ["true"])
    assert "--unshare-net" in argv
    assert "--unshare-pid" in argv
    assert "--die-with-parent" in argv


def test_build_bwrap_argv_env_whitelist_only_four_keys():
    argv = build_bwrap_argv(Path("/work/wt"), [Path("/usr")], ["true"])
    ops, _ = _parse_bwrap_argv(argv)

    assert "--clearenv" in argv
    setenv_ops = [args for flag, args in ops if flag == "--setenv"]
    keys = [key for key, _value in setenv_ops]
    assert sorted(keys) == ["LANG", "LC_ALL", "PATH", "TMPDIR"]

    # --clearenv 必須先於所有 --setenv（bwrap 依序處理 env 操作）
    assert argv.index("--clearenv") < argv.index("--setenv")

    # 不得洩漏 host env（HOME、PYTHONPATH 等一律不得出現）
    assert not [key for key, _ in setenv_ops if key in ("HOME", "PYTHONPATH", "USER")]

    # 不得有 --unsetenv 混搭（clearenv 已全清）
    assert not [flag for flag, _ in ops if flag == "--unsetenv"]


# ---------------------------------------------------------------------------
# IsolationRunner.run：fake bwrap 注入
# ---------------------------------------------------------------------------


def test_run_returns_execution_via_fake_bwrap(tmp_path: Path, worktree: Path):
    """fake bwrap 直接執行 `--` 之後的 command，驗 run() 的組裝與回傳欄位。"""
    args_file = tmp_path / "recorded_args.json"
    fake = _write_fake_bwrap(
        tmp_path / "fake-bwrap",
        f"""
        import json, subprocess, sys
        args = sys.argv[1:]
        json.dump(args, open({str(args_file)!r}, "w"))
        cmd = args[args.index("--") + 1:]
        raise SystemExit(subprocess.call(cmd))
        """,
    )
    runner = IsolationRunner(worktree, [Path("/usr")], bwrap_path=fake)
    ex = runner.run(
        [
            "/usr/bin/python3",
            "-c",
            "import sys; sys.stdout.write('out'); sys.stderr.write('err'); sys.exit(3)",
        ],
        cwd=worktree,
        timeout_s=30.0,
    )

    assert isinstance(ex, Execution)
    assert ex.exit_code == 3
    assert ex.stdout == "out"
    assert ex.stderr == "err"
    assert ex.timed_out is False
    assert ex.wall_ms >= 0
    assert ex.cpu_ms >= 0

    # run() 必須走 build_bwrap_argv 的沙箱參數（唯一 seam 不可繞過）
    recorded = json.load(open(args_file))
    assert "--unshare-net" in recorded
    assert "--unshare-pid" in recorded
    assert "--die-with-parent" in recorded
    assert "--clearenv" in recorded
    ops, _ = _parse_bwrap_argv([str(fake), *recorded])
    chdirs = [args for flag, args in ops if flag == "--chdir"]
    assert chdirs == [(str(worktree),)]


def test_run_timeout_kills_child(tmp_path: Path, worktree: Path):
    pid_file = tmp_path / "child.pid"
    fake = _write_fake_bwrap(
        tmp_path / "fake-bwrap-sleeper",
        f"""
        import os, time
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        time.sleep(60)
        """,
    )
    runner = IsolationRunner(worktree, [Path("/usr")], bwrap_path=fake)
    ex = runner.run(["ignored"], cwd=worktree, timeout_s=0.8)

    assert ex.timed_out is True
    assert ex.exit_code != 0
    assert ex.wall_ms >= 500

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_run_rejects_cwd_outside_worktree(tmp_path: Path, worktree: Path):
    fake = _write_fake_bwrap(tmp_path / "fake-bwrap", "raise SystemExit(0)\n")
    runner = IsolationRunner(worktree, [Path("/usr")], bwrap_path=fake)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(ValueError):
        runner.run(["true"], cwd=outside, timeout_s=5.0)


# ---------------------------------------------------------------------------
# capabilities：fail-closed 探測與快取
# ---------------------------------------------------------------------------


def test_capabilities_probe_failure_returns_all_false(tmp_path: Path, worktree: Path):
    failing = _write_fake_bwrap(tmp_path / "fake-bwrap-fail", "raise SystemExit(1)\n")
    runner = IsolationRunner(worktree, [Path("/usr")], bwrap_path=failing)
    assert runner.capabilities() == Capabilities(
        mount_ns=False, net_ns=False, pid_ns=False
    )


def test_capabilities_missing_bwrap_returns_all_false(tmp_path: Path, worktree: Path):
    runner = IsolationRunner(
        worktree, [Path("/usr")], bwrap_path=tmp_path / "no-such-bwrap"
    )
    assert runner.capabilities() == Capabilities(
        mount_ns=False, net_ns=False, pid_ns=False
    )


def test_capabilities_probe_once_and_cached(tmp_path: Path, worktree: Path):
    counter = tmp_path / "probe_count"
    fake = _write_fake_bwrap(
        tmp_path / "fake-bwrap-count",
        f"""
        open({str(counter)!r}, "a").write("x")
        raise SystemExit(0)
        """,
    )
    runner = IsolationRunner(worktree, [Path("/usr")], bwrap_path=fake)
    first = runner.capabilities()
    second = runner.capabilities()
    assert first == second == Capabilities(mount_ns=True, net_ns=True, pid_ns=True)
    assert counter.read_text() == "x"
