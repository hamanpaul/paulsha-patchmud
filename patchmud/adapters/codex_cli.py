"""codex CLI headless adapter（OpenAI，OAuth 登入態；issue #14）。

以 ``codex exec`` 非互動模式執行，認證取自 ``codex login`` 建立的
``~/.codex/auth.json``——不需要 ``OPENAI_API_KEY``。

純補全模式的硬性旗標（見 :mod:`patchmud.adapters.cli_base` 的理由）：

``--sandbox read-only``
    禁止寫入；candidate code 的執行仍只走 ``IsolationRunner``。
``--cd <tmpdir>``
    在臨時空目錄執行，避免沾染 encounter workspace。**不再帶 ``--ephemeral``**
    （paulsha-cortex#842 Gap B）：thread 由 codex 持久化在 ``CODEX_HOME``，事後
    才能用 app-server ``thread/read`` 讀回 provider 記錄的實際 model 與
    reasoningEffort；session 檔不在 encounter workspace，不影響隔離。
``--skip-git-repo-check``
    臨時目錄不是 git repo。
``--ignore-user-config``
    不載入 ``~/.codex/config.toml``（personality、預設 effort、hooks 都會
    污染評測的可重現性）；auth 仍照 ``CODEX_HOME`` 解析。
``model_reasoning_effort=<effort>``
    使用 profile descriptor 驗證後解析出的原生 effort；run 未指定時沿用
    descriptor 明示的 ``high`` 預設值；明確指定則使用該值，不讀 ambient 設定。
``--disable plugins|memories|goals|hooks``
    關閉會把使用者狀態帶進 prompt 的功能。

輸出解析（``--json`` 的 JSONL 事件串）::

    {"type":"item.completed","item":{"type":"agent_message","text":"…"}}
    {"type":"turn.completed","usage":{"input_tokens":…,"output_tokens":…}}

取**最後**一則 ``agent_message`` 當回覆（模型可能先講一句再給動作），
``turn.completed.usage`` 原樣透傳給 ledger 的 ``codex`` mapper。

實際執行條件觀測（observed plane）::

    {"type":"thread.started","thread_id":"…"}

每次呼叫取唯一的 ``thread_id``，交給注入的 ``thread_reader``（正式路徑是
:func:`build_app_server_thread_reader`：``codex app-server`` 的 ``thread/read``）
讀回 provider 持久化的 ``model``／``reasoningEffort``／``modelProvider``。
觀測失敗只讓該條件維持 unknown，不讓回合失敗（評測資料流與身分觀測分離）；
未注入 reader 時（unit tests）一律不啟動任何外部程序。
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping

from patchmud.adapters.base import AdapterError, AdapterResponse
from patchmud.adapters.cli_base import CliModelAdapter, iter_json_objects
from patchmud.adapters.observation import RuntimeObservation

__all__ = [
    "THREAD_IDENTITY_SOURCE",
    "CodexCliAdapter",
    "ThreadReader",
    "build_app_server_thread_reader",
]

#: provenance reference：observed model／effort 的來源協定。
THREAD_IDENTITY_SOURCE = "codex-app-server:thread/read"
#: provider 端的 model provider 必須是 OpenAI（與 Cortex qualification driver 同一判準）。
EXPECTED_MODEL_PROVIDER = "openai"
#: thread_id 格式（與 Cortex qualification driver 同一判準）。
_THREAD_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}")

#: thread_reader callable：thread_id → provider 持久化的 thread metadata。
ThreadReader = Callable[[str], Mapping[str, object]]


class CodexCliAdapter(CliModelAdapter):
    """``codex exec`` 純補全 adapter。"""

    usage_provider = "codex"
    binary_name = "codex"

    #: 會把使用者狀態帶進 prompt 的功能，一律關閉。
    DISABLED_FEATURES = ("plugins", "memories", "goals", "hooks")

    def __init__(
        self,
        model: str = "gpt-5.6-sol",
        *,
        codex_binary: str | None = None,
        workdir: str | None = None,
        thread_reader: ThreadReader | None = None,
        **kwargs,
    ) -> None:
        super().__init__(model, binary=codex_binary, **kwargs)
        self._workdir = workdir
        #: 本次呼叫的臨時工作目錄（只在 complete() 執行期間有值）。
        self._active_workdir: str | None = None
        self._thread_reader = thread_reader
        #: 最近一次 ``_parse`` 取得的唯一 thread id（無或不唯一時為 None）。
        self._last_thread_id: str | None = None
        #: 逐次呼叫的 provider 身分觀測（只存 digest 與回報值）。
        self._thread_observations: list[dict[str, object]] = []

    def complete(self, messages: list[dict]) -> AdapterResponse:
        """未指定 workdir 時，每次呼叫配一個用完即刪的臨時空目錄。

        對局是多回合的，每回合一次 ``complete()``；若只建不刪，``/tmp`` 會逐回合
        累積 ``patchmud-codex-*`` 目錄，長時間 pilot 矩陣會吃光 inode。
        回覆解析成功後才讀 thread 身分；身分觀測失敗不影響回合結果。
        """
        self._last_thread_id = None
        if self._workdir is not None:
            response = super().complete(messages)
        else:
            with tempfile.TemporaryDirectory(prefix="patchmud-codex-") as tmp:
                self._active_workdir = tmp
                try:
                    response = super().complete(messages)
                finally:
                    self._active_workdir = None
        self._observe_thread(self._last_thread_id)
        return response

    def _observe_thread(self, thread_id: str | None) -> None:
        call = len(self._thread_observations) + 1
        if self._thread_reader is None:
            self._thread_observations.append(
                {"call": call, "state": "unavailable", "reason": "thread-reader-not-configured"}
            )
            return
        if thread_id is None:
            self._thread_observations.append(
                {"call": call, "state": "unavailable", "reason": "thread-id-not-unique"}
            )
            return
        try:
            thread = self._thread_reader(thread_id)
        except Exception as exc:  # noqa: BLE001 - 身分觀測 fail-soft，回合不受影響
            self._thread_observations.append(
                {
                    "call": call,
                    "state": "unavailable",
                    "reason": f"thread-read-failed:{type(exc).__name__}",
                }
            )
            return
        values = {
            name: thread.get(name) if isinstance(thread, Mapping) else None
            for name in ("id", "model", "reasoningEffort", "modelProvider")
        }
        if values["id"] != thread_id or not all(
            isinstance(values[name], str) and values[name]
            for name in ("model", "reasoningEffort", "modelProvider")
        ):
            self._thread_observations.append(
                {"call": call, "state": "unavailable", "reason": "thread-identity-incomplete"}
            )
            return
        self._thread_observations.append(
            {
                "call": call,
                "state": "observed",
                "thread_sha256": hashlib.sha256(thread_id.encode("utf-8")).hexdigest(),
                "model": values["model"],
                "reasoning_effort": values["reasoningEffort"],
                "model_provider": values["modelProvider"],
            }
        )

    def runtime_observation(self) -> RuntimeObservation:
        """彙總本 run 每次呼叫的 provider 身分；任一次缺漏或不一致即 unknown。"""
        records = tuple(dict(item) for item in self._thread_observations)
        if not records:
            reason = "no-provider-call-observed"
            return RuntimeObservation(
                source=THREAD_IDENTITY_SOURCE,
                model_id=None,
                effort=None,
                model_reason=reason,
                effort_reason=reason,
            )
        missing = [item for item in records if item["state"] != "observed"]
        if missing:
            reason = f"provider-identity-unavailable:{len(missing)}/{len(records)}-calls"
            return RuntimeObservation(
                source=THREAD_IDENTITY_SOURCE,
                model_id=None,
                effort=None,
                model_reason=reason,
                effort_reason=reason,
                evidence=records,
            )
        providers = {item["model_provider"] for item in records}
        models = {item["model"] for item in records}
        efforts = {item["reasoning_effort"] for item in records}
        model_id: str | None = None
        effort: object | None = None
        model_reason: str | None = None
        effort_reason: str | None = None
        if providers != {EXPECTED_MODEL_PROVIDER}:
            model_reason = effort_reason = "unexpected-model-provider"
        else:
            if len(models) == 1:
                model_id = str(next(iter(models)))
            else:
                model_reason = "provider-model-inconsistent-across-calls"
            if len(efforts) == 1:
                effort = next(iter(efforts))
            else:
                effort_reason = "provider-effort-inconsistent-across-calls"
        return RuntimeObservation(
            source=THREAD_IDENTITY_SOURCE,
            model_id=model_id,
            effort=effort,
            model_reason=model_reason,
            effort_reason=effort_reason,
            evidence=records,
        )

    def _build_argv(self, prompt: str) -> list[str]:
        # 顯式 workdir 優先；否則用 complete() 開的臨時目錄（deterministic、無殘留）。
        workdir = self._workdir or self._active_workdir
        if workdir is None:  # pragma: no cover - complete() 保證兩者其一有值
            raise AdapterError("codex adapter 缺工作目錄（_build_argv 未經 complete 呼叫）")
        argv = [
            self._binary,
            "exec",
            prompt,
            "-m",
            self.model,
            "-c",
            f"model_reasoning_effort={self.effort}",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ignore-user-config",
        ]
        for feature in self.DISABLED_FEATURES:
            argv += ["--disable", feature]
        argv += ["--cd", workdir, "--json"]
        return argv

    def _parse(self, stdout: str) -> tuple[str, dict]:
        text: str | None = None
        usage: dict | None = None
        thread_ids: set[str] = set()
        for event in iter_json_objects(stdout):
            kind = event.get("type")
            if kind == "thread.started":
                thread_id = event.get("thread_id")
                if isinstance(thread_id, str) and _THREAD_ID_RE.fullmatch(thread_id):
                    thread_ids.add(thread_id)
                else:
                    thread_ids.add("")  # 格式不合法：視同不唯一，身分不觀測
            elif kind == "item.completed":
                item = event.get("item")
                if isinstance(item, dict) and item.get("type") == "agent_message":
                    message = item.get("text")
                    if not isinstance(message, str):
                        raise AdapterError(f"agent_message 缺字串內容：{item!r}")
                    text = message  # 後到的覆蓋前面的：取最後一則。
            elif kind == "turn.completed":
                candidate = event.get("usage")
                if isinstance(candidate, dict):
                    usage = candidate

        if text is None:
            raise AdapterError(
                "codex 輸出沒有 agent_message 事件（fail-closed）："
                f"{stdout.strip()[:300]}"
            )
        if usage is None:
            raise AdapterError("codex 輸出缺 turn.completed.usage（ledger 無從計費）")
        unique = next(iter(thread_ids)) if len(thread_ids) == 1 else ""
        self._last_thread_id = unique or None
        return text.strip(), usage


def build_app_server_thread_reader(
    binary: str,
    *,
    timeout_s: float = 60.0,
    disabled_features: tuple[str, ...] = CodexCliAdapter.DISABLED_FEATURES,
) -> ThreadReader:
    """以 ``codex app-server``（stdio JSON-RPC）的 ``thread/read`` 讀 thread metadata。

    只讀 provider 持久化的 thread（``includeTurns: false``），不 resume、不開新回合，
    因此不產生模型呼叫。每次呼叫獨立啟動並收掉 app-server process group；
    逾時、協定錯誤或 thread id 不符一律拋例外（由 adapter 轉成 unknown）。
    """

    argv = [binary, "app-server"]
    for feature in disabled_features:
        argv += ["--disable", feature]

    def _read(thread_id: str) -> Mapping[str, object]:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
        lines: queue.Queue[str | None] = queue.Queue()

        def _pump() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=_pump, daemon=True).start()
        deadline = time.monotonic() + timeout_s

        def send(message: Mapping[str, object]) -> None:
            assert process.stdin is not None
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

        def receive(request_id: int) -> Mapping[str, object]:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AdapterError("codex app-server thread/read 逾時")
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty as exc:
                    raise AdapterError("codex app-server thread/read 逾時") from exc
                if line is None:
                    raise AdapterError("codex app-server 在回應前結束")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and record.get("id") == request_id:
                    if "error" in record or not isinstance(record.get("result"), dict):
                        raise AdapterError("codex app-server 回報錯誤")
                    return record["result"]

        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "patchmud",
                            "title": "PatchMUD execution profile observer",
                            "version": "1",
                        }
                    },
                }
            )
            receive(1)
            send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "thread/read",
                    "params": {"threadId": thread_id, "includeTurns": False},
                }
            )
            thread = receive(2).get("thread")
            if not isinstance(thread, dict) or thread.get("id") != thread_id:
                raise AdapterError("codex app-server thread/read 回應與 thread id 不符")
            return thread
        except OSError as exc:
            raise AdapterError(f"codex app-server 無法通訊：{exc}") from exc
        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except OSError:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        process.kill()
                    process.wait()

    return _read
