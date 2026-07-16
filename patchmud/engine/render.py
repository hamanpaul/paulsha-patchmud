"""狀態 renderer：`RunState` → zh-TW 敘事字串（spec §5.3–5.4、報告 §9.4）。

- 文案全部經 `render_zh_tw` 查表；本模組**不得**出現硬編中文字串
  （golden 測試以 AST 掃描 string literal 鎖定）。
- `RunState` 是純 view model：turn loop（Task 13）把 queue／ledger／時鐘
  映射進來，renderer 不觸碰引擎狀態、不產生事件（觀戰／遊玩同源，§5.4）。
- flood 狀態門檻（報告 §7.5）只影響文案，不進任何分數（§8.2）。
- 互斥資源欄位不可得記 ``None``（NA），render 為 ``NA`` 字樣，不記 0（§10.1）。
"""

from __future__ import annotations

from dataclasses import dataclass

from patchmud.engine import render_zh_tw as zh

__all__ = ["IssueView", "RunState", "flood_state_key", "render_state"]

#: 報告 §7.5 flood 門檻：backlog 上限（含）→ 狀態 key；超過最後一檔即 meltdown。
_FLOOD_BANDS: tuple[tuple[int, str], ...] = (
    (1, "stable"),
    (3, "noisy"),
    (5, "flooded"),
)
_FLOOD_TOP = "meltdown"


@dataclass(frozen=True)
class IssueView:
    """queue render 一列：ID／type 英文（結構化協定），summary 為敘事資料。"""

    item_id: str
    type: str
    summary: str


@dataclass(frozen=True)
class RunState:
    """當回合狀態 render 的 view model（queue、資源、flood；§5.3）。"""

    turn: int
    max_turns: int
    encounter_id: str
    open_issues: tuple[IssueView, ...]
    #: 剩餘 wall-clock 秒數；不可得記 None（render 為 NA）。
    seconds_left: int | None
    #: 累積已用 tokens（billed totals）；不可得記 None（render 為 NA）。
    tokens_spent: int | None


def flood_state_key(backlog: int) -> str:
    """backlog → flood 狀態 key（stable/noisy/flooded/meltdown；只供文案）。"""
    for upper, key in _FLOOD_BANDS:
        if backlog <= upper:
            return key
    return _FLOOD_TOP


def _na(value: int | None) -> str:
    return zh.text("na") if value is None else str(value)


def render_state(run_state: RunState) -> str:
    """render 當回合狀態：回合、戰場、議題佇列、資源、洪水壓力。"""
    lines = [
        zh.text(
            "state.turn_header",
            turn=run_state.turn,
            max_turns=run_state.max_turns,
        ),
        zh.text("state.battlefield", encounter_id=run_state.encounter_id),
        zh.text("state.queue_header", count=len(run_state.open_issues)),
    ]
    if run_state.open_issues:
        lines.extend(
            zh.text(
                "state.queue_item",
                item_id=issue.item_id,
                type=issue.type,
                summary=issue.summary,
            )
            for issue in run_state.open_issues
        )
    else:
        lines.append(zh.text("state.queue_empty"))
    lines.append(
        zh.text(
            "state.resources",
            turns_left=max(run_state.max_turns - run_state.turn, 0),
            seconds_left=_na(run_state.seconds_left),
            tokens_spent=_na(run_state.tokens_spent),
        )
    )
    backlog = len(run_state.open_issues)
    state_key = flood_state_key(backlog)
    lines.append(
        zh.text(
            "state.flood",
            backlog=backlog,
            label=zh.text(f"flood.{state_key}.label"),
            desc=zh.text(f"flood.{state_key}.desc"),
        )
    )
    return "\n".join(lines)
