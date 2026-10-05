"""builder lane 的外層邊界：bwrap workspace-write 沙箱＋egress allowlist proxy。

Cortex 對 ``builder-workspace-write`` 這一格發 ``codex --sandbox danger-full-access``，
``workspace-write`` 契約由外層邊界保證（systemd unit＋egress proxy）。PatchMUD 以同一
形狀實現：

- **可寫**只有 agent workspace（``/workspace/repo``）、這場 run 專用的 ``CODEX_HOME``
  （``/codex-home``）與新鮮 tmpfs ``/tmp``；``/usr``、``/etc``、codex／node 安裝目錄、
  pytest site 唯讀；deck（含 ``hidden/``）、runs store、PatchMUD repo 與使用者 HOME
  一律不掛入。
- ``auth.json`` 以 ``--ro-bind`` 唯讀掛入專用 ``CODEX_HOME``：codex 不讀使用者的
  ``~/.codex``（AGENTS.md、hooks、memories 都會污染條件；relay hook 甚至會碰 live
  系統），也不可能改寫使用者憑證。
- ``--unshare-net``：沙箱沒有 host 網路。codex 與模型下的指令只能經沙箱內的
  :mod:`egress_bridge` 抵達 host 端 :class:`EgressProxy`，只放行 allowlist 內 host
  的 443 CONNECT（預設 ``chatgpt.com``／``openai.com`` 與其子網域）。PatchMUD repo 是
  public，這條管制是 hidden 資產「永不進 sandbox」在網路面的保證。
"""

from __future__ import annotations

import os
import select
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DEFAULT_EGRESS_ALLOWLIST",
    "EGRESS_PORT",
    "SANDBOX_CODEX_HOME",
    "SANDBOX_LAST_MESSAGE",
    "SANDBOX_WORKSPACE",
    "AgentOutcome",
    "AgentSandboxSpec",
    "EgressProxy",
    "SandboxError",
    "build_agent_bwrap_argv",
    "codex_runtime_layout",
    "egress_host_allowed",
    "run_agent_in_sandbox",
]

SANDBOX_WORKSPACE = "/workspace/repo"
SANDBOX_CODEX_HOME = "/codex-home"
SANDBOX_LAST_MESSAGE = f"{SANDBOX_CODEX_HOME}/patchmud-last-message.json"
SANDBOX_HOME = "/tmp/home"
SANDBOX_EGRESS_DIR = "/run/patchmud-egress"
SANDBOX_BRIDGE = "/opt/patchmud/egress_bridge.py"
EGRESS_SOCKET_NAME = "proxy.sock"
EGRESS_PORT = 3128
DEFAULT_EGRESS_ALLOWLIST = ("chatgpt.com", "openai.com")
_BRIDGE_SOURCE = Path(__file__).resolve().parent / "egress_bridge.py"
_USR_MERGE_SYMLINKS: tuple[tuple[str, str], ...] = (
    ("usr/bin", "/bin"),
    ("usr/sbin", "/sbin"),
    ("usr/lib", "/lib"),
    ("usr/lib64", "/lib64"),
)
_HOST_ENV = {"PATH": "/usr/bin:/bin"}
_MAX_HEADER_BYTES = 8192
_PROXY_IDLE_S = 900


class SandboxError(RuntimeError):
    """外層沙箱無法依契約建立（fail-closed）。"""


@dataclass(frozen=True)
class AgentSandboxSpec:
    """一場 builder session 的外層邊界（host 路徑）。"""

    workspace: Path
    codex_home: Path
    auth_file: Path
    #: codex／node 安裝根目錄（唯讀，掛在原路徑）。
    runtime_ro: tuple[Path, ...]
    #: 其他唯讀 toolchain（pytest site 等，掛在原路徑）。
    toolchain_ro: tuple[Path, ...] = ()
    #: 沙箱內 PATH 的額外目錄（放在 ``/usr/bin:/bin`` 之前）。
    path_dirs: tuple[str, ...] = ()
    #: 沙箱內 PYTHONPATH（讓 ``python3 -m pytest`` 找得到唯讀掛入的 pytest）。
    pythonpath: tuple[str, ...] = ()

    def rw_mounts(self) -> tuple[tuple[str, str], ...]:
        """(host, sandbox) 可寫 bind；launch observation 以這份清單判定 workspace-write。"""
        return (
            (str(self.workspace), SANDBOX_WORKSPACE),
            (str(self.codex_home), SANDBOX_CODEX_HOME),
        )


