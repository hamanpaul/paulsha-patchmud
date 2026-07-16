"""腳本化 adapter：測試與矩陣 dry-run 用（plan Task 9、Task 21）。

依序回放預先寫死的 replies；耗盡再被呼叫 → ``ScriptedRepliesExhausted``
（fail-closed，劇本寫錯不得靜默循環）。

usage_raw 用 openai 格式的**合成 deterministic 量**（字元數 // 4，下限 1），
讓 dry-run 的 ledger 路徑（``map_usage("openai", …)``）可完整走通；
不打網路、不耗 wall-clock（``wall_ms = 0``）。
"""

from __future__ import annotations

from collections.abc import Sequence

from patchmud.adapters.base import (
    AdapterResponse,
    ModelAdapter,
    ScriptedRepliesExhausted,
)

__all__ = ["ScriptedAdapter"]


def _synthetic_tokens(text: str) -> int:
    """deterministic 合成 token 量：字元數 // 4，下限 1（避免零量計費歧義）。"""
    return max(1, len(text) // 4)


class ScriptedAdapter(ModelAdapter):
    """依序回放 replies 的假模型。"""

    usage_provider = "openai"

    def __init__(self, replies: Sequence[str]) -> None:
        self._replies = list(replies)
        self._cursor = 0

    def complete(self, messages: list[dict]) -> AdapterResponse:
        if self._cursor >= len(self._replies):
            raise ScriptedRepliesExhausted(
                f"scripted replies 已耗盡（共 {len(self._replies)} 則）"
            )
        reply = self._replies[self._cursor]
        self._cursor += 1
        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        usage_raw = {
            "prompt_tokens": _synthetic_tokens("x" * prompt_chars),
            "completion_tokens": _synthetic_tokens(reply),
        }
        return AdapterResponse(text=reply, usage_raw=usage_raw, wall_ms=0)
