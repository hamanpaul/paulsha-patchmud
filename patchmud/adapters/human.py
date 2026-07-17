"""Human adapter：`patchmud play` 人類親自對局（spec §5.4；plan Task 22）。

`HumanAdapter` 以 stdin/stdout 取代模型 adapter，人類以同一命令協定打完
一場 encounter——引擎、probe、queue、評分完全同構：

- ``complete(messages)`` 先 ``output_fn`` 最新狀態 render（system prompt
  在內容變更時輸出：作者各回合相同 → 只出一次；R1 loadout 的 reviewer
  subcall 重用同一 adapter，換上 ``reviewer.system_rules``——全 codebase
  唯一陳述 review YAML schema 之處——必須重新輸出，人類才寫得出
  schema-valid review，與模型對局同構），再讀 ``input_fn()`` 為回覆。
- ``usage_raw = {}``：人類對局沒有任何 token 量測事實，ledger 全欄位 NA
  （``ledger.tokens._map_human``；NA 不記 0，§10.1）。
- ``input_fn`` 收到 EOF（Ctrl-D）→ 視同 ``COMMIT`` 的收尾語意。
- ``wall_ms`` 由注入時鐘量測（人類思考時間）；unit tests 注入 fake 時鐘
  保 deterministic（plan invariant 3 同源精神）。
- 敘事文字一律經 zh-TW render pack 查表；命令關鍵字（``ACTION: COMMIT``）
  是結構化協定，維持英文（§5.4）。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from patchmud.adapters.base import AdapterResponse, ModelAdapter
from patchmud.engine import render_zh_tw as zh

__all__ = ["EOF_COMMIT_REPLY", "HumanAdapter"]

#: EOF（Ctrl-D）收尾語意：視同 COMMIT（命令關鍵字英文，spec §5.4）。
EOF_COMMIT_REPLY = "ACTION: COMMIT"


class HumanAdapter(ModelAdapter):
    """stdin/stdout 人類 adapter：render 先出、回覆後進，全 token 欄位 NA。"""

    usage_provider = "human"

    def __init__(
        self,
        input_fn: Callable[[], str] = input,
        output_fn: Callable[[str], None] = print,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._input_fn = input_fn
        self._output_fn = output_fn
        self._clock = clock
        self._last_system: str | None = None

    def complete(self, messages: list[dict]) -> AdapterResponse:
        # 先 render：system prompt 在內容變更時輸出——作者各回合 system 相同
        # → 只出一次；reviewer subcall（R1，重用同一 adapter）換上
        # reviewer.system_rules → 必須重新輸出（review YAML schema 唯一出處，
        # spec §5.4/§6.2 同構）
        system = _system_content(messages)
        if system is not None and system != self._last_system:
            self._output_fn(system)
            self._last_system = system
        latest = _latest_user_content(messages)
        if latest:
            self._output_fn(latest)
        self._output_fn(zh.text("play.input_prompt"))

        started = self._clock()
        try:
            reply = str(self._input_fn())
        except EOFError:
            self._output_fn(zh.text("play.eof_commit"))
            reply = EOF_COMMIT_REPLY
        wall_ms = max(0, round((self._clock() - started) * 1000))
        return AdapterResponse(text=reply, usage_raw={}, wall_ms=wall_ms)


def _system_content(messages: list[dict]) -> str | None:
    for message in messages:
        if message.get("role") == "system":
            return str(message.get("content", ""))
    return None


def _latest_user_content(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""
