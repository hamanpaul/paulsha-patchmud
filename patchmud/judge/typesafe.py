"""TypeSafe System One（JEV）的 structured-judge adapter。

- endpoint 與 request 形狀依官方 HTTP API（``POST /v1/systemone``，body 為
  ``model``／``state``／``questions``）。model 預設 pin 版本 ID，不用會移動的
  alias，讓結果可重現；回應的 ``model`` 欄位原樣記錄。
- API key 只從 process environment 的 ``TYPESAFE_API_KEY`` 讀取，不接受參數
  傳入、不寫入任何紀錄。
- 429／529 依官方建議指數退避重試（有 ``Retry-After`` 就遵守，上限 30 秒）；
  其他 HTTP 錯誤不重試。重試只處理技術失敗，判決內容一律照收。
- 成本依牌價換算：input token × 每百萬 token 0.042 美元（output 不計費）。
  牌價取自官方 Models 頁（2026-09-27 查閱），``basis`` 標明是換算值。
- 傳輸與睡眠皆可注入：unit tests 不打真網路、不真的等待。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from patchmud.judge.structured import (
    JudgeCost,
    JudgeError,
    StructuredJudgeAdapter,
    StructuredRequest,
    StructuredResponse,
    parse_answers,
    sha256_json,
)

__all__ = [
    "JEV_DEFAULT_MODEL",
    "JEV_ENDPOINT",
    "JEV_USD_PER_INPUT_MTOK",
    "TypeSafeHttpReply",
    "TypeSafeJudgeAdapter",
    "build_typesafe_transport",
]

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_MODEL = "jev-1.13.0"
JEV_USD_PER_INPUT_MTOK = Decimal("0.042")
API_KEY_ENV = "TYPESAFE_API_KEY"
_RETRYABLE_STATUS = frozenset({429, 529})
_RETRY_AFTER_CAP_S = 30.0


@dataclass(frozen=True)
class TypeSafeHttpReply:
    status: int
    body: dict
    headers: dict = field(default_factory=dict)


#: transport callable：送出 (url, headers, payload)，回傳 ``TypeSafeHttpReply``。
TypeSafeTransport = Callable[[str, dict, dict], TypeSafeHttpReply]


def build_typesafe_transport(timeout_s: float = 60.0) -> TypeSafeTransport:
    """stdlib urllib 傳輸；HTTP 錯誤也回傳 status，由 adapter 決定是否重試。"""

    def _transport(url: str, headers: dict, payload: dict) -> TypeSafeHttpReply:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                status = response.status
                raw = response.read()
                reply_headers = {k.lower(): v for k, v in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            reply_headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise JudgeError("transport", f"TypeSafe 傳輸失敗：{exc}", retryable=True) from exc
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"_non_json_body": raw[:500].decode("utf-8", errors="replace")}
        if not isinstance(body, dict):
            body = {"_non_object_body": body}
        return TypeSafeHttpReply(status=status, body=body, headers=reply_headers)

    return _transport


class TypeSafeJudgeAdapter(StructuredJudgeAdapter):
    """以 TypeSafe HTTP API 回答 ``StructuredRequest``。"""

    provider = "jev"

    def __init__(
        self,
        model: str = JEV_DEFAULT_MODEL,
        *,
        transport: TypeSafeTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        environ: dict | None = None,
        max_attempts: int = 4,
    ) -> None:
        self.model = model
        self._transport = transport if transport is not None else build_typesafe_transport()
        self._clock = clock
        self._sleep = sleep
        self._environ = environ if environ is not None else os.environ
        self._max_attempts = max(1, max_attempts)

    def _headers(self) -> dict:
        key = self._environ.get(API_KEY_ENV, "")
        if not key:
            raise JudgeError("config", f"缺 {API_KEY_ENV}（只從 environment 讀取）")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    def _backoff(self, attempt: int, reply: TypeSafeHttpReply | None) -> float:
        if reply is not None:
            retry_after = reply.headers.get("retry-after")
            try:
                if retry_after is not None:
                    return min(max(float(retry_after), 0.0), _RETRY_AFTER_CAP_S)
            except ValueError:
                pass
        return min(2.0 ** (attempt - 1), _RETRY_AFTER_CAP_S)

    def judge(self, request: StructuredRequest) -> StructuredResponse:
        headers = self._headers()
        payload = {"model": self.model, **request.to_payload()}
        started = self._clock()
        attempt = 0
        while True:
            attempt += 1
            reply: TypeSafeHttpReply | None = None
            try:
                reply = self._transport(JEV_ENDPOINT, headers, payload)
            except JudgeError as exc:
                if exc.kind != "transport" or not exc.retryable or attempt >= self._max_attempts:
                    raise
            if reply is not None:
                if reply.status == 200:
                    break
                detail = json.dumps(reply.body, ensure_ascii=False)[:500]
                retryable = reply.status in _RETRYABLE_STATUS
                if not retryable or attempt >= self._max_attempts:
                    raise JudgeError(
                        "transport",
                        f"TypeSafe HTTP {reply.status}：{detail}",
                        status=reply.status,
                        retryable=retryable,
                    )
            self._sleep(self._backoff(attempt, reply))
        wall_ms = round((self._clock() - started) * 1000)
        body = reply.body
        answers = parse_answers(request, body.get("answers"))
        usage = body.get("usage")
        if not isinstance(usage, dict):
            raise JudgeError("invalid_output", "TypeSafe 回應缺 usage")
        input_tokens = usage.get("input_tokens")
        usd = None
        if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
            usd = Decimal(input_tokens) * JEV_USD_PER_INPUT_MTOK / Decimal(1_000_000)
        return StructuredResponse(
            answers=answers,
            provider=self.provider,
            model=str(body.get("model") or self.model),
            wall_ms=wall_ms,
            response_sha256=sha256_json(body),
            cost=JudgeCost(
                usd=usd,
                basis="typesafe_list_price_input_tokens_usd_0.042_per_mtok",
                units={"input_tokens": input_tokens, "output_tokens": usage.get("output_tokens")},
            ),
            usage={k: usage.get(k) for k in ("input_tokens", "output_tokens")},
            attempts=attempt,
        )
