"""Task 9 RED：model adapters（spec §10.1、plan Task 9）。

不打真網路：HTTP adapters 以注入的 fake transport callable 測試，鎖定：
- request 組裝（model、messages、max_tokens、認證 headers、endpoint URL）。
- ``usage_raw`` 原樣透傳——mapping 是 ledger 的事，adapter 不拆（§10.1）。
- 回應結構缺 usage / 缺 content → fail-closed ``AdapterError``。
- ``ScriptedAdapter(replies)`` 依序回放，耗盡 replies → raise。
- ``wall_ms`` 由注入時鐘量測（unit tests 一律注入 fake，plan invariant）。
"""

from __future__ import annotations

import pytest

from patchmud.adapters.anthropic import ANTHROPIC_VERSION, AnthropicAdapter
from patchmud.adapters.base import (
    AdapterError,
    AdapterResponse,
    HttpRequest,
    ModelAdapter,
    ScriptedRepliesExhausted,
)
from patchmud.adapters.openai_compat import OpenAICompatAdapter
from patchmud.adapters.scripted import ScriptedAdapter


class FakeTransport:
    """記錄 HttpRequest 並回放 canned JSON body 的 transport callable。"""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[HttpRequest] = []

    def __call__(self, request: HttpRequest) -> dict:
        self.requests.append(request)
        return self.response


class FakeClock:
    """依序回放 monotonic 秒數的假時鐘。"""

    def __init__(self, ticks: list[float]) -> None:
        self._ticks = iter(ticks)

    def __call__(self) -> float:
        return next(self._ticks)


MESSAGES = [
    {"role": "system", "content": "你是 PatchMUD 的作者 agent。"},
    {"role": "user", "content": "ACTION: LOOK"},
]

ANTHROPIC_RESPONSE = {
    "id": "msg_01",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "ACTION: RUN_TEST"}],
    "usage": {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 30,
        "cache_creation_input_tokens": 40,
        # 未知欄位也必須透傳：adapter 不解讀 usage。
        "server_tool_use": {"web_search_requests": 0},
    },
}

OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ACTION: RUN_TEST"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 30},
        "completion_tokens_details": {"reasoning_tokens": 5},
    },
}


def _anthropic(transport, **overrides) -> AnthropicAdapter:
    kwargs = dict(
        model="claude-test-1",
        api_key="sk-ant-test",
        max_tokens=1024,
        transport=transport,
    )
    kwargs.update(overrides)
    return AnthropicAdapter(**kwargs)


def _openai(transport, **overrides) -> OpenAICompatAdapter:
    kwargs = dict(
        model="gpt-test-1",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        max_tokens=1024,
        transport=transport,
    )
    kwargs.update(overrides)
    return OpenAICompatAdapter(**kwargs)


class TestScriptedAdapter:
    def test_is_model_adapter(self):
        assert isinstance(ScriptedAdapter(["a"]), ModelAdapter)

    def test_replays_replies_in_order(self):
        adapter = ScriptedAdapter(["第一回合", "第二回合", "第三回合"])
        texts = [adapter.complete(MESSAGES).text for _ in range(3)]
        assert texts == ["第一回合", "第二回合", "第三回合"]

    def test_exhausted_replies_raise(self):
        adapter = ScriptedAdapter(["唯一回覆"])
        adapter.complete(MESSAGES)
        with pytest.raises(ScriptedRepliesExhausted):
            adapter.complete(MESSAGES)
        # 耗盡也是 AdapterError 家族（呼叫端 fail-closed 一致處理）。
        assert issubclass(ScriptedRepliesExhausted, AdapterError)

    def test_synthetic_usage_is_deterministic_openai_format(self):
        # dry-run 矩陣要能餵 ledger：usage_raw 用 openai 格式的合成量，
        # 且對相同輸入完全 deterministic。
        first = ScriptedAdapter(["回覆 A"]).complete(MESSAGES)
        second = ScriptedAdapter(["回覆 A"]).complete(MESSAGES)
        assert isinstance(first, AdapterResponse)
        assert first.usage_raw == second.usage_raw
        assert set(first.usage_raw) == {"prompt_tokens", "completion_tokens"}
        assert all(isinstance(v, int) and v >= 1 for v in first.usage_raw.values())
        assert first.wall_ms == 0
        assert ScriptedAdapter([]).usage_provider == "openai"