@dataclass(frozen=True)
class AgentOutcome:
    """一次 builder session 的觀測結果。"""

    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr_tail: str
    wall_ms: int
    egress_allowed: Mapping[str, int] = field(default_factory=dict)
    egress_denied: Mapping[str, int] = field(default_factory=dict)
    sandbox_argv: tuple[str, ...] = ()


def egress_host_allowed(host: str, allowlist: Sequence[str] = DEFAULT_EGRESS_ALLOWLIST) -> bool:
    name = host.strip().lower().rstrip(".")
    if not name or any(ch in name for ch in "/\\@ "):
        return False
    return any(name == entry or name.endswith("." + entry) for entry in allowlist)


def build_agent_bwrap_argv(
    spec: AgentSandboxSpec,
    command: Sequence[str],
    *,
    bwrap_path: str = "/usr/bin/bwrap",
    egress_dir: Path | None = None,
) -> list[str]:
    """組 builder session 的 bwrap argv；``egress_dir`` 為 None 時沙箱完全沒有網路。"""
    path = ":".join((*spec.path_dirs, "/usr/bin", "/bin"))
    out: list[str] = [
        str(bwrap_path),
        "--die-with-parent",
        "--unshare-user",
        "--unshare-ipc",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--clearenv",
        "--setenv", "PATH", path,
        "--setenv", "HOME", SANDBOX_HOME,
        "--setenv", "CODEX_HOME", SANDBOX_CODEX_HOME,
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "LC_ALL", "C.UTF-8",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "GIT_CONFIG_GLOBAL", "/dev/null",
        "--setenv", "GIT_CONFIG_NOSYSTEM", "1",
    ]
    if spec.pythonpath:
        out += ["--setenv", "PYTHONPATH", ":".join(spec.pythonpath)]
    if egress_dir is not None:
        proxy = f"http://127.0.0.1:{EGRESS_PORT}"
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
            out += ["--setenv", name, proxy]
        out += ["--setenv", "NO_PROXY", "localhost,127.0.0.1"]
    out += ["--tmpfs", "/tmp", "--dir", SANDBOX_HOME]
    for target, link in _USR_MERGE_SYMLINKS:
        out += ["--symlink", target, link]
    out += ["--ro-bind", "/usr", "/usr", "--ro-bind", "/etc", "/etc"]
    for path_item in (*spec.runtime_ro, *spec.toolchain_ro):
        out += ["--ro-bind", str(path_item), str(path_item)]
    for host, target in spec.rw_mounts():
        out += ["--bind", host, target]
    out += ["--ro-bind", str(spec.auth_file), f"{SANDBOX_CODEX_HOME}/auth.json"]
    if egress_dir is not None:
        out += [
            "--ro-bind", str(_BRIDGE_SOURCE), SANDBOX_BRIDGE,
            "--bind", str(egress_dir), SANDBOX_EGRESS_DIR,
        ]
    out += ["--proc", "/proc", "--dev", "/dev", "--chdir", SANDBOX_WORKSPACE, "--"]
    if egress_dir is not None:
        out += [
            "python3", "-I", SANDBOX_BRIDGE,
            f"{SANDBOX_EGRESS_DIR}/{EGRESS_SOCKET_NAME}", str(EGRESS_PORT),
        ]
    out.extend(command)
    return out


def codex_runtime_layout(codex_executable: str) -> tuple[str, tuple[Path, ...], tuple[str, ...]]:
    """解析 codex 安裝：回傳 (沙箱內呼叫路徑, 唯讀掛載根, PATH 目錄)。

    原生 ELF：掛其所在目錄。node 包裝（``#!/usr/bin/env node``）：掛 node 安裝前綴
    （含 ``bin/node`` 與全域 ``lib/node_modules``）與 codex 套件根。
    """
    found = shutil.which(codex_executable)
    if found is None:
        raise SandboxError(f"找不到 codex 可執行檔：{codex_executable!r}")
    invoke = Path(found)
    resolved = invoke.resolve()
    with resolved.open("rb") as handle:
        head = handle.read(128)
    roots: list[Path] = []
    path_dirs: list[str] = [str(invoke.parent)]
    if head.startswith(b"\x7fELF"):
        roots.append(resolved.parent)
    elif head.startswith(b"#!") and b"node" in head.split(b"\n", 1)[0]:
        node = shutil.which("node")
        if node is None:
            raise SandboxError("codex 是 node 包裝，但找不到 node")
        node_path = Path(node)
        prefix = node_path.resolve().parents[1]
        roots.append(prefix)
        path_dirs.append(str(node_path.parent))
        package_root = resolved.parents[1]
        if not package_root.is_relative_to(prefix):
            roots.append(package_root)
    else:
        raise SandboxError("無法辨識 codex 可執行檔格式（不是 ELF 也不是 node 包裝）")
    if not any(invoke.is_relative_to(root) for root in roots):
        roots.append(invoke.parent)
    return str(invoke), tuple(dict.fromkeys(roots)), tuple(dict.fromkeys(path_dirs))


