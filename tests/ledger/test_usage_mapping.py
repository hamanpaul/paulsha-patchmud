"""Task 7 RED：per-provider usage mapping 與互斥 ledger（spec §10.1、報告 §5.6.1）。

鎖定：
- anthropic 式（``input_tokens/output_tokens/cache_read_input_tokens``）與
  openai 式（``prompt_tokens/completion_tokens/completion_tokens_details.reasoning_tokens``、
  cached ⊆ prompt）usage fixture → 互斥欄位＋billed totals＋``unallocated``。
- provider 未揭露 reasoning → ``None``，不得記 0（NA 原則）。
- ``aggregate_work_tokens``：T^work = uncached + cached + visible + reasoning
  （報告 §5.6.1），任一 entry 含 ``None`` → 整體 ``None``（NA 傳染）。
"""

from __future__ import annotations

import pytest

from patchmud.ledger.tokens import (
    LedgerEntry,
    LedgerError,
    aggregate_work_tokens,
    map_usage,
)


def _full_entry(**overrides) -> LedgerEntry:
    """全揭露 entry（直接建構，供 aggregate 測試）。"""
    kwargs = dict(
        turn=1,
        role="author",
        input_uncached=1000,
        input_cached=300,
        output_visible=500,
        reasoning=200,
        billed_input_total=1300,
        billed_output_total=700,
        unallocated=0,
    )
    kwargs.update(overrides)
    return LedgerEntry(**kwargs)


class TestAnthropicMapping:
    def test_exclusive_fields_and_billed_totals(self):
        entry = map_usage(
            "anthropic",
            {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 300,
            },
            turn=3,
            role="author",
        )
        assert entry.turn == 3
        assert entry.role == "author"
        assert entry.input_uncached == 1000
        assert entry.input_cached == 300
        assert entry.output_visible == 500
        # anthropic usage 不拆 reasoning → NA，不得記 0。
        assert entry.reasoning is None
        # billed totals = provider 帳面計價量總和，永遠照抄。
        assert entry.billed_input_total == 1300
        assert entry.billed_output_total == 500
        assert entry.unallocated == 0
        assert entry.api_calls == 1

    def test_cache_creation_goes_to_unallocated(self):
        entry = map_usage(
            "anthropic",
            {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 300,
                "cache_creation_input_tokens": 200,
            },
        )
        # cache 寫入 tokens 有計價但不屬四個互斥欄位 → 殘差進 unallocated。
        assert entry.billed_input_total == 1500
        assert entry.input_uncached == 1000
        assert entry.input_cached == 300
        assert entry.unallocated == 200

    def test_undisclosed_cache_is_na_not_zero(self):
        entry = map_usage(
            "anthropic",
            {"input_tokens": 1000, "output_tokens": 500},
        )
        assert entry.input_cached is None
        assert entry.reasoning is None
        assert entry.billed_input_total == 1000
        assert entry.billed_output_total == 500


class TestOpenAIMapping:
    def test_reasoning_split_and_cached_subset(self):
        entry = map_usage(
            "openai",
            {
                "prompt_tokens": 1200,
                "completion_tokens": 800,
                "prompt_tokens_details": {"cached_tokens": 400},
                "completion_tokens_details": {"reasoning_tokens": 300},
            },
            turn=1,
            role="reviewer",
        )
        assert entry.role == "reviewer"
        # cached ⊆ prompt：先拆 uncached，禁止重複相加。
        assert entry.input_uncached == 800
        assert entry.input_cached == 400
        # reasoning 含在 completion total：先扣除再填 visible。
        assert entry.output_visible == 500
        assert entry.reasoning == 300
        assert entry.billed_input_total == 1200
        assert entry.billed_output_total == 800
        assert entry.unallocated == 0

    def test_reasoning_absent_is_none_not_zero(self):
        entry = map_usage(
            "openai",
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "prompt_tokens_details": {"cached_tokens": 0},
            },
        )
        assert entry.reasoning is None
        assert entry.reasoning != 0
        assert entry.output_visible == 50
        assert entry.input_cached == 0
        assert entry.input_uncached == 100

    def test_details_absent_entirely_cached_is_na(self):
        entry = map_usage(
            "openai",
            {"prompt_tokens": 100, "completion_tokens": 50},
        )
        assert entry.input_cached is None
        assert entry.reasoning is None
        assert entry.billed_input_total == 100
        assert entry.billed_output_total == 50

    def test_cached_exceeding_prompt_fails_closed(self):
        with pytest.raises(LedgerError):
            map_usage(
                "openai",
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 200},
                },
            )

    def test_reasoning_exceeding_completion_fails_closed(self):
        with pytest.raises(LedgerError):
            map_usage(
                "openai",
                {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "completion_tokens_details": {"reasoning_tokens": 80},
                },
            )


