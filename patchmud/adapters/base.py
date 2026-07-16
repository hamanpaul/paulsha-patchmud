"""Model adapter 契約（spec §2、§10.1；plan Task 9）。

engine 只透過 ``ModelAdapter`` 觸碰模型（架構硬性邊界，spec §2）。契約唯一：
``complete(messages) -> AdapterResponse(text, usage_raw, wall_ms)``。

原則：
- ``usage_raw`` 是 provider usage metadata 的**原樣透傳**——互斥欄位拆分
  （mapping）是 ledger 的事（``patchmud.ledger.tokens.map_usage``），adapter
  不拆、不清洗、不解讀（§10.1）。
- ``usage_provider`` 宣告 usage_raw 的 schema（``map_usage`` 的 provider key）。
- HTTP 傳輸抽成注入的 transport callable：unit tests 注入 fake，不打真網路
  （plan invariant 3 同源精神）；真傳輸走 stdlib urllib，失敗 fail-closed。
- ``wall_ms`` 由注入時鐘量測，unit tests 注入 fake 時鐘保 deterministic。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = [
    "AdapterError",
    "AdapterResponse",
    "HttpRequest",
    "ModelAdapter",
    "ScriptedRepliesExhausted",
    "Transport",
    "build_urllib_transport",
]

DEFAULT_TIMEOUT_S = 600.0


class AdapterError(RuntimeError):
    """adapter 契約違反：傳輸失敗、回應結構非法、腳本耗盡。fail-closed。"""


class ScriptedRepliesExhausted(AdapterError):
    """ScriptedAdapter 的 replies 已耗盡仍被呼叫。"""


@dataclass(frozen=True)
class AdapterResponse:
    """一次模型呼叫的觀測結果。

    ``usage_raw``：provider usage metadata 原樣透傳（mapping 是 ledger 的事）。
    """

    text: str
    usage_raw: dict
    wall_ms: int


@dataclass(frozen=True)
class HttpRequest:
    """組裝完成、待 transport 送出的一次 HTTP POST。"""

    url: str
    headers: dict = field(default_factory=dict)
    payload: dict = field(default_factory=dict)


# transport callable：送出 HttpRequest、回傳解析後的 JSON body（dict）。
Transport = Callable[[HttpRequest], dict]


class ModelAdapter(ABC):
    """engine 觸碰模型的唯一 seam。"""

    #: usage_raw 的 schema 宣告：``ledger.tokens.map_usage`` 的 provider key。
    usage_provider: str = ""

    @abstractmethod
    def complete(self, messages: list[dict]) -> AdapterResponse:
        """送出對話 messages（``{"role", "content"}``），回一次完整補全。"""


class HttpModelAdapter(ModelAdapter):
    """HTTP adapters 的共同骨架：計時、傳輸、usage 原樣透傳。"""

    def __init__(
        self,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport: Transport = (
            transport if transport is not None else build_urllib_transport()
        )
        self._clock = clock

    def complete(self, messages: list[dict]) -> AdapterResponse:
        request = self._build_request(list(messages))
        started = self._clock()
        body = self._transport(request)
        wall_ms = round((self._clock() - started) * 1000)
        if not isinstance(body, dict):
            raise AdapterError(f"回應 body 必須是 JSON object：{type(body).__name__}")
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise AdapterError("回應缺 usage metadata（ledger 無從計費，fail-closed）")
        return AdapterResponse(
            text=self._extract_text(body), usage_raw=usage, wall_ms=wall_ms
        )

    def _build_request(self, messages: list[dict]) -> HttpRequest:
        raise NotImplementedError

    def _extract_text(self, body: dict) -> str:
        raise NotImplementedError


def build_urllib_transport(timeout_s: float = DEFAULT_TIMEOUT_S) -> Transport:
    """真 HTTP 傳輸（stdlib urllib）；任何傳輸／解析失敗 → ``AdapterError``。"""

    def _transport(request: HttpRequest) -> dict:
        data = json.dumps(request.payload).encode("utf-8")
        http_request = urllib.request.Request(
            request.url, data=data, headers=dict(request.headers), method="POST"
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise AdapterError(f"HTTP {exc.code}：{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AdapterError(f"傳輸失敗：{exc}") from exc
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdapterError(f"回應非合法 JSON：{exc}") from exc
        if not isinstance(body, dict):
            raise AdapterError(f"回應 body 必須是 JSON object：{type(body).__name__}")
        return body

    return _transport