class EgressProxy:
    """host 端 HTTP CONNECT allowlist proxy（Unix socket）；只放行 allowlist host 的 443。"""

    def __init__(self, allowlist: Sequence[str] = DEFAULT_EGRESS_ALLOWLIST) -> None:
        self.allowlist = tuple(allowlist)
        self.allowed: Counter[str] = Counter()
        self.denied: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._dir: Path | None = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None

    @property
    def directory(self) -> Path:
        if self._dir is None:
            raise SandboxError("egress proxy 尚未啟動")
        return self._dir

    def __enter__(self) -> "EgressProxy":
        # AF_UNIX 路徑上限 108 bytes：socket 目錄放在短路徑。
        self._dir = Path(tempfile.mkdtemp(prefix="pm-eg-", dir="/tmp"))
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self._dir / EGRESS_SOCKET_NAME))
        server.listen(64)
        self._server = server
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        if self._server is not None:
            try:
                self._server.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._server.close()
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)

    def snapshot(self) -> tuple[dict[str, int], dict[str, int]]:
        with self._lock:
            return dict(self.allowed), dict(self.denied)

    def _record(self, bucket: Counter[str], host: str) -> None:
        with self._lock:
            bucket[host[:255]] += 1

    def _serve(self) -> None:
        assert self._server is not None
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(30)
        buffer = b""
        try:
            while b"\r\n\r\n" not in buffer:
                chunk = client.recv(4096)
                if not chunk or len(buffer) + len(chunk) > _MAX_HEADER_BYTES:
                    client.close()
                    return
                buffer += chunk
        except OSError:
            client.close()
            return
        request_line = buffer.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = request_line.split()
        if len(parts) != 3 or parts[0] != "CONNECT":
            self._record(self.denied, "<non-connect>")
            self._reply(client, b"405 Method Not Allowed")
            return
        host, _, port = parts[1].rpartition(":")
        if port != "443" or not egress_host_allowed(host, self.allowlist):
            self._record(self.denied, host or parts[1])
            self._reply(client, b"403 Forbidden")
            return
        try:
            upstream = socket.create_connection((host, 443), timeout=30)
        except OSError:
            self._record(self.denied, f"{host}:unreachable")
            self._reply(client, b"502 Bad Gateway")
            return
        self._record(self.allowed, host)
        try:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            rest = buffer.split(b"\r\n\r\n", 1)[1]
            if rest:
                upstream.sendall(rest)
        except OSError:
            client.close()
            upstream.close()
            return
        client.settimeout(None)
        upstream.settimeout(None)
        _splice(client, upstream)

    @staticmethod
    def _reply(client: socket.socket, status: bytes) -> None:
        try:
            client.sendall(b"HTTP/1.1 " + status + b"\r\n\r\n")
        except OSError:
            pass
        client.close()


def _splice(left: socket.socket, right: socket.socket) -> None:
    try:
        while True:
            readable, _, _ = select.select([left, right], [], [], _PROXY_IDLE_S)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                (right if source is left else left).sendall(data)
    except OSError:
        return
    finally:
        left.close()
        right.close()


def run_agent_in_sandbox(
    spec: AgentSandboxSpec,
    command: Sequence[str],
    *,
    timeout_s: float,
    bwrap_path: str = "/usr/bin/bwrap",
    allowlist: Sequence[str] = DEFAULT_EGRESS_ALLOWLIST,
) -> AgentOutcome:
    """在外層沙箱＋egress proxy 下執行 builder session；逾時殺整個 process group。"""
    with EgressProxy(allowlist) as proxy:
        argv = build_agent_bwrap_argv(
            spec, command, bwrap_path=bwrap_path, egress_dir=proxy.directory
        )
        started = time.monotonic()
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
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
            stdout, stderr = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
        wall_ms = round((time.monotonic() - started) * 1000)
        allowed, denied = proxy.snapshot()
    return AgentOutcome(
        exit_code=None if timed_out else process.returncode,
        timed_out=timed_out,
        stdout=stdout,
        stderr_tail=(stderr or "")[-2000:],
        wall_ms=wall_ms,
        egress_allowed=allowed,
        egress_denied=denied,
        sandbox_argv=tuple(argv[: argv.index("--") + 1]),
    )