class TestToolCallsSeam:
    """F16：per_tool_call 計價依賴 mapping 路徑帶入 tool_calls。

    map_usage 若無 tool_calls seam，真實 run 的每筆 entry 永遠
    tool_calls=0 → per_tool_call 成本靜默計 0（無聲少收）。
    """

    def test_tool_calls_flow_through_mapping(self):
        entry = map_usage(
            "openai",
            {"prompt_tokens": 100, "completion_tokens": 50},
            tool_calls=3,
        )
        assert entry.tool_calls == 3

    def test_tool_calls_default_zero(self):
        entry = map_usage(
            "anthropic", {"input_tokens": 1, "output_tokens": 1}
        )
        assert entry.tool_calls == 0

    def test_negative_tool_calls_rejected(self):
        with pytest.raises(LedgerError):
            map_usage(
                "anthropic",
                {"input_tokens": 1, "output_tokens": 1},
                tool_calls=-1,
            )


class TestMapUsageFailClosed:
    def test_unknown_provider_rejected(self):
        """未知 provider 必須 fail-closed，且非因欄位缺漏連帶擋下。

        判別性：fixture 採可被任一已知 mapper 成功映射的形狀——若 dispatch
        改成 fail-open（fallback 到任一預設 mapper），mapping 會靜默成功，
        本測試必炸。
        """
        # openai 形：fallback 到 _map_openai 會成功 → 抓 fail-open。
        with pytest.raises(LedgerError):
            map_usage("mystery", {"prompt_tokens": 1, "completion_tokens": 1})
        # anthropic 形：fallback 到 _map_anthropic 會成功 → 抓 fail-open。
        with pytest.raises(LedgerError):
            map_usage("mystery", {"input_tokens": 1, "output_tokens": 1})

    def test_missing_required_usage_fields_rejected(self):
        with pytest.raises(LedgerError):
            map_usage("anthropic", {"input_tokens": 1})
        with pytest.raises(LedgerError):
            map_usage("openai", {"prompt_tokens": 1})

    def test_negative_tokens_rejected(self):
        with pytest.raises(LedgerError):
            map_usage("anthropic", {"input_tokens": -1, "output_tokens": 1})

    def test_invalid_role_rejected(self):
        with pytest.raises(LedgerError):
            map_usage(
                "anthropic",
                {"input_tokens": 1, "output_tokens": 1},
                role="observer",
            )


class TestAggregateWorkTokens:
    def test_sums_all_four_exclusive_fields(self):
        entries = [_full_entry(), _full_entry(turn=2)]
        # 每筆 T^work = 1000 + 300 + 500 + 200 = 2000（報告 §5.6.1）。
        assert aggregate_work_tokens(entries) == 4000

    def test_any_na_field_poisons_aggregate(self):
        entries = [_full_entry(), _full_entry(turn=2, reasoning=None)]
        assert aggregate_work_tokens(entries) is None

    def test_na_cached_also_poisons(self):
        entries = [_full_entry(input_cached=None)]
        assert aggregate_work_tokens(entries) is None

    def test_empty_entries_sum_to_zero(self):
        assert aggregate_work_tokens([]) == 0


class TestLedgerEntryContract:
    def test_entry_is_immutable(self):
        entry = _full_entry()
        with pytest.raises(Exception):
            entry.turn = 99  # type: ignore[misc]

    def test_invalid_role_rejected_on_construction(self):
        with pytest.raises(LedgerError):
            _full_entry(role="bystander")

    def test_negative_billed_total_rejected(self):
        with pytest.raises(LedgerError):
            _full_entry(billed_input_total=-1)
