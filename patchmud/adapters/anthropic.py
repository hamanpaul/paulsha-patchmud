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

__all__ = ["ANTHROPIC_VERSION", "OAUTH_BETA", "AnthropicAdapter", "DEFAULT_BASE_URL"]

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"
#: OAuth bearer token 認證所需的 beta header（`/v1/messages` 不帶會 401）。
OAUTH_BETA = "oauth-2025-04-20"


class AnthropicAdapter(HttpModelAdapter):
    """Anthropic Messages API（``POST {base_url}/v1/messages``）。

    認證兩選一：``api_key`` → ``x-api-key``；``auth_token`` → OAuth bearer
    （``Authorization: Bearer`` + ``anthropic-beta: oauth-2025-04-20``），
    讓使用者用 ``ant auth login`` 的 Claude 帳號登入而不必管 API key。
    """

    usage_provider = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str = "",
        *,
        auth_token: str = "",
        max_tokens: int = 4096,
        base_url: str = DEFAULT_BASE_URL,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(transport=transport, clock=clock)
        if not api_key and not auth_token:
            raise AdapterError("AnthropicAdapter 需要 api_key 或 auth_token 其一")
        self._model = model
        self._api_key = api_key
        self._auth_token = auth_token
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
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        if self._auth_token:  # OAuth bearer（優先）
            headers["authorization"] = f"Bearer {self._auth_token}"
            headers["anthropic-beta"] = OAUTH_BETA
        else:
            headers["x-api-key"] = self._api_key
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