class TestAnthropicAdapter:
    def test_usage_provider(self):
        assert AnthropicAdapter.usage_provider == "anthropic"
        assert isinstance(_anthropic(FakeTransport(ANTHROPIC_RESPONSE)), ModelAdapter)

    def test_request_assembly(self):
        transport = FakeTransport(ANTHROPIC_RESPONSE)
        _anthropic(transport).complete(MESSAGES)
        assert len(transport.requests) == 1
        request = transport.requests[0]
        assert request.url == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "sk-ant-test"
        assert request.headers["anthropic-version"] == ANTHROPIC_VERSION
        assert request.headers["content-type"] == "application/json"
        assert request.payload["model"] == "claude-test-1"
        assert request.payload["max_tokens"] == 1024

    def test_oauth_bearer_headers(self):
        # auth_token → Authorization: Bearer + oauth beta header，且不送 x-api-key
        transport = FakeTransport(ANTHROPIC_RESPONSE)
        AnthropicAdapter(
            model="claude-test-1", auth_token="oat-xyz", transport=transport
        ).complete(MESSAGES)
        headers = transport.requests[0].headers
        assert headers["authorization"] == "Bearer oat-xyz"
        assert headers["anthropic-beta"] == "oauth-2025-04-20"
        assert "x-api-key" not in headers

    def test_api_key_path_has_no_bearer(self):
        transport = FakeTransport(ANTHROPIC_RESPONSE)
        _anthropic(transport).complete(MESSAGES)
        headers = transport.requests[0].headers
        assert headers["x-api-key"] == "sk-ant-test"
        assert "authorization" not in headers

    def test_requires_some_credential(self):
        with pytest.raises(AdapterError):
            AnthropicAdapter(model="claude-test-1")

    def test_system_message_extracted_to_top_level(self):
        # anthropic Messages API 的 system 是 top-level 參數，不進 messages。
        transport = FakeTransport(ANTHROPIC_RESPONSE)
        _anthropic(transport).complete(MESSAGES)
        payload = transport.requests[0].payload
        assert payload["system"] == "你是 PatchMUD 的作者 agent。"
        assert payload["messages"] == [{"role": "user", "content": "ACTION: LOOK"}]

    def test_usage_raw_passthrough_verbatim(self):
        # mapping 是 ledger 的事：adapter 不拆、不清洗、不解讀。
        response = _anthropic(FakeTransport(ANTHROPIC_RESPONSE)).complete(MESSAGES)
        assert response.usage_raw == ANTHROPIC_RESPONSE["usage"]

    def test_text_joins_text_blocks_only(self):
        body = dict(ANTHROPIC_RESPONSE)
        body["content"] = [
            {"type": "thinking", "thinking": "（內部思考不進 text）"},
            {"type": "text", "text": "ACTION: "},
            {"type": "text", "text": "COMMIT"},
        ]
        response = _anthropic(FakeTransport(body)).complete(MESSAGES)
        assert response.text == "ACTION: COMMIT"

    def test_missing_usage_fail_closed(self):
        body = {k: v for k, v in ANTHROPIC_RESPONSE.items() if k != "usage"}
        with pytest.raises(AdapterError):
            _anthropic(FakeTransport(body)).complete(MESSAGES)

    def test_missing_content_fail_closed(self):
        body = {k: v for k, v in ANTHROPIC_RESPONSE.items() if k != "content"}
        with pytest.raises(AdapterError):
            _anthropic(FakeTransport(body)).complete(MESSAGES)

    def test_wall_ms_from_injected_clock(self):
        adapter = _anthropic(
            FakeTransport(ANTHROPIC_RESPONSE), clock=FakeClock([10.0, 10.25])
        )
        assert adapter.complete(MESSAGES).wall_ms == 250


class TestOpenAICompatAdapter:
    def test_usage_provider(self):
        assert OpenAICompatAdapter.usage_provider == "openai"
        assert isinstance(_openai(FakeTransport(OPENAI_RESPONSE)), ModelAdapter)

    def test_request_assembly(self):
        transport = FakeTransport(OPENAI_RESPONSE)
        _openai(transport).complete(MESSAGES)
        assert len(transport.requests) == 1
        request = transport.requests[0]
        assert request.url == "https://api.openai.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        assert request.headers["content-type"] == "application/json"
        assert request.payload["model"] == "gpt-test-1"
        assert request.payload["max_tokens"] == 1024
        # chat-completions 格式的 system 留在 messages 內，原樣透傳。
        assert request.payload["messages"] == MESSAGES

    def test_local_endpoint_without_api_key_omits_auth_header(self):
        # 地端 vllm/ollama（spec §2）：api_key 為空 → 不送 authorization。
        transport = FakeTransport(OPENAI_RESPONSE)
        _openai(
            transport, api_key="", base_url="http://localhost:8000/v1/"
        ).complete(MESSAGES)
        request = transport.requests[0]
        assert request.url == "http://localhost:8000/v1/chat/completions"
        assert "authorization" not in request.headers

    def test_usage_raw_passthrough_verbatim(self):
        response = _openai(FakeTransport(OPENAI_RESPONSE)).complete(MESSAGES)
        assert response.usage_raw == OPENAI_RESPONSE["usage"]
        # 巢狀 details 也必須原封不動（reasoning 拆分是 ledger 的事）。
        assert response.usage_raw["completion_tokens_details"] == {
            "reasoning_tokens": 5
        }

    def test_text_from_first_choice(self):
        response = _openai(FakeTransport(OPENAI_RESPONSE)).complete(MESSAGES)
        assert response.text == "ACTION: RUN_TEST"

    def test_missing_choices_fail_closed(self):
        body = {k: v for k, v in OPENAI_RESPONSE.items() if k != "choices"}
        with pytest.raises(AdapterError):
            _openai(FakeTransport(body)).complete(MESSAGES)

    def test_non_text_content_fail_closed(self):
        body = dict(OPENAI_RESPONSE)
        body["choices"] = [{"index": 0, "message": {"role": "assistant", "content": None}}]
        with pytest.raises(AdapterError):
            _openai(FakeTransport(body)).complete(MESSAGES)

    def test_missing_usage_fail_closed(self):
        body = {k: v for k, v in OPENAI_RESPONSE.items() if k != "usage"}
        with pytest.raises(AdapterError):
            _openai(FakeTransport(body)).complete(MESSAGES)

    def test_wall_ms_from_injected_clock(self):
        adapter = _openai(
            FakeTransport(OPENAI_RESPONSE), clock=FakeClock([5.0, 5.5])
        )
        assert adapter.complete(MESSAGES).wall_ms == 500
