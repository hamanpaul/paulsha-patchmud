"""Namespace 隔離執行器（spec §7，F1 修正後）。

`IsolationRunner` 是全系統唯一執行 candidate code 的 seam（plan invariant 3）：
probe、import smoke、compile、lint、hidden evaluator 一律經此進入 bubblewrap
（mount + network + PID namespace）沙箱。

bind allowlist 預設只含三項：worktree（rw）、Python toolchain（ro）、新鮮 tmpfs
`/tmp`；native provider 的 runtime/auth 路徑必須由呼叫端明確映射。
deck 目錄、runs store、engine 程式碼、`$HOME`、host `/proc` view 一律不得進入，
除非 native 呼叫端明確提供窄化的 bind mapping。環境變數 sanitized：預設只留
`PATH/LANG/LC_ALL/TMPDIR` 四鍵白名單，額外 key 也必須明確傳入且不得是 loader
injection 或 `TYPESAFE_API_KEY`。

namespace 能力以一次性 `bwrap --unshare-all` 探測並快取；探測失敗回全 False，
由上層（ranked / pilot gate）fail-closed 拒絕啟動。
"""

from __future__ import annotations

import os
import resource
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
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

# Explicit native runtime state is allowed, but environment hooks that can
# inject code into the provider process remain forbidden.  Credentials are
# also intentionally excluded: native providers receive only their explicitly
# mapped OAuth/config state, never the generic TypeSafe API key.
_FORBIDDEN_SANDBOX_ENV_KEYS = {
    "TYPESAFE_API_KEY",
    "PYTHONPATH",
    "PYTHONHOME",
    "PERL5LIB",
    "PERL5OPT",
    "RUBYLIB",
    "RUBYOPT",
    "NODE_OPTIONS",
    "BASH_ENV",
    "ENV",
}

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


