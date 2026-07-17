"""Task 22 RED：HumanAdapter（spec §5.4；plan Task 22 Step 1）。

`patchmud play` 的人類 adapter：`complete(messages)` 先 `output_fn` 最新
狀態 render、再讀 `input_fn()` 為回覆；`usage_raw = {}`（ledger 全欄位
NA）；`input_fn` 收到 EOF（Ctrl-D）→ 視同 `COMMIT` 的收尾語意。

unit tests 全部注入 fake（scripted input_fn／spy output_fn／fake 時鐘），
不碰 stdin、不啟真 namespace、不打真 API（plan invariant 3）。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from patchmud.adapters.human import EOF_COMMIT_REPLY, HumanAdapter
from patchmud.engine import render_zh_tw as zh
from patchmud.ledger.cost import entry_cost
from patchmud.ledger.pricing import PricingSnapshot
from patchmud.ledger.tokens import (
    LedgerError,
    aggregate_billed_totals,
    aggregate_work_tokens,
    map_usage,
)

SYSTEM_PROMPT = "系統規則：每回合恰好一個動作。"
RENDER_TURN_1 = "=== 回合 1／8 ===\n【戰場】encounter mini-inventory-v1"
RENDER_TURN_2 = "=== 回合 2／8 ===\n【戰場】encounter mini-inventory-v1"

MESSAGES_TURN_1 = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": RENDER_TURN_1},
]
MESSAGES_TURN_2 = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": RENDER_TURN_1},
    {"role": "assistant", "content": "ACTION: LOOK"},
    {"role": "user", "content": RENDER_TURN_2},
]


class SpyIO:
    """scripted input_fn ＋ spy output_fn：以單一事件序驗互動順序。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.events: list[tuple[str, ...]] = []

    def input_fn(self) -> str:
        self.events.append(("input",))
        if not self._replies:
            raise EOFError
        return self._replies.pop(0)

    def output_fn(self, text: object) -> None:
        self.events.append(("output", str(text)))

    def outputs(self) -> list[str]:
        return [event[1] for event in self.events if event[0] == "output"]

    def first_input_index(self) -> int:
        return self.events.index(("input",))


class TestCompleteContract:
    def test_render_output_before_input(self) -> None:
        io = SpyIO(["ACTION: LOOK"])
        adapter = HumanAdapter(input_fn=io.input_fn, output_fn=io.output_fn)

        response = adapter.complete(MESSAGES_TURN_1)

        # 互動順序：先看到最新狀態 render，才被要求輸入
        first_input = io.first_input_index()
        render_outputs = [
            index
            for index, event in enumerate(io.events)
            if event[0] == "output" and "回合 1" in event[1]
        ]
        assert render_outputs and all(index < first_input for index in render_outputs)
        # 回覆原樣透傳；usage_raw 為空 dict（ledger 全欄位 NA）
        assert response.text == "ACTION: LOOK"
        assert response.usage_raw == {}

    def test_system_prompt_shown_only_once(self) -> None:
        io = SpyIO(["ACTION: LOOK", "ACTION: COMMIT"])
        adapter = HumanAdapter(input_fn=io.input_fn, output_fn=io.output_fn)

        adapter.complete(MESSAGES_TURN_1)
        adapter.complete(MESSAGES_TURN_2)

        outputs = io.outputs()
        assert sum(1 for text in outputs if SYSTEM_PROMPT in text) == 1
        # 第二回合仍先輸出最新 render
        assert any("回合 2" in text for text in outputs)

    def test_system_reshown_when_content_changes(self) -> None:
        # review finding：R1 loadout 的 reviewer subcall 重用同一 HumanAdapter
        # （loop `config.reviewer_adapter or self.adapter`；play_cli 不設
        # reviewer_adapter）。system 內容變更（reviewer.system_rules——全
        # codebase 唯一陳述 review YAML schema 之處）必須重新輸出給人類，
        # 否則人類寫不出 schema-valid review（spec §5.4 同構）。
        reviewer_system = zh.text("reviewer.system_rules")
        io = SpyIO(["ACTION: LOOK", "findings: []"])
        adapter = HumanAdapter(input_fn=io.input_fn, output_fn=io.output_fn)

        adapter.complete(MESSAGES_TURN_1)  # author：author system 出一次
        adapter.complete(
            [
                {"role": "system", "content": reviewer_system},
                {"role": "user", "content": "【審查素材】cumulative diff …"},
            ]
        )

        # reviewer system rules 必須輸出，且先於該次 subcall 的輸入要求
        input_indexes = [
            index for index, event in enumerate(io.events) if event[0] == "input"
        ]
        rules_indexes = [
            index
            for index, event in enumerate(io.events)
            if event[0] == "output" and reviewer_system in event[1]
        ]
        assert rules_indexes, "reviewer system rules 必須輸出給人類"
        assert input_indexes[0] < rules_indexes[0] < input_indexes[1]

    def test_wall_ms_from_injected_clock(self) -> None:
        io = SpyIO(["ACTION: LOOK"])
        ticks = iter([2.0, 3.5])
        adapter = HumanAdapter(
            input_fn=io.input_fn, output_fn=io.output_fn, clock=lambda: next(ticks)
        )
        response = adapter.complete(MESSAGES_TURN_1)
        assert response.wall_ms == 1500

    def test_eof_is_commit(self) -> None:
        io = SpyIO([])  # input 立即 EOF（Ctrl-D）
        adapter = HumanAdapter(input_fn=io.input_fn, output_fn=io.output_fn)

        response = adapter.complete(MESSAGES_TURN_1)

        assert response.text == EOF_COMMIT_REPLY == "ACTION: COMMIT"
        assert response.usage_raw == {}
        # EOF 收尾的敘事經 render pack 查表（spec §5.4）
        assert zh.text("play.eof_commit") in io.outputs()


class TestHumanLedgerMapping:
    def test_map_usage_human_all_na(self) -> None:
        entry = map_usage("human", {}, turn=1, role="author")
        # 互斥 token 欄位與 billed totals 全 NA（不記 0，§10.1）
        assert entry.input_uncached is None
        assert entry.input_cached is None
        assert entry.output_visible is None
        assert entry.reasoning is None
        assert entry.billed_input_total is None
        assert entry.billed_output_total is None
        assert entry.work_tokens() is None
        assert aggregate_work_tokens([entry]) is None
        assert aggregate_billed_totals([entry]) == (None, None)

    def test_map_usage_human_rejects_nonempty_usage(self) -> None:
        with pytest.raises(LedgerError):
            map_usage("human", {"prompt_tokens": 1})

    def test_human_entry_cost_fail_closed(self) -> None:
        entry = map_usage("human", {}, turn=1, role="author")
        snapshot = PricingSnapshot(
            snapshot_date="2026-07-17",
            currency="USD",
            input_uncached_per_mtok=Decimal("3"),
            input_cached_per_mtok=Decimal("0.3"),
            output_per_mtok=Decimal("15"),
            per_request=Decimal("0"),
            per_tool_call=Decimal("0"),
            minimum_charge_per_call=Decimal("0"),
            content_hash="x" * 64,
        )
        # human entry 無計費事實（billed totals NA）→ 不可計價
        with pytest.raises(LedgerError):
            entry_cost(entry, snapshot)
