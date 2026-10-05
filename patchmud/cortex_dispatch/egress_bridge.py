"""沙箱內的 egress bridge：127.0.0.1:<port> → host egress proxy 的 Unix socket，再執行命令。

builder lane 的外層 bwrap 以 ``--unshare-net`` 拿掉 host 網路；codex 與模型下的指令
只能經 ``HTTPS_PROXY=http://127.0.0.1:<port>`` 走這支 bridge 抵達 host 端的 allowlist
proxy（:mod:`patchmud.cortex_dispatch.sandbox`）。本檔在沙箱內以 ``python3 -I`` 獨立
執行，只用標準函式庫，不 import patchmud。

用法：``python3 -I egress_bridge.py <unix_socket> <port> <command> [args...]``
"""

from __future__ import annotations

import select
import socket
import subprocess
import sys
import threading

_IDLE_TIMEOUT_S = 900


def _pipe(left: socket.socket, right: socket.socket) -> None:
    try:
        while True:
            readable, _, _ = select.select([left, right], [], [], _IDLE_TIMEOUT_S)
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


def _serve(server: socket.socket, unix_path: str) -> None:
    while True:
        try:
            client, _ = server.accept()
        except OSError:
            return
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            upstream.connect(unix_path)
        except OSError:
            client.close()
            upstream.close()
            continue
        threading.Thread(target=_pipe, args=(client, upstream), daemon=True).start()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: egress_bridge.py <unix_socket> <port> <command> [args...]", file=sys.stderr)
        return 2
    unix_path, port, command = argv[0], int(argv[1]), argv[2:]
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)
    threading.Thread(target=_serve, args=(server, unix_path), daemon=True).start()
    try:
        return subprocess.call(command, stdin=subprocess.DEVNULL)
    finally:
        server.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