def _validated_sandbox_env(
    sandbox_env: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    """Validate and preserve explicitly requested native runtime variables."""

    if sandbox_env is None:
        return ()
    validated: list[tuple[str, str]] = []
    for key, value in sandbox_env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("sandbox_env keys and values must be strings")
        upper_key = key.upper()
        if (
            upper_key == "TYPESAFE_API_KEY"
            or upper_key in _FORBIDDEN_SANDBOX_ENV_KEYS
            or upper_key.startswith("LD_")
            or upper_key.startswith("DYLD_")
        ):
            raise ValueError(f"sandbox_env key is forbidden: {key}")
        if "\x00" in key or "\x00" in value:
            raise ValueError(f"sandbox_env key/value contains NUL: {key}")
        validated.append((key, value))
    return tuple(validated)


def build_bwrap_argv(
    worktree: Path,
    toolchain_ro: Sequence[Path],
    argv: Sequence[str],
    *,
    bwrap_path: str | os.PathLike[str] = DEFAULT_BWRAP_PATH,
    chdir: Path | None = None,
    masked_paths: Sequence[Path] = (),
    protected_paths: Sequence[Path] = (),
    writable_paths: Sequence[Path] = (),
    network_access: bool = False,
    ro_bindings: Sequence[tuple[Path, Path]] = (),
    rw_bindings: Sequence[tuple[Path, Path]] = (),
    sandbox_env: Mapping[str, str] | None = None,
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
    ]
    if not network_access:
        out.append("--unshare-net")
    out += ["--unshare-uts", "--clearenv"]
    env_values = dict(_ENV_WHITELIST)
    for key, value in _validated_sandbox_env(sandbox_env):
        env_values[key] = value
    for key, value in env_values.items():
        out += ["--setenv", key, value]
    out += ["--tmpfs", "/tmp"]
    for target, link in _USR_MERGE_SYMLINKS:
        out += ["--symlink", target, link]
    for path in toolchain_ro:
        out += ["--ro-bind", str(path), str(path)]
    out += ["--bind", str(worktree), str(worktree)]
    for source, target in ro_bindings:
        out += ["--ro-bind", str(source), str(target)]
    for source, target in rw_bindings:
        out += ["--bind", str(source), str(target)]
    # Host-side Workspace subsequently invokes Git.  Scoring protects its
    # metadata and original tests against writes made by candidate code, too.
    for path in protected_paths:
        canonical = Path(path).resolve()
        if not canonical.is_relative_to(worktree.resolve()):
            raise ValueError('protected path must be within worktree')
        out += ["--ro-bind", str(canonical), str(canonical)]
    for path in writable_paths:
        canonical = Path(path).resolve()
        if not any(canonical.is_relative_to(Path(root).resolve()) for root in protected_paths):
            raise ValueError('writable exception must be within a protected path')
        out += ["--bind", str(canonical), str(canonical)]
    # Wheels can install engine code and private case anchors inside a
    # toolchain's site-packages.  Overlay those directories only after the
    # toolchain mounts; otherwise the read-only parent would reveal them again.
    for path in masked_paths:
        canonical = Path(path).resolve()
        if any(canonical.is_relative_to(Path(root).resolve()) for root in toolchain_ro):
            out += ["--tmpfs", str(canonical)]
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
        extra_ro: Sequence[Path] = (),
        masked_paths: Sequence[Path] = (),
        protected_paths: Sequence[Path] = (),
        writable_paths: Sequence[Path] = (),
        network_access: bool = False,
        ro_bindings: Sequence[tuple[Path, Path]] = (),
        rw_bindings: Sequence[tuple[Path, Path]] = (),
        sandbox_env: Mapping[str, str] | None = None,
    ) -> None:
        self._worktree = Path(worktree)
        self._toolchain_ro = tuple(Path(p) for p in toolchain_ro)
        self._extra_ro = [Path(p) for p in extra_ro]
        self._masked_paths = tuple(Path(p) for p in masked_paths)
        self._protected_paths = tuple(Path(p) for p in protected_paths)
        self._writable_paths = tuple(Path(p) for p in writable_paths)
        self._network_access = bool(network_access)
        self._ro_bindings = tuple(
            (Path(source), Path(target)) for source, target in ro_bindings
        )
        self._rw_bindings = tuple(
            (Path(source), Path(target)) for source, target in rw_bindings
        )
        self._sandbox_env = dict(sandbox_env or {})
        _validated_sandbox_env(self._sandbox_env)
        self._bwrap_path = str(bwrap_path)

    def add_ro_bind(self, path: Path) -> None:
        """追加 read-only bind；evaluator 用來把 hidden 掛在 candidate 樹之外（F1）。"""
        self._extra_ro.append(Path(path))

    def run(
        self,
        argv: list[str],
        cwd: Path,
        timeout_s: float,
        input_text: str | None = None,
    ) -> Execution:
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
            (*self._toolchain_ro, *self._extra_ro),
            argv,
            bwrap_path=self._bwrap_path,
            chdir=cwd,
            masked_paths=self._masked_paths,
            protected_paths=self._protected_paths,
            writable_paths=self._writable_paths,
            network_access=self._network_access,
            ro_bindings=self._ro_bindings,
            rw_bindings=self._rw_bindings,
            sandbox_env=self._sandbox_env,
        )

        rusage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.monotonic()
        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": _HOST_ENV,
            "start_new_session": True,
        }
        if input_text is not None:
            popen_kwargs["stdin"] = subprocess.PIPE
        proc = subprocess.Popen(full_argv, **popen_kwargs)
        timed_out = False
        stdout = ""
        stderr = ""

        def make_execution() -> Execution:
            wall_ms = round((time.monotonic() - started) * 1000)
            rusage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_s = (rusage_after.ru_utime - rusage_before.ru_utime) + (
                rusage_after.ru_stime - rusage_before.ru_stime
            )
            exit_code = getattr(proc, "returncode", None)
            if exit_code is None:
                # Minimal fake processes used by unit tests need not expose a
                # Popen returncode; real Popen always does after reap.
                exit_code = -1
            return Execution(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                wall_ms=wall_ms,
                cpu_ms=round(cpu_s * 1000),
                timed_out=timed_out,
            )

        try:
            if input_text is None:
                stdout, stderr = proc.communicate(timeout=timeout_s)
            else:
                stdout, stderr = proc.communicate(
                    input=input_text, timeout=timeout_s
                )
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc.pid)
            stdout, stderr = proc.communicate()
        except BaseException as exc:
            # Scoring cancellation must not leave candidate descendants alive
            # after its workspace or evidence writer has been torn down.
            _kill_process_group(proc.pid)
            try:
                stdout, stderr = proc.communicate()
            except BaseException:
                # Keep the original cancellation exception and attach whatever
                # output was available before the reap itself was interrupted.
                pass
            try:
                setattr(exc, "execution_result", make_execution())
            except (AttributeError, TypeError):
                # BaseException instances normally have a __dict__; preserve
                # the legacy re-raise contract even for unusual exceptions.
                pass
            raise
        return make_execution()

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
