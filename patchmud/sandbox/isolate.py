"""Namespace 隔離執行器（spec §7，F1 修正後）。

`IsolationRunner` 是全系統唯一執行 candidate code 的 seam（plan invariant 3）：
probe、import smoke、compile、lint、hidden evaluator 一律經此進入 bubblewrap
（mount + network + PID namespace）沙箱。

bind allowlist 只含三項：worktree（rw）、Python toolchain（ro）、新鮮 tmpfs `/tmp`。
deck 目錄、runs store、engine 程式碼、`$HOME`、host `/proc` view 一律不得進入。
環境變數 sanitized：只留 `PATH/LANG/LC_ALL/TMPDIR` 四鍵白名單。

namespace 能力以一次性 `bwrap --unshare-all` 探測並快取；探測失敗回全 False，
由上層（ranked / pilot gate）fail-closed 拒絕啟動。
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Capabilities",
    "Execution",
    "IsolationRunner",
    "build_bwrap_argv",
    "DEFAULT_BWRAP_PATH",
]

DEFAULT_BWRAP_PATH = "/usr/bin/bwrap"

# spec §7：環境變數 sanitized，只留四鍵白名單；值 pin 死，杜絕 host env 洩漏。
_ENV_WHITELIST: tuple[tuple[str, str], ...] = (
    ("PATH", "/usr/bin:/bin"),
    ("LANG", "C.UTF-8"),
    ("LC_ALL", "C.UTF-8"),
    ("TMPDIR", "/tmp"),
)

# usr-merge 相容 symlink（/bin → usr/bin 等）：symlink 非 bind，allowlist 不變。
_USR_MERGE_SYMLINKS: tuple[tuple[str, str], ...] = (
    ("usr/bin", "/bin"),
    ("usr/sbin", "/sbin"),
    ("usr/lib", "/lib"),
    ("usr/lib64", "/lib64"),
)

# bwrap 本體所需的最小 env（沙箱內 env 由 --clearenv/--setenv 控制）。
_HOST_ENV: dict[str, str] = {"PATH": "/usr/bin:/bin"}

# capabilities 探測結果快取（key: bwrap 路徑字串）
_capabilities_cache: dict[str, "Capabilities"] = {}


@dataclass(frozen=True)
class Execution:
    """一次沙箱內執行的觀測結果。"""

    exit_code: int
    stdout: str
    stderr: str
    wall_ms: int
    cpu_ms: int
    timed_out: bool


@dataclass(frozen=True)
class Capabilities:
    """namespace 能力探測結果；任一 False 即 degraded（§7 fail-closed 依據）。"""

    mount_ns: bool
    net_ns: bool
    pid_ns: bool


def build_bwrap_argv(
    worktree: Path,
    toolchain_ro: Sequence[Path],
    argv: Sequence[str],
    *,
    bwrap_path: str | os.PathLike[str] = DEFAULT_BWRAP_PATH,
    chdir: Path | None = None,
) -> list[str]:
    """組裝 bubblewrap argv：bind allowlist + namespace 旗標 + env 白名單。

    掛載順序：tmpfs `/tmp` 最先，worktree / toolchain 之後才 bind——
    即使它們位於 host `/tmp` 之下也不會被新鮮 tmpfs 遮蔽。
    """
    out: list[str] = [
        str(bwrap_path),
        "--die-with-parent",
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--clearenv",
    ]
    for key, value in _ENV_WHITELIST:
        out += ["--setenv", key, value]
    out += ["--tmpfs", "/tmp"]
    for target, link in _USR_MERGE_SYMLINKS:
        out += ["--symlink", target, link]
    for path in toolchain_ro:
        out += ["--ro-bind", str(path), str(path)]
    out += ["--bind", str(worktree), str(worktree)]
    out += ["--proc", "/proc", "--dev", "/dev"]
    if chdir is not None:
        out += ["--chdir", str(chdir)]
    out.append("--")
    out.extend(argv)
    return out


class IsolationRunner:
    """執行 candidate code 的唯一 seam；unit tests 以 fake bwrap 注入。"""

    def __init__(
        self,
        worktree: Path,
        toolchain_ro: Sequence[Path] = (Path("/usr"),),
        *,
        bwrap_path: str | os.PathLike[str] = DEFAULT_BWRAP_PATH,
    ) -> None:
        self._worktree = Path(worktree)
        self._toolchain_ro = tuple(Path(p) for p in toolchain_ro)
        self._bwrap_path = str(bwrap_path)

    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
        """在沙箱內執行 argv；timeout 時強制終止整個 process group。"""
        cwd = Path(cwd)
        resolved_cwd = cwd.resolve()
        resolved_wt = self._worktree.resolve()
        if not (
            resolved_cwd == resolved_wt or resolved_cwd.is_relative_to(resolved_wt)
        ):
            raise ValueError(f"cwd 必須位於 worktree 內：{cwd}")

        full_argv = build_bwrap_argv(
            self._worktree,
            self._toolchain_ro,
            argv,
            bwrap_path=self._bwrap_path,
            chdir=cwd,
        )

        rusage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        proc = subprocess.Popen(
            full_argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_HOST_ENV,
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc.pid)
            stdout, stderr = proc.communicate()
        wall_ms = round((time.monotonic() - started) * 1000)
        rusage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu_s = (rusage_after.ru_utime - rusage_before.ru_utime) + (
            rusage_after.ru_stime - rusage_before.ru_stime
        )
        return Execution(
            exit_code=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            wall_ms=wall_ms,
            cpu_ms=round(cpu_s * 1000),
            timed_out=timed_out,
        )

    def capabilities(self) -> Capabilities:
        """一次性探測 namespace 能力並快取；任何失敗回全 False。"""
        cached = _capabilities_cache.get(self._bwrap_path)
        if cached is None:
            cached = _probe_capabilities(self._bwrap_path)
            _capabilities_cache[self._bwrap_path] = cached
        return cached


def _probe_capabilities(bwrap_path: str) -> Capabilities:
    probe_argv = [bwrap_path, "--unshare-all", "--die-with-parent"]
    for target, link in _USR_MERGE_SYMLINKS:
        probe_argv += ["--symlink", target, link]
    probe_argv += ["--ro-bind", "/usr", "/usr", "--", "/usr/bin/true"]
    try:
        result = subprocess.run(
            probe_argv,
            capture_output=True,
            timeout=30,
            env=_HOST_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Capabilities(mount_ns=False, net_ns=False, pid_ns=False)
    ok = result.returncode == 0
    return Capabilities(mount_ns=ok, net_ns=ok, pid_ns=ok)


def _kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
