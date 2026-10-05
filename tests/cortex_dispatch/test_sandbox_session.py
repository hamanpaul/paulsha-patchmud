"""paulsha-cortex#842：builder lane 外層邊界、egress allowlist 與 codex 事件解析。"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from patchmud.cortex_dispatch.lane import _boundary_is_workspace_write
from patchmud.cortex_dispatch.sandbox import (
    SANDBOX_CODEX_HOME,
    SANDBOX_WORKSPACE,
    AgentSandboxSpec,
    EgressProxy,
    SandboxError,
    build_agent_bwrap_argv,
    codex_runtime_layout,
    egress_host_allowed,
)
from patchmud.cortex_dispatch.session import observe_thread, parse_session


def _spec(tmp_path: Path) -> AgentSandboxSpec:
    for name in ("ws", "codex-home", "runtime"):
        (tmp_path / name).mkdir(exist_ok=True)
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    return AgentSandboxSpec(
        workspace=tmp_path / "ws",
        codex_home=tmp_path / "codex-home",
        auth_file=auth,
        runtime_ro=(tmp_path / "runtime",),
        toolchain_ro=(tmp_path / "site",),
        path_dirs=(str(tmp_path / "runtime" / "bin"),),
        pythonpath=(str(tmp_path / "site"),),
    )


def _pairs(argv: list[str], flag: str, width: int = 2) -> list[tuple[str, ...]]:
    return [tuple(argv[i + 1 : i + 1 + width]) for i, token in enumerate(argv) if token == flag]


def test_bwrap_argv_confines_writes_and_mounts_auth_read_only(tmp_path):
    spec = _spec(tmp_path)
    argv = build_agent_bwrap_argv(spec, ["codex", "exec"], egress_dir=tmp_path / "egress")
    head = argv[: argv.index("--")]
    assert {"--unshare-net", "--unshare-user", "--unshare-pid", "--clearenv", "--die-with-parent"} <= set(head)
    assert _pairs(head, "--bind") == [
        (str(spec.workspace), SANDBOX_WORKSPACE),
        (str(spec.codex_home), SANDBOX_CODEX_HOME),
        (str(tmp_path / "egress"), "/run/patchmud-egress"),
    ]
    ro = dict(_pairs(head, "--ro-bind"))
    assert ro[str(spec.auth_file)] == f"{SANDBOX_CODEX_HOME}/auth.json"
    assert ro["/usr"] == "/usr" and ro["/etc"] == "/etc"
    assert _pairs(head, "--tmpfs", 1) == [("/tmp",)]
    env = dict(_pairs(head, "--setenv"))
    assert env["CODEX_HOME"] == SANDBOX_CODEX_HOME
    assert env["HOME"] == "/tmp/home"
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:3128"
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["PYTHONPATH"] == str(tmp_path / "site")
    tail = argv[argv.index("--") + 1 :]
    assert tail[:3] == ["python3", "-I", "/opt/patchmud/egress_bridge.py"]
    assert tail[-2:] == ["codex", "exec"]


def test_bwrap_argv_without_egress_has_no_network_and_no_proxy(tmp_path):
    argv = build_agent_bwrap_argv(_spec(tmp_path), ["codex", "app-server"], egress_dir=None)
    head = argv[: argv.index("--")]
    assert "--unshare-net" in head
    assert "HTTPS_PROXY" not in head
    assert argv[argv.index("--") + 1 :] == ["codex", "app-server"]


def test_boundary_check_accepts_only_contract_mounts(tmp_path):
    spec = _spec(tmp_path)
    deck = tmp_path / "decks" / "demo" / "encounter"
    deck.mkdir(parents=True)
    argv = build_agent_bwrap_argv(spec, ["codex"], egress_dir=tmp_path / "egress")
    head = argv[: argv.index("--") + 1]
    assert _boundary_is_workspace_write(head, spec, forbidden=(deck,))

    extra_rw = head[:-1] + ["--bind", str(tmp_path / "ws"), "/elsewhere", "--"]
    assert not _boundary_is_workspace_write(extra_rw, spec, forbidden=(deck,))

    exposes_deck = head[:-1] + ["--ro-bind", str(tmp_path / "decks"), str(tmp_path / "decks"), "--"]
    assert not _boundary_is_workspace_write(exposes_deck, spec, forbidden=(deck,))

    with_net = [token for token in head if token != "--unshare-net"]
    assert not _boundary_is_workspace_write(with_net, spec, forbidden=(deck,))
    assert not _boundary_is_workspace_write((), spec)


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("chatgpt.com", True),
        ("ab.chatgpt.com", True),
        ("api.openai.com", True),
        ("CHATGPT.COM.", True),
        ("raw.githubusercontent.com", False),
        ("github.com", False),
        ("evilchatgpt.com", False),
        ("chatgpt.com.evil.test", False),
        ("sdmntprwestus.oaiusercontent.com", False),
        ("", False),
        ("user@chatgpt.com", False),
    ],
)
def test_egress_allowlist_matches_exact_host_or_subdomain(host, allowed):
    assert egress_host_allowed(host) is allowed


def _connect(proxy: EgressProxy, request: bytes) -> bytes:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(10)
    client.connect(str(proxy.directory / "proxy.sock"))
    client.sendall(request)
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = client.recv(4096)
        if not chunk:
            break
        response += chunk
    client.close()
    return response


def test_egress_proxy_denies_hosts_outside_allowlist_without_connecting():
    with EgressProxy() as proxy:
        denied = _connect(proxy, b"CONNECT raw.githubusercontent.com:443 HTTP/1.1\r\nHost: x\r\n\r\n")
        wrong_port = _connect(proxy, b"CONNECT chatgpt.com:80 HTTP/1.1\r\n\r\n")
        plain = _connect(proxy, b"GET http://chatgpt.com/ HTTP/1.1\r\n\r\n")
        allowed, rejected = proxy.snapshot()
        directory = proxy.directory
    assert denied.startswith(b"HTTP/1.1 403")
    assert wrong_port.startswith(b"HTTP/1.1 403")
    assert plain.startswith(b"HTTP/1.1 405")
    assert allowed == {}
    assert rejected == {"raw.githubusercontent.com": 1, "chatgpt.com": 1, "<non-connect>": 1}
    assert not directory.exists()


def test_codex_runtime_layout_for_native_binary(tmp_path, monkeypatch):
    binary = tmp_path / "toolchain" / "bin" / "codex"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\x7fELF" + b"\0" * 32)
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{binary.parent}:/usr/bin:/bin")
    invoke, roots, path_dirs = codex_runtime_layout("codex")
    assert invoke == str(binary)
    assert roots == (binary.parent,)
    assert path_dirs == (str(binary.parent),)


def test_codex_runtime_layout_for_node_wrapper(tmp_path, monkeypatch):
    prefix = tmp_path / "node"
    package = prefix / "lib" / "node_modules" / "@openai" / "codex"
    (package / "bin").mkdir(parents=True)
    entry = package / "bin" / "codex.js"
    entry.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    entry.chmod(0o755)
    (prefix / "bin").mkdir()
    (prefix / "bin" / "codex").symlink_to(entry)
    node = prefix / "bin" / "node"
    node.write_bytes(b"\x7fELF")
    node.chmod(0o755)
    monkeypatch.setenv("PATH", f"{prefix / 'bin'}:/usr/bin:/bin")
    invoke, roots, path_dirs = codex_runtime_layout("codex")
    assert invoke == str(prefix / "bin" / "codex")
    assert roots == (prefix.resolve(),)
    assert path_dirs == (str(prefix / "bin"),)


def test_codex_runtime_layout_requires_codex(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SandboxError):
        codex_runtime_layout("codex")


def _jsonl(*events: dict) -> str:
    return "noise line\n" + "\n".join(json.dumps(event) for event in events) + "\n"


USAGE = {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 3, "reasoning_output_tokens": 1}


def test_parse_session_extracts_thread_usage_and_commands():
    transcript = parse_session(
        _jsonl(
            {"type": "thread.started", "thread_id": "01a10a88-f0da-7062"},
            {"type": "item.completed", "item": {"type": "command_execution", "command": "git commit -m x"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "last"}},
            {"type": "turn.completed", "usage": USAGE},
        )
    )
    assert transcript.thread_id == "01a10a88-f0da-7062"
    assert transcript.usage == USAGE
    assert transcript.last_message == "last"
    assert transcript.command_count == 1
    assert transcript.failures == ()


def test_parse_session_fails_closed_on_ambiguous_thread_or_usage():
    transcript = parse_session(
        _jsonl(
            {"type": "thread.started", "thread_id": "thread-aaaaaaaa"},
            {"type": "thread.started", "thread_id": "thread-bbbbbbbb"},
            {"type": "turn.completed", "usage": USAGE},
            {"type": "turn.completed", "usage": USAGE},
            {"type": "turn.failed", "error": {"message": "boom"}},
        )
    )
    assert transcript.thread_id is None
    assert transcript.usage is None
    assert transcript.failures


def test_observe_thread_requires_matching_openai_identity():
    thread = {"id": "thread-aaaaaaaa", "model": "gpt-6-luna", "reasoningEffort": "max", "modelProvider": "openai"}
    observed = observe_thread("thread-aaaaaaaa", lambda _tid: thread)
    assert (observed.model_id, observed.effort) == ("gpt-6-luna", "max")
    assert observed.evidence[0]["thread_sha256"] != "thread-aaaaaaaa"

    assert observe_thread(None, lambda _tid: thread).model_id is None
    assert observe_thread("thread-aaaaaaaa", None).model_id is None
    mismatched = observe_thread("thread-aaaaaaaa", lambda _tid: {**thread, "id": "other-thread"})
    assert mismatched.model_reason == "thread-identity-incomplete"
    foreign = observe_thread("thread-aaaaaaaa", lambda _tid: {**thread, "modelProvider": "azure"})
    assert (foreign.model_id, foreign.model_reason) == (None, "unexpected-model-provider")

    def broken(_tid):
        raise RuntimeError("app-server down")

    assert observe_thread("thread-aaaaaaaa", broken).model_reason == "thread-read-failed:RuntimeError"
