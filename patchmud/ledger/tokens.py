"""互斥 token ledger：per-provider usage mapping（spec §10.1、報告 §5.6.1）。

原則：
- 互斥欄位 ``input_uncached / input_cached / output_visible / reasoning``
  只記 provider 明確揭露的量；不可得記 ``None``（NA），不得記 0。
- ``billed_input_total / billed_output_total`` 為 provider 帳面計價總量，
  永遠照抄；計費一律以 billed totals 計算（F17）。
- 拆不動的殘差（如 cache 寫入 tokens）進 ``unallocated``；殘差為負代表
  provider 資料不一致，fail-closed。
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_ROLES = ("author", "reviewer")


class LedgerError(ValueError):
    """ledger 契約違反：未知 provider、欄位缺漏、量值不一致、計價設定錯誤。"""


@dataclass(frozen=True)
class LedgerEntry:
    """一次 adapter 呼叫的 ledger 紀錄（spec §10.1 全欄位；NA 以 None 表示）。"""

    turn: int
    role: str
    input_uncached: int | None
    input_cached: int | None
    output_visible: int | None
    reasoning: int | None
    billed_input_total: int
    billed_output_total: int
    unallocated: int
    api_calls: int = 1
    tool_calls: int = 0
    wall_clock_ms: int = 0
    prompt_bytes: int = 0
    generated_bytes: int = 0
    pricing_snapshot_ref: str = ""

    def __post_init__(self) -> None:
        if self.role not in VALID_ROLES:
            raise LedgerError(f"role 非法：{self.role!r}")
        for name in ("billed_input_total", "billed_output_total", "unallocated",
                     "api_calls", "tool_calls", "wall_clock_ms",
                     "prompt_bytes", "generated_bytes", "turn"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise LedgerError(f"{name} 必須是非負整數：{value!r}")
        for name in ("input_uncached", "input_cached", "output_visible", "reasoning"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise LedgerError(f"{name} 必須是非負整數或 None（NA）：{value!r}")

    def work_tokens(self) -> int | None:
        """T^work = uncached + cached + visible + reasoning（報告 §5.6.1）。

        任一互斥欄位為 NA → 本筆 T^work 為 NA。
        """
        parts = (self.input_uncached, self.input_cached,
                 self.output_visible, self.reasoning)
        if any(part is None for part in parts):
            return None
        return sum(parts)  # type: ignore[arg-type]


def _require_token(usage: dict, key: str) -> int:
    value = usage.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LedgerError(f"usage 缺必填欄位或值非法：{key}={value!r}")
    return value


def _optional_token(container: dict, key: str) -> int | None:
    """provider 未揭露 → None（NA）；揭露則必須是非負整數。"""
    if key not in container:
        return None
    value = container[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LedgerError(f"usage 欄位值非法：{key}={value!r}")
    return value


def _map_anthropic(usage: dict) -> dict:
    input_tokens = _require_token(usage, "input_tokens")
    output_tokens = _require_token(usage, "output_tokens")
    cache_read = _optional_token(usage, "cache_read_input_tokens")
    cache_creation = _optional_token(usage, "cache_creation_input_tokens")
    billed_input = input_tokens + (cache_read or 0) + (cache_creation or 0)
    return dict(
        input_uncached=input_tokens,
        input_cached=cache_read,
        output_visible=output_tokens,
        # anthropic usage 不拆 reasoning（thinking 併入 output）→ NA。
        reasoning=None,
        billed_input_total=billed_input,
        billed_output_total=output_tokens,
        # cache 寫入 tokens 有計價但不屬互斥欄位 → 殘差。
        unallocated=cache_creation or 0,
    )


def _map_openai(usage: dict) -> dict:
    prompt_tokens = _require_token(usage, "prompt_tokens")
    completion_tokens = _require_token(usage, "completion_tokens")
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    cached = _optional_token(prompt_details, "cached_tokens")
    reasoning = _optional_token(completion_details, "reasoning_tokens")

    if cached is None:
        input_uncached = prompt_tokens
    else:
        if cached > prompt_tokens:
            raise LedgerError(
                f"cached_tokens 超過 prompt_tokens：{cached} > {prompt_tokens}"
            )
        input_uncached = prompt_tokens - cached

    if reasoning is None:
        output_visible = completion_tokens
    else:
        if reasoning > completion_tokens:
            raise LedgerError(
                f"reasoning_tokens 超過 completion_tokens："
                f"{reasoning} > {completion_tokens}"
            )
        output_visible = completion_tokens - reasoning

    return dict(
        input_uncached=input_uncached,
        input_cached=cached,
        output_visible=output_visible,
        reasoning=reasoning,
        billed_input_total=prompt_tokens,
        billed_output_total=completion_tokens,
        unallocated=0,
    )


_PROVIDER_MAPPERS = {
    "anthropic": _map_anthropic,
    "openai": _map_openai,
}


def map_usage(
    provider: str,
    usage: dict,
    *,
    turn: int = 0,
    role: str = "author",
    wall_clock_ms: int = 0,
    prompt_bytes: int = 0,
    generated_bytes: int = 0,
    pricing_snapshot_ref: str = "",
) -> LedgerEntry:
    """provider usage metadata → 互斥 ledger entry（未知 provider fail-closed）。"""
    mapper = _PROVIDER_MAPPERS.get(provider)
    if mapper is None:
        raise LedgerError(f"未知 provider：{provider!r}")
    if not isinstance(usage, dict):
        raise LedgerError("usage 必須是 dict")
    fields = mapper(usage)
    return LedgerEntry(
        turn=turn,
        role=role,
        wall_clock_ms=wall_clock_ms,
        prompt_bytes=prompt_bytes,
        generated_bytes=generated_bytes,
        pricing_snapshot_ref=pricing_snapshot_ref,
        **fields,
    )


def aggregate_work_tokens(entries: list[LedgerEntry]) -> int | None:
    """run 級 T^work 聚合；任一 entry 為 NA → 整體 NA（NA 傳染，§10.1）。"""
    total = 0
    for entry in entries:
        work = entry.work_tokens()
        if work is None:
            return None
        total += work
    return total
