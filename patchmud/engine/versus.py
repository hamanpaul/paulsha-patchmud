"""versus 並排對戰視圖：多個模型各自打同一關（隔離 run），同步並排呈現。

**隔離不可動搖**：每個模型在自己的凍結 sandbox 打同一道 issue，互不干擾——
否則一個模型的 patch 會改變另一個看到的狀態，受控比較即失效（研究報告 §13、
§17.2）。versus 只是把 N 場「隔離 run」的結果**攤成並排視覺**：逐回合看每個
模型做了什麼，最後一張記分板就是 benchmark 本身。

- `render_versus` 純函數（events + result 進、字串出）；文案一律經 render pack。
- `run_versus` orchestration：依序跑每個模型的隔離 run，載入其 events / result。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from patchmud.engine import narration
from patchmud.engine import render_zh_tw as zh
from patchmud.store.run_store import RunStore

__all__ = ["VersusEntry", "render_versus", "run_versus"]

_BASELINE = "baseline"
_TURN = "turn"


@dataclass(frozen=True)
class VersusEntry:
    """一個模型的隔離 run 投影，供並排呈現。"""

    model: str
    events: list[dict]
    result: dict
    cost: str = "NA"


# ---------------------------------------------------------------------------
# 純渲染
# ---------------------------------------------------------------------------


def render_versus(
    entries: list[VersusEntry],
    encounter: str | None = None,
    briefing: str | None = None,
) -> str:
    """同步並排：開場 bug 說明、逐回合白話敘述、記分板、收尾「誰贏在哪」判詞。"""
    if not entries:
        return zh.text("versus.scoreboard_header")

    if encounter is None:
        encounter = str(entries[0].result.get("run_id", zh.text("na")))
    loadout = entries[0].result.get("loadout", zh.text("na"))
    names = " / ".join(_short(e.model) for e in entries)

    timelines = [_timeline(e.events) for e in entries]
    max_round = max((len(t) - 1 for t in timelines), default=0)

    lines = [
        zh.text("versus.header", encounter=encounter, loadout=loadout),
        zh.text("versus.participants", names=names),
    ]
    if briefing:
        lines.append(zh.text("versus.briefing", text=briefing))
    lines.append("")

    for r in range(max_round + 1):
        lines.append(
            zh.text("versus.baseline_header")
            if r == 0
            else zh.text("versus.round_header", n=r)
        )
        for entry, timeline in zip(entries, timelines):
            lines.append(_model_line(entry.model, timeline, r))
        lines.append("")

    lines.append(zh.text("versus.scoreboard_header"))
    for entry in entries:
        lines.append(_score_row(entry))
    lines.append("")
    lines.append(zh.text("versus.verdict_header"))
    lines.append(narration.verdict(entries))
    return "\n".join(lines).rstrip()


def _model_line(model: str, timeline: list[dict], r: int) -> str:
    if r >= len(timeline):
        return zh.text("versus.model_done", model=_short(model))
    return zh.text(
        "versus.model_line_v2",
        model=_short(model),
        narration=narration.narrate_round(timeline[r]),
    )


def _score_row(entry: VersusEntry) -> str:
    result = entry.result
    clear = result.get("clear")
    label = zh.text("run.clear_yes") if clear == 1 else zh.text("run.clear_no")
    power = narration.format_power(_power_total(result))
    turns = result.get("turns", zh.text("na"))
    cost = (
        zh.text("versus.cost_na")
        if entry.cost == "NA"
        else zh.text("versus.cost", cost=entry.cost)
    )
    return zh.text(
        "versus.score_row",
        model=_short(entry.model),
        result=label,
        power=power,
        turns=turns,
        cost=cost,
    )


def _timeline(events: list[dict]) -> list[dict]:
    """events → 每回合 {baseline, action, outcome, resolved, backlog_before/after}。

    round 0 = baseline。欄位供 :func:`narration.narrate_round` 推導白話。
    """
    rounds: list[dict] = []
    prev_open: set[str] = set()
    prev_backlog = 0
    for event in events:
        etype = event.get("type")
        if etype not in (_BASELINE, _TURN):
            continue
        queue = event.get("queue") or {}
        open_items = queue.get("open_items") or []
        open_ids = {str(i.get("item_id")) for i in open_items if isinstance(i, dict)}
        backlog = queue.get("b_t", len(open_ids))
        if etype == _BASELINE:
            rounds.append(
                {
                    "baseline": True,
                    "action": None,
                    "outcome": None,
                    "resolved": [],
                    "backlog_before": backlog,
                    "backlog_after": backlog,
                }
            )
        else:
            rounds.append(
                {
                    "baseline": False,
                    "action": event.get("action"),
                    "outcome": event.get("outcome"),
                    "resolved": sorted(prev_open - open_ids),
                    "backlog_before": prev_backlog,
                    "backlog_after": backlog,
                }
            )
        prev_open, prev_backlog = open_ids, backlog
    return rounds


def _power_total(result: dict) -> object:
    power = result.get("power")
    if isinstance(power, dict) and "total" in power:
        return power["total"]
    return zh.text("na")


def _short(model: str) -> str:
    """model spec 顯示用短名：去掉 scripted 檔路徑等雜訊，保留可辨識部分。"""
    if model.startswith("scripted:"):
        return "scripted:" + Path(model.split(":", 1)[1]).stem
    return model


# ---------------------------------------------------------------------------
# orchestration（隔離依序跑）
# ---------------------------------------------------------------------------


def run_versus(
    encounter_dir: Path,
    model_specs: list[str],
    loadout_spec: str,
    runs_root: Path,
    *,
    run_one,
    now: str | None = None,
) -> list[VersusEntry]:
    """依序跑每個模型的隔離 run，載入 events / result 組成並排資料。

    ``run_one(encounter_dir, model_spec, loadout_spec, runs_root, run_id) -> RunResult``
    由 CLI 注入（真佈線 `run_cli`）；測試可注入 fake。每個模型獨立 run_id、
    獨立 sandbox（materialize 於 run_one 內），彼此零共享。
    """
    stamp = now if now is not None else time.strftime("%Y%m%d%H%M%S")
    runs_root = Path(runs_root)
    entries: list[VersusEntry] = []
    for idx, spec in enumerate(model_specs):
        run_id = f"versus-{stamp}-{idx}-{_slug(spec)}"
        result = run_one(encounter_dir, spec, loadout_spec, runs_root, run_id)
        run_dir = runs_root / run_id
        events = RunStore.open(run_dir).load_events()
        result_yaml = yaml.safe_load((run_dir / "result.yaml").read_text(encoding="utf-8"))
        entries.append(
            VersusEntry(model=spec, events=events, result=result_yaml, cost=_cost_of(result))
        )
    return entries


def _cost_of(run_result) -> str:
    """RunResult 的可顯示成本；MVP 無 pricing snapshot → NA（§10.2）。"""
    economy = getattr(run_result, "result", {}).get("economy") if run_result else None
    return "NA" if economy in (None, "NA") else str(economy)


def _slug(spec: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in spec)[:40]
