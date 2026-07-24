"""離線觀戰 viewer：封存 events → 逐回合 zh-TW 戰報（spec §5.4；plan Task 23）。

- 資料只來自封存 events（`RunStore.load_events()` 同源，與 replay L1 同一
  資料面）與 result.yaml；不重新執行任何 probe 或模型呼叫、不觸碰
  `IsolationRunner`、不寫回 run 目錄、不新增事件——純視圖層（spec §5.4）。
- 敘事文字一律經 zh-TW render pack 查表（本模組不得硬編中文字串，golden
  測試以 AST 掃描鎖定）；命令關鍵字、item id、probe id、end_reason 與
  artifact 格式維持英文。
- queue 變化（新增／解決）以相鄰事件的 queue snapshot open_items 差集推導；
  欄位缺漏、未知事件型別、回合不存在一律 fail-closed `WatchError`。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence

from patchmud.engine import render_zh_tw as zh
from patchmud.engine.render import flood_state_key

__all__ = ["LiveSpectator", "WatchError", "render_battle_report", "render_turn"]

_BASELINE = "baseline"
_TURN = "turn"
_FINAL = "final"


class WatchError(Exception):
    """watch 操作性失敗（事件缺漏、回合不存在、非回合制 run），fail-closed。"""


def _print_flush(text: str) -> None:
    print(text, flush=True)


class LiveSpectator:
    """邊玩邊看：run loop 每 append 一個 event 就即時渲染成 zh-TW 戰報段落。

    與離線 `watch` 同源渲染，但由 loop 於執行中逐事件推送——真模型對局時每回合
    之間的 API 延遲即成為自然節奏，觀眾看到「這回合 agent 做了什麼、queue 怎麼
    變、洪水壓力升降」as it happens。純視圖：不改變任何評分資料流。
    """

    def __init__(
        self,
        out: Callable[[str], None] | None = None,
        *,
        delay: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # 預設輸出強制 flush：確保每段戰報在終端機當下就浮現（不被 stdout
        # 區塊緩衝憋到最後），live 才有逐回合節奏。
        self._out = out if out is not None else _print_flush
        self._delay = max(0.0, delay)
        self._sleep = sleep
        self._prev_queue: dict | None = None

    def feed(self, event: dict) -> None:
        etype = _field(event, "type")
        if etype == _BASELINE:
            self._out(_render_baseline(event))
            self._prev_queue = _field(event, "queue")
            self._pace()
        elif etype == _TURN:
            self._out(_render_turn_event(event, self._prev_queue))
            self._prev_queue = _field(event, "queue")
            self._pace()
        elif etype == _FINAL:
            self._out(_render_final(event))

    def _pace(self) -> None:
        if self._delay > 0:
            self._sleep(self._delay)


def render_battle_report(events: Sequence[dict], result: dict) -> str:
    """全場戰報：開場基線 → 逐回合（行動、probe、queue 變化、flood）→ 終局。"""
    if not isinstance(result, dict):
        raise WatchError(zh.text("watch.error.result_not_mapping"))
    sections = [
        zh.text(
            "watch.report_header",
            run_id=result.get("run_id", zh.text("na")),
            loadout=result.get("loadout", zh.text("na")),
        )
    ]
    prev_queue: dict | None = None
    for event in events:
        etype = _field(event, "type")
        if etype == _BASELINE:
            sections.append(_render_baseline(event))
        elif etype == _TURN:
            sections.append(_render_turn_event(event, prev_queue))
        elif etype == _FINAL:
            sections.append(_render_final(event))
            continue  # final 不更新 prev_queue（其後不應再有 turn）
        else:
            raise WatchError(zh.text("watch.error.unknown_event", type=etype))
        prev_queue = _field(event, "queue")
    return "\n\n".join(sections)


def render_turn(events: Sequence[dict], n: int) -> str:
    """只輸出第 ``n`` 回合的段落；找不到該回合 → :class:`WatchError`。"""
    prev_queue: dict | None = None
    for event in events:
        etype = _field(event, "type")
        if etype == _TURN and _field(event, "turn") == n:
            return _render_turn_event(event, prev_queue)
        if etype in (_BASELINE, _TURN):
            prev_queue = _field(event, "queue")
        elif etype != _FINAL:
            raise WatchError(zh.text("watch.error.unknown_event", type=etype))
    raise WatchError(zh.text("watch.error.turn_not_found", turn=n))


# ---------------------------------------------------------------------------
# 段落 render（每段皆純函數：events 進、字串出）
# ---------------------------------------------------------------------------


def _render_baseline(event: dict) -> str:
    queue = _field(event, "queue")
    lines = [zh.text("watch.baseline_header")]
    lines.extend(_probe_lines(_field(event, "probes")))
    lines.extend(_queue_open_lines(queue))
    lines.append(_flood_line(queue))
    return "\n".join(lines)


def _render_turn_event(event: dict, prev_queue: dict | None) -> str:
    if prev_queue is None:
        raise WatchError(zh.text("watch.error.no_baseline"))
    queue = _field(event, "queue")
    lines = [zh.text("watch.turn_header", turn=_field(event, "turn"))]

    action = _field(event, "action")
    outcome = _field(event, "outcome")
    if action is None:
        lines.append(zh.text("watch.action_unparsed"))
    else:
        lines.append(
            zh.text(
                "watch.action",
                action=action,
                verdict=zh.text(f"watch.outcome.{outcome}"),
            )
        )
    detail = event.get("detail")
    if detail:
        lines.append(zh.text("watch.detail", detail=detail))

    claim = event.get("claim")
    if claim:
        lines.append(zh.text("watch.claim", claim=claim))

    reviewer = event.get("reviewer_subcall")
    if reviewer:
        lines.append(
            zh.text(
                "watch.reviewer_subcall",
                count=_field(reviewer, "findings"),
                valid=_field(reviewer, "valid"),
            )
        )

    probes = _field(event, "probes")
    if probes:
        lines.extend(_probe_lines(probes))

    lines.append(zh.text("watch.queue_delta_header"))
    lines.extend(_queue_delta_lines(prev_queue, queue))
    lines.append(_flood_line(queue))
    return "\n".join(lines)


def _render_final(event: dict) -> str:
    queue = _field(event, "queue")
    lines = [
        zh.text("watch.final_header"),
        zh.text(
            "watch.final_summary",
            end_reason=_field(event, "end_reason"),
            turns=_field(event, "turns"),
            clear=_field(event, "clear"),
        ),
    ]
    lines.extend(_probe_lines(_field(event, "probes")))
    lines.extend(_queue_open_lines(queue))
    lines.append(_flood_line(queue))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 共用小段（probe 清單、queue 視圖、flood 壓力）
# ---------------------------------------------------------------------------


def _probe_lines(probes: dict) -> list[str]:
    lines = [zh.text("loop.probe_header")]
    lines.extend(
        zh.text("loop.probe_line", probe_id=probe_id, status=status)
        for probe_id, status in probes.items()
    )
    return lines


def _queue_open_lines(queue: dict) -> list[str]:
    items = _open_items(queue)
    lines = [zh.text("watch.queue_open_header", count=len(items))]
    lines.extend(
        zh.text(
            "watch.queue_open_item",
            item_id=_field(item, "item_id"),
            type=_field(item, "type"),
        )
        for item in items
    )
    return lines


def _queue_delta_lines(prev_queue: dict, queue: dict) -> list[str]:
    """相鄰 snapshot 的 open_items 差集 → 新增／解決敘事（純推導，零重算）。"""
    prev_items = {_field(i, "item_id"): i for i in _open_items(prev_queue)}
    cur_items = {_field(i, "item_id"): i for i in _open_items(queue)}
    spawned = [i for item_id, i in cur_items.items() if item_id not in prev_items]
    resolved = [i for item_id, i in prev_items.items() if item_id not in cur_items]
    lines = [
        zh.text(
            "watch.queue_spawned",
            item_id=_field(item, "item_id"),
            type=_field(item, "type"),
        )
        for item in spawned
    ]
    lines.extend(
        zh.text(
            "watch.queue_resolved",
            item_id=_field(item, "item_id"),
            type=_field(item, "type"),
        )
        for item in resolved
    )
    if not lines:
        lines.append(zh.text("watch.queue_unchanged"))
    return lines


def _flood_line(queue: dict) -> str:
    backlog = _field(queue, "b_t")
    state_key = flood_state_key(backlog)
    return zh.text(
        "state.flood",
        backlog=backlog,
        label=zh.text(f"flood.{state_key}.label"),
        desc=zh.text(f"flood.{state_key}.desc"),
    )


def _open_items(queue: dict) -> Iterable[dict]:
    return _field(queue, "open_items")


def _field(record: dict, key: str):
    """封存欄位讀取 fail-closed：缺欄位即 :class:`WatchError`，不靜默補值。"""
    try:
        return record[key]
    except (KeyError, TypeError) as exc:
        etype = record.get("type") if isinstance(record, dict) else None
        raise WatchError(
            zh.text("watch.error.missing_field", field=key, type=etype)
        ) from exc
