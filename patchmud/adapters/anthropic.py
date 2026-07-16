"""Anthropic Messages API adapter（spec §2 adapters、§10.1）。

- system 訊息依 Messages API 契約抽到 top-level ``system`` 參數。
- ``usage_raw`` 原樣透傳 ``body["usage"]``（cache_read / cache_creation 等
  欄位的拆分是 ledger ``map_usage("anthropic", …)`` 的事）。
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

__all__ = ["ANTHROPIC_VERSION", "AnthropicAdapter", "DEFAULT_BASE_URL"]

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"


class AnthropicAdapter(HttpModelAdapter):
    """Anthropic Messages API（``POST {base_url}/v1/messages``）。"""

    usage_provider = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        max_tokens: int = 4096,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(transport=transport, clock=clock)
        self._model = model
        self._api_key = api_key
        self._max_tokens = max_tokens
        self._base_url = base_url.rstrip("/")

    def _build_request(self, messages: list[dict]) -> HttpRequest:
        system_parts = [
            str(m.get("content", "")) for m in messages if m.get("role") == "system"
        ]
        chat_messages = [m for m in messages if m.get("role") != "system"]
        payload: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": chat_messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        return HttpRequest(
            url=f"{self._base_url}/v1/messages", headers=headers, payload=payload
        )

    def _extract_text(self, body: dict) -> str:
        content = body.get("content")
        if not isinstance(content, list):
            raise AdapterError("回應缺 content blocks（fail-closed）")
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                raise AdapterError(f"content block 非法：{block!r}")
            if block.get("type") == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise AdapterError(f"text block 缺字串內容：{block!r}")
                parts.append(text)
        return "".join(parts)
