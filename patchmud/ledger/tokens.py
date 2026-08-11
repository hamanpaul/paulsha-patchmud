"""互斥 token ledger：per-provider usage mapping（spec §10.1、報告 §5.6.1）。

原則：
- 互斥欄位 ``input_uncached / input_cached / output_visible / reasoning``
  只記 provider 明確揭露的量；不可得記 ``None``（NA），不得記 0。
- ``billed_input_total / billed_output_total`` 為 provider 帳面計價總量，
  永遠照抄；計費一律以 billed totals 計算（F17）。
- 拆不動的殘差（如 cache 寫入 tokens）進 ``unallocated``；殘差為負代表
  provider 資料不一致，fail-closed。
- ``human`` provider（``patchmud play``，spec §5.4）：無任何 token 量測
  事實——互斥欄位**與 billed totals** 全記 ``None``（NA 不記 0，§10.1）；
  usage_raw 必須是空 dict，NA entry 進計價一律 fail-closed。
- CLI-based provider（``codex`` / ``agy``）欄位名各自成一格，但語意與
  ``openai`` 相同（cached ⊆ input、reasoning ⊆ output）；差異吸收在
  mapper 內，adapter 一律原樣透傳。
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
    #: billed totals：model provider 必為 int；human run 無計費事實 → None（NA）。
    billed_input_total: int | None
    billed_output_total: int | None
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
        for name in ("unallocated", "api_calls", "tool_calls", "wall_clock_ms",
                     "prompt_bytes", "generated_bytes", "turn"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise LedgerError(f"{name} 必須是非負整數：{value!r}")
        for name in ("input_uncached", "input_cached", "output_visible", "reasoning",
                     "billed_input_total", "billed_output_total"):
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


def _split_subset(total: int, part: int | None, *, whole: str, name: str) -> int:
    """``part ⊆ total`` 的差集；part 超出 total 代表 provider 資料不一致 → 拒收。"""
    if part is None:
        return total
    if part > total:
        raise LedgerError(f"{name} 超過 {whole}：{part} > {total}")
    return total - part


def _map_codex(usage: dict) -> dict:
    """codex CLI（``codex exec --json`` 的 ``turn.completed.usage``）。

    欄位名自成一格，但語意與 openai 一致（實測 gpt-5.6-luna：
    ``output_tokens`` 29 − ``reasoning_output_tokens`` 21 = 8 ≈ 可見回覆量）：
    ``cached_input_tokens ⊆ input_tokens``、
    ``reasoning_output_tokens ⊆ output_tokens``。
    ``cache_write_input_tokens`` 有計價但不屬互斥欄位 → 殘差。
    """
    input_tokens = _require_token(usage, "input_tokens")
    output_tokens = _require_token(usage, "output_tokens")
    cached = _optional_token(usage, "cached_input_tokens")
    reasoning = _optional_token(usage, "reasoning_output_tokens")
    cache_write = _optional_token(usage, "cache_write_input_tokens")
    return dict(
        input_uncached=_split_subset(
            input_tokens, cached, whole="input_tokens", name="cached_input_tokens"
        ),
        input_cached=cached,
        output_visible=_split_subset(
            output_tokens,
            reasoning,
            whole="output_tokens",
            name="reasoning_output_tokens",
        ),
        reasoning=reasoning,
        billed_input_total=input_tokens,
        billed_output_total=output_tokens,
        unallocated=cache_write or 0,
    )


def _map_agy(usage: dict) -> dict:
    """agy CLI（``agy --print --output-format json`` 的 ``usage``）。

    語意同 openai（實測 gemini-3.6-flash-high：``output_tokens`` 459 −
    ``thinking_tokens`` 452 = 7 ≈ 可見回覆量）：``cache_read_tokens ⊆
    input_tokens``、``thinking_tokens ⊆ output_tokens``。

    ``total_tokens`` 是 ``input + output`` 的重述而非獨立計價量，故不進
    ledger；provider 若給出對不上的值，代表資料不一致 → fail-closed。
    """
    input_tokens = _require_token(usage, "input_tokens")
    output_tokens = _require_token(usage, "output_tokens")
    cached = _optional_token(usage, "cache_read_tokens")
    thinking = _optional_token(usage, "thinking_tokens")

    total = _optional_token(usage, "total_tokens")
    if total is not None and total != input_tokens + output_tokens:
        raise LedgerError(
            f"total_tokens 與 input+output 不一致：{total} != "
            f"{input_tokens} + {output_tokens}"
        )

    return dict(
        input_uncached=_split_subset(
            input_tokens, cached, whole="input_tokens", name="cache_read_tokens"
        ),
        input_cached=cached,
        output_visible=_split_subset(
            output_tokens, thinking, whole="output_tokens", name="thinking_tokens"
        ),
        reasoning=thinking,
        billed_input_total=input_tokens,
        billed_output_total=output_tokens,
        unallocated=0,
    )


def _map_human(usage: dict) -> dict:
    """human adapter（``patchmud play``，spec §5.4）：全欄位 NA。

    人類對局沒有任何 token 量測事實；usage_raw 帶值代表佈線錯誤，
    fail-closed（NA 不記 0，§10.1）。
    """
    if usage:
        raise LedgerError(
            f"human adapter 的 usage_raw 必須是空 dict（全欄位 NA）：{sorted(usage)}"
        )
    return dict(
        input_uncached=None,
        input_cached=None,
        output_visible=None,
        reasoning=None,
        billed_input_total=None,
        billed_output_total=None,
        unallocated=0,
    )


_PROVIDER_MAPPERS = {
    "anthropic": _map_anthropic,
    "openai": _map_openai,
    "codex": _map_codex,
    "agy": _map_agy,
    "human": _map_human,
}


def map_usage(
    provider: str,
    usage: dict,
    *,
    turn: int = 0,
    role: str = "author",
    tool_calls: int = 0,
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
        tool_calls=tool_calls,
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


def aggregate_billed_totals(
    entries: list[LedgerEntry],
) -> tuple[int | None, int | None]:
    """run 級 billed totals 聚合：``(Σ input, Σ output)``。

    任一 entry 的 billed 欄位為 NA（human run）→ 該欄整體 NA（NA 傳染，
    §10.1）。loop（result.yaml）與 replay L1 重算共用此單一實作，保證
    位元一致。
    """
    total_input: int | None = 0
    total_output: int | None = 0
    for entry in entries:
        if total_input is not None:
            total_input = (
                None
                if entry.billed_input_total is None
                else total_input + entry.billed_input_total
            )
        if total_output is not None:
            total_output = (
                None
                if entry.billed_output_total is None
                else total_output + entry.billed_output_total
            )
    return total_input, total_output
