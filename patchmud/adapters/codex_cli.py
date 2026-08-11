"""codex CLI headless adapter（OpenAI，OAuth 登入態；issue #14）。

以 ``codex exec`` 非互動模式執行，認證取自 ``codex login`` 建立的
``~/.codex/auth.json``——不需要 ``OPENAI_API_KEY``。

純補全模式的硬性旗標（見 :mod:`patchmud.adapters.cli_base` 的理由）：

``--sandbox read-only``
    禁止寫入；candidate code 的執行仍只走 ``IsolationRunner``。
``--cd <tmpdir>`` + ``--ephemeral``
    在臨時空目錄執行、不落 session 檔，避免沾染 encounter workspace。
``--skip-git-repo-check``
    臨時目錄不是 git repo。
``--ignore-user-config``
    不載入 ``~/.codex/config.toml``（personality、預設 effort、hooks 都會
    污染評測的可重現性）；auth 仍照 ``CODEX_HOME`` 解析。
``--disable plugins|memories|goals|hooks``
    關閉會把使用者狀態帶進 prompt 的功能。

輸出解析（``--json`` 的 JSONL 事件串）::

    {"type":"item.completed","item":{"type":"agent_message","text":"…"}}
    {"type":"turn.completed","usage":{"input_tokens":…,"output_tokens":…}}

取**最後**一則 ``agent_message`` 當回覆（模型可能先講一句再給動作），
``turn.completed.usage`` 原樣透傳給 ledger 的 ``codex`` mapper。
"""

from __future__ import annotations

import tempfile

from patchmud.adapters.base import AdapterError
from patchmud.adapters.cli_base import CliModelAdapter, iter_json_objects

__all__ = ["CodexCliAdapter"]


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
        **kwargs,
    ) -> None:
        super().__init__(model, binary=codex_binary, **kwargs)
        self._workdir = workdir

    def _build_argv(self, prompt: str) -> list[str]:
        # workdir 未指定 → 每次呼叫一個新的臨時空目錄（deterministic、無殘留）。
        workdir = self._workdir or tempfile.mkdtemp(prefix="patchmud-codex-")
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
            "--ephemeral",
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
        for event in iter_json_objects(stdout):
            kind = event.get("type")
            if kind == "item.completed":
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
        return text.strip(), usage
