"""OpenAI-compatible chat-completions adapter（spec §2 adapters、§10.1）。

涵蓋 openai 官方與地端 vllm / ollama 的 OpenAI 相容端點：以 ``base_url``
指向任意 ``…/v1``；``api_key`` 為空字串時不送 authorization header（地端）。

- messages（含 system）依 chat-completions 契約原樣透傳進 payload。
- ``usage_raw`` 原樣透傳 ``body["usage"]``（cached / reasoning details 的
  拆分是 ledger ``map_usage("openai", …)`` 的事）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from patchmud.adapters.base import (
    AdapterError,
    HttpModelAdapter,
    HttpRequest,
    Transport,
)

__all__ = ["OpenAICompatAdapter"]


class OpenAICompatAdapter(HttpModelAdapter):
    """OpenAI-compatible API（``POST {base_url}/chat/completions``）。"""

    usage_provider = "openai"

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        max_tokens: int = 4096,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(transport=transport, clock=clock)
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._base_url = base_url.rstrip("/")

    def _build_request(self, messages: list[dict]) -> HttpRequest:
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        return HttpRequest(
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            payload=payload,
        )

    def _extract_text(self, body: dict) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AdapterError("回應缺 choices（fail-closed）")
        first = choices[0]
        if not isinstance(first, dict):
            raise AdapterError(f"choice 非法：{first!r}")
        message = first.get("message")
        if not isinstance(message, dict):
            raise AdapterError("choice 缺 message（fail-closed）")
        content = message.get("content")
        if not isinstance(content, str):
            raise AdapterError(f"message.content 必須是字串：{content!r}")
        return content
