"""Task 2 Step 3：真 bwrap 整合測試（spec §7、acceptance「隔離」第 1 條）。

在真 namespace 內執行探測程式，鎖定：
- 讀取 allowlist 外路徑（模擬 deck `hidden/`、`$HOME`）必須失敗。
- 無網路：connect 立即失敗。
- worktree rw、toolchain ro、PID namespace 生效、timeout 生效。

環境無 bwrap 或 namespace 能力不足時整檔 skip。
"""

import textwrap
from pathlib import Path

import pytest

from patchmud.sandbox.isolate import IsolationRunner

_BWRAP = Path("/usr/bin/bwrap")
_PYTHON = "/usr/bin/python3"
_TOOLCHAIN = [Path("/usr")]


@pytest.fixture()
def worktree(tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "marker.txt").write_text("worktree marker\n")
    return wt


@pytest.fixture()
def runner(worktree: Path) -> IsolationRunner:
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    r = IsolationRunner(worktree, _TOOLCHAIN, bwrap_path=_BWRAP)
    caps = r.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")
    return r


def _run_probe(runner: IsolationRunner, worktree: Path, code: str):
    return runner.run(
        [_PYTHON, "-c", textwrap.dedent(code)], cwd=worktree, timeout_s=60.0
    )


def test_reads_outside_allowlist_fail(runner, worktree, tmp_path):
    """模擬 deck hidden/ 與 $HOME：allowlist 外路徑在沙箱內必須讀不到。"""
    hidden = tmp_path / "deck" / "hidden"
    hidden.mkdir(parents=True)
    secret = hidden / "reference.patch"
    secret.write_text("SECRET-REFERENCE-PATCH\n")
    home = Path.home()

    ex = _run_probe(
        runner,
        worktree,
        f"""
        import os
        leaks = []
        for path in [{str(secret)!r}, {str(hidden)!r}, {str(home)!r}]:
            try:
                os.stat(path)
                leaks.append(path)
            except OSError:
                pass
        print("LEAK" if leaks else "SEALED", leaks)
        """,
    )
    assert ex.exit_code == 0, ex.stderr
    assert ex.timed_out is False
    assert "SEALED" in ex.stdout
    assert "LEAK " not in ex.stdout


def test_worktree_rw_toolchain_ro(runner, worktree):
    ex = _run_probe(
        runner,
        worktree,
        """
        import sys
        # worktree（cwd）可讀寫
        assert open("marker.txt").read().startswith("worktree marker")
        open("written_inside.txt", "w").write("from sandbox")
        # toolchain 唯讀
        try:
            open("/usr/patchmud_probe.txt", "w")
        except OSError:
            print("RO-OK")
            sys.exit(0)
        print("RW-LEAK")
        sys.exit(1)
        """,
    )
    assert ex.exit_code == 0, ex.stderr
    assert "RO-OK" in ex.stdout
    # rw bind 真的落地到 host worktree
    assert (worktree / "written_inside.txt").read_text() == "from sandbox"


def test_no_network_connect_fails_immediately(runner, worktree):
    ex = _run_probe(
        runner,
        worktree,
        """
        import socket, sys
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(("1.1.1.1", 80))
        except OSError as e:
            print("NETFAIL", type(e).__name__)
            sys.exit(0)
        print("CONNECTED")
        sys.exit(1)
        """,
    )
    assert ex.exit_code == 0, ex.stderr
    assert "NETFAIL" in ex.stdout
    # 立即失敗（unreachable），不是等到 timeout
    assert ex.wall_ms < 5000


def test_pid_namespace_isolated(runner, worktree):
    ex = _run_probe(
        runner,
        worktree,
        """
        import os
        print("PID", os.getpid())
        procs = [p for p in os.listdir("/proc") if p.isdigit()]
        print("NPROC", len(procs))
        """,
    )
    assert ex.exit_code == 0, ex.stderr
    lines = dict(line.split() for line in ex.stdout.splitlines())
    assert int(lines["PID"]) <= 10
    assert int(lines["NPROC"]) <= 10


def test_timeout_inside_real_bwrap(runner, worktree):
    ex = runner.run(
        [_PYTHON, "-c", "import time; time.sleep(60)"],
        cwd=worktree,
        timeout_s=1.0,
    )
    assert ex.timed_out is True
    assert ex.exit_code != 0
