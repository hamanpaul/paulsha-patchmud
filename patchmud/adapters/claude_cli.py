"""Claude CLI headless adapter（使用系統上已認證的 `claude` CLI 執行對局）。

- 無需在環境變數設定 `ANTHROPIC_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN`。
- 以子流程呼叫 `claude --tools "" -p <prompt>` 非互動式極速輸出；`--tools ""`
  關閉全部工具，維持純補全語意（執行 candidate code 的唯一 seam 仍是
  ``IsolationRunner``，spec §2）。
- ``usage_raw`` 原樣透傳 `--output-format json` 回報的 usage（anthropic schema，
  由 ledger 的 ``map_usage("anthropic", …)`` 拆分）。CLI 未回報可用數值時，以
  prompt／回覆的字元數估算 input/output tokens 補齊，讓 ledger 有可計費的量而
  不致崩潰——估算值是 degraded 量測，精度不等同 API 直接回報的 usage。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable

from patchmud.adapters.base import AdapterError, AdapterResponse, ModelAdapter

__all__ = ["ClaudeCliAdapter"]


class ClaudeCliAdapter(ModelAdapter):
    """使用系統安裝的 `claude` CLI 進行 headless 補全。"""

    usage_provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-sonnet-5",
        *,
        claude_binary: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        runner: Callable[[list[str]], str] | None = None,
    ) -> None:
        self._model = model
        self._claude_binary = claude_binary or shutil.which("claude") or "claude"
        self._clock = clock
        self._runner = runner

    def complete(self, messages: list[dict]) -> AdapterResponse:
        system_parts = [
            str(m.get("content", "")) for m in messages if m.get("role") == "system"
        ]
        chat_messages = [m for m in messages if m.get("role") != "system"]

        prompt_lines = []
        for m in chat_messages:
            role = m.get("role")
            content = m.get("content", "")
            if role == "user":
                prompt_lines.append(f"[User]\n{content}")
            elif role == "assistant":
                prompt_lines.append(f"[Assistant]\n{content}")
            else:
                prompt_lines.append(f"[{role}]\n{content}")

        prompt_text = "\n\n".join(prompt_lines)

        cmd = [self._claude_binary, "--tools", "", "--output-format", "json", "-p", prompt_text]
        if self._model:
            cmd.extend(["--model", self._model])
        if system_parts:
            cmd.extend(["--system-prompt", "\n\n".join(system_parts)])

        start = self._clock()
        try:
            if self._runner is not None:
                out = self._runner(cmd)
            else:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=180,
                )
                out = res.stdout
        except (subprocess.SubprocessError, OSError) as exc:
            raise AdapterError(f"Claude CLI 執行失敗：{exc}") from exc

        elapsed_ms = int((self._clock() - start) * 1000)

        # 解析 JSON 輸出（claude --output-format json 會傳回帶 usage 的 JSON）
        usage_raw: dict = {}
        text: str = ""
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                text = str(data.get("result", "") or data.get("text", "")).strip()
                raw_u = data.get("usage")
                if isinstance(raw_u, dict):
                    usage_raw = dict(raw_u)
            else:
                text = str(out).strip()
        except (json.JSONDecodeError, ValueError):
            text = str(out).strip()

        # 確保 usage_raw 必定具備 input_tokens 與 output_tokens（避免 ledger map_usage 崩潰）
        if "input_tokens" not in usage_raw or not isinstance(usage_raw["input_tokens"], int):
            usage_raw["input_tokens"] = max(1, len(prompt_text) // 4)
        if "output_tokens" not in usage_raw or not isinstance(usage_raw["output_tokens"], int):
            usage_raw["output_tokens"] = max(1, len(text) // 4)

        return AdapterResponse(text=text, usage_raw=usage_raw, wall_ms=elapsed_ms)
