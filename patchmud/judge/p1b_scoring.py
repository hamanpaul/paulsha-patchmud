"""P1b 確定性 evaluator：由 router／jdiag／codex／claude 紀錄推導混合系統並判 go／no-go。

門檻依 R23 A7（開跑前登錄），三層全部通過才 go：

1. 路由安全（hidden）：程式類誤送 JEV（unsafe route）＝0；JEV_ELIGIBLE 的
   預測至少 75% 送到 JEV（避免幾乎全送 LLM 的假混合）。
2. JEV 切片（hidden 中 non_code 且實際送 JEV 的預測）：false-satisfied＝0、
   macro-F1 ≥ 0.90、insufficient recall ≥ 0.90、誤導變體翻轉＝0。
3. 混合系統（全部 hidden 預測）：false-satisfied＝0、macro-F1 與最佳全 LLM
   對照組差距 ≤ 3pp；且至少一項資源收益：LLM 呼叫數比 Codex 全包少 ≥ 35%、
   平均延遲少 ≥ 30%，或可比較的成本少 ≥ 30%（Codex 無金額時此項不適用）。

計分規則：
- 每題的預測數＝基礎題 1 次、誤導變體 2 次（``p1b.runs_for``）。
- 混合系統 H 同一題同一 run：路由為 JEV 取 ``jdiag`` 判決，否則取 ``codex``
  判決；延遲＝路由延遲＋所選分支延遲。路由技術失敗時依 v3 以 LLM 分支處理並計數。
- 判決缺漏（錯誤）算錯；翻轉＝變體判決與同系統基礎題 run 0 判決不同。
- 任一 arm 的紀錄未齊一律 no-go（資料不足），不追加樣本。
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from patchmud.judge.bank import Bank
from patchmud.judge.p1b import runs_for
from patchmud.judge.scoring import _latest, classification_metrics

__all__ = ["P1B_THRESHOLDS", "decide_p1b", "render_p1b_report", "summarize_p1b"]

P1B_THRESHOLDS = {
    "unsafe_route_max": 0,
    "eligible_routed_min": 0.75,
    "slice_false_satisfied_max": 0,
    "slice_macro_f1_min": 0.90,
    "slice_insufficient_recall_min": 0.90,
    "slice_misleading_flip_max": 0,
    "hybrid_false_satisfied_max": 0,
    "hybrid_quality_margin": 0.03,
    "llm_call_reduction_min": 0.35,
    "latency_reduction_min": 0.30,
    "cost_reduction_min": 0.30,
}
ARMS = ("router", "jdiag", "codex", "claude")
SYSTEMS = ("hybrid", "codex", "claude", "jdiag")


def _by_arm(records: list, split: str) -> dict:
    grouped = defaultdict(list)
    for record in records:
        if record.get("split") == split:
            grouped[record["provider"]].append(record)
    return {arm: _latest(rows) for arm, rows in grouped.items()}


def _false_satisfied(pairs: list) -> int:
    return sum(1 for gold, pred in pairs if gold != "satisfied" and pred == "satisfied")


def _mean(values: list):
    return round(statistics.fmean(values), 1) if values else None


def summarize_p1b(bank: Bank, records: list, *, split: str = "hidden") -> dict:
    items = [i for i in bank.items if i.split == split]
    by_id = {i.id: i for i in items}
    keys = [(i.id, run) for i in items for run in range(runs_for(i))]
    arms = _by_arm(records, split)

    def rec(arm: str, item_id: str, run: int):
        return arms.get(arm, {}).get((item_id, "clean", run))

    def verdict(arm: str, item_id: str, run: int):
        r = rec(arm, item_id, run)
        return None if r is None or r.get("error") else r.get("verdict")

    coverage = {}
    for arm in ARMS:
        present = sum(1 for item_id, run in keys if (r := rec(arm, item_id, run)) is not None and (r.get("error") or {}).get("kind") != "transport")
        coverage[arm] = {"expected": len(keys), "observed": present, "complete": present == len(keys)}

    routes, router_failures = {}, 0
    for item_id, run in keys:
        r = rec("router", item_id, run)
        if r is None or r.get("error") or not r.get("details"):
            router_failures += 1
            routes[(item_id, run)] = "LLM"
        else:
            routes[(item_id, run)] = r["details"]["route"]

    predictions = {system: {} for system in SYSTEMS}
    walls = {system: [] for system in SYSTEMS}
    for item_id, run in keys:
        for arm in ("codex", "claude", "jdiag"):
            predictions[arm][(item_id, run)] = verdict(arm, item_id, run)
            r = rec(arm, item_id, run)
            if r is not None and r.get("wall_ms") is not None:
                walls[arm].append(r["wall_ms"])
        branch = "jdiag" if routes[(item_id, run)] == "JEV" else "codex"
        predictions["hybrid"][(item_id, run)] = verdict(branch, item_id, run)
        router_rec, branch_rec = rec("router", item_id, run), rec(branch, item_id, run)
        if branch_rec is not None and branch_rec.get("wall_ms") is not None:
            router_ms = (router_rec or {}).get("wall_ms") or 0
            walls["hybrid"].append(router_ms + branch_rec["wall_ms"])

    def pairs(system: str, select=lambda item, key: True) -> list:
        return [(by_id[k[0]].gold, p) for k, p in predictions[system].items() if select(by_id[k[0]], k)]

    def flips(system: str, select=lambda item, key: True) -> dict:
        total = changed = 0
        for (item_id, run), pred in predictions[system].items():
            item = by_id[item_id]
            if item.variant_of is None or not select(item, (item_id, run)):
                continue
            total += 1
            if pred is None or pred != predictions[system].get((item.variant_of, 0)):
                changed += 1
        return {"variants": total, "flips": changed}

    systems = {}
    for system in SYSTEMS:
        all_pairs = pairs(system)
        systems[system] = {
            "overall": classification_metrics(all_pairs),
            "false_satisfied": _false_satisfied(all_pairs),
            "misleading": flips(system),
            "mean_wall_ms": _mean(walls[system]),
            "median_wall_ms": statistics.median(walls[system]) if walls[system] else None,
        }

    eligible = [k for k in keys if by_id[k[0]].route_label == "JEV_ELIGIBLE"]
    routed_eligible = sum(1 for k in eligible if routes[k] == "JEV")
    unsafe = [f"{k[0]}#{k[1]}" for k in keys if routes[k] == "JEV" and by_id[k[0]].route_label == "LLM_REQUIRED"]
    router = {
        "unsafe_route": len(unsafe),
        "unsafe_cases": unsafe,
        "eligible_predictions": len(eligible),
        "eligible_routed_to_jev": routed_eligible,
        "eligible_routed_rate": round(routed_eligible / len(eligible), 6) if eligible else None,
        "router_failures": router_failures,
        "routes": {f"{k[0]}#{k[1]}": v for k, v in routes.items()},
    }

    def in_slice(item, key) -> bool:
        return item.ac_type == "non_code" and routes[key] == "JEV"

    slice_pairs = pairs("jdiag", in_slice)
    slice_metrics = classification_metrics(slice_pairs)
    jev_slice = {
        "predictions": len(slice_pairs),
        "metrics": slice_metrics,
        "false_satisfied": _false_satisfied(slice_pairs),
        "insufficient_recall": slice_metrics["per_class"]["insufficient"]["recall"],
        "insufficient_support": slice_metrics["per_class"]["insufficient"]["support"],
        "misleading": flips("jdiag", in_slice),
    }
    llm_calls = sum(1 for k in keys if routes[k] == "LLM")
    return {
        "split": split,
        "predictions": len(keys),
        "coverage": coverage,
        "router": router,
        "jev_slice": jev_slice,
        "systems": systems,
        "llm_calls": {"hybrid": llm_calls, "codex_all": len(keys)},
    }


def _gate(value, threshold, passed) -> dict:
    return {"value": value, "threshold": threshold, "pass": bool(passed)}


def decide_p1b(summary: dict, *, thresholds: dict | None = None) -> dict:
    t = dict(P1B_THRESHOLDS if thresholds is None else thresholds)
    reasons = []
    incomplete = [arm for arm, c in summary["coverage"].items() if not c["complete"]]
    if incomplete:
        reasons.append(f"資料不足：{', '.join(incomplete)} 的紀錄未齊")
    router, jev_slice, systems = summary["router"], summary["jev_slice"], summary["systems"]
    rate = router["eligible_routed_rate"]
    router_gates = {
        "unsafe_route": _gate(router["unsafe_route"], t["unsafe_route_max"], router["unsafe_route"] <= t["unsafe_route_max"]),
        "eligible_routed_rate": _gate(rate, t["eligible_routed_min"], rate is not None and rate >= t["eligible_routed_min"]),
    }
    has_slice = jev_slice["predictions"] > 0
    slice_gates = {
        "false_satisfied": _gate(jev_slice["false_satisfied"], t["slice_false_satisfied_max"], has_slice and jev_slice["false_satisfied"] <= t["slice_false_satisfied_max"]),
        "macro_f1": _gate(jev_slice["metrics"]["macro_f1"], t["slice_macro_f1_min"], has_slice and jev_slice["metrics"]["macro_f1"] >= t["slice_macro_f1_min"]),
        "insufficient_recall": _gate(
            jev_slice["insufficient_recall"], t["slice_insufficient_recall_min"],
            has_slice and jev_slice["insufficient_support"] > 0 and jev_slice["insufficient_recall"] >= t["slice_insufficient_recall_min"],
        ),
        "misleading_flip": _gate(jev_slice["misleading"]["flips"], t["slice_misleading_flip_max"], has_slice and jev_slice["misleading"]["flips"] <= t["slice_misleading_flip_max"]),
    }
    hybrid = systems["hybrid"]
    best = max(("codex", "claude"), key=lambda s: (systems[s]["overall"]["macro_f1"], s))
    best_f1 = systems[best]["overall"]["macro_f1"]
    calls = summary["llm_calls"]
    call_reduction = round(1 - calls["hybrid"] / calls["codex_all"], 6) if calls["codex_all"] else None
    h_lat, c_lat = hybrid["mean_wall_ms"], systems["codex"]["mean_wall_ms"]
    latency_reduction = round(1 - h_lat / c_lat, 6) if h_lat is not None and c_lat else None
    value = {
        "llm_call_reduction": _gate(call_reduction, t["llm_call_reduction_min"], call_reduction is not None and call_reduction >= t["llm_call_reduction_min"]),
        "latency_reduction": _gate(latency_reduction, t["latency_reduction_min"], latency_reduction is not None and latency_reduction >= t["latency_reduction_min"]),
        "cost_reduction": _gate(None, t["cost_reduction_min"], False),
    }
    hybrid_gates = {
        "false_satisfied": _gate(hybrid["false_satisfied"], t["hybrid_false_satisfied_max"], hybrid["false_satisfied"] <= t["hybrid_false_satisfied_max"]),
        "quality_vs_best_control": _gate(
            {"hybrid": hybrid["overall"]["macro_f1"], "best_control": best, "best_f1": best_f1},
            t["hybrid_quality_margin"],
            hybrid["overall"]["macro_f1"] >= round(best_f1 - t["hybrid_quality_margin"], 6),
        ),
        "resource_value": _gate(
            [name for name, g in value.items() if g["pass"]], "至少一項", any(g["pass"] for g in value.values())
        ),
    }
    for layer, gates in (("路由安全", router_gates), ("JEV 切片", slice_gates), ("混合系統", hybrid_gates)):
        failed = [name for name, gate in gates.items() if not gate["pass"]]
        if failed:
            reasons.append(f"{layer}未過：{', '.join(failed)}")
    return {
        "decision": "go" if not reasons else "no-go",
        "reasons": reasons,
        "router": router_gates,
        "jev_slice": slice_gates,
        "hybrid": hybrid_gates,
        "value": value,
        "thresholds": t,
    }


def _f(value, fmt="{:.3f}") -> str:
    return "—" if value is None else fmt.format(value)


def render_p1b_report(summary: dict, decision: dict, meta: dict) -> str:
    lines = [
        f"# JEV P1b 結果：{meta.get('run_id', '')}",
        "",
        f"- 判定：**{decision['decision']}**",
        *[f"  - {r}" for r in decision["reasons"]],
        f"- 協定凍結：{meta.get('frozen_at', '未凍結')}；dataset `{meta.get('dataset_digest', '')}`",
        f"- 預測數：{summary['predictions']}（基礎題 1 次、誤導變體 2 次）",
        "",
        "## 各系統（全部 hidden 預測）",
        "",
        "| 系統 | macro-F1 | false-satisfied | 誤導翻轉 | 平均延遲 | median 延遲 |",
        "|---|---|---|---|---|---|",
    ]
    names = {"hybrid": "H 混合（JEV 路由）", "codex": "C Codex 全包", "claude": "D Claude 全包", "jdiag": "Jdiag JEV 全包拆解（診斷）"}
    for system, s in summary["systems"].items():
        lines.append(
            f"| {names[system]} | {_f(s['overall']['macro_f1'])} | {s['false_satisfied']} | "
            f"{s['misleading']['flips']}/{s['misleading']['variants']} | {_f(s['mean_wall_ms'], '{:.0f} ms')} | "
            f"{_f(s['median_wall_ms'], '{:.0f} ms')} |"
        )
    router = summary["router"]
    lines += [
        "",
        "## 路由",
        "",
        f"- unsafe route（程式類送 JEV）：{router['unsafe_route']} {router['unsafe_cases']}",
        f"- JEV_ELIGIBLE 送到 JEV：{router['eligible_routed_to_jev']}/{router['eligible_predictions']}（{_f(router['eligible_routed_rate'])}）",
        f"- 路由技術失敗（改走 LLM）：{router['router_failures']}",
        f"- LLM 呼叫：H {summary['llm_calls']['hybrid']}／Codex 全包 {summary['llm_calls']['codex_all']}",
        "",
        "## JEV 切片（non_code 且送 JEV）",
        "",
        f"- 預測數 {summary['jev_slice']['predictions']}；macro-F1 {_f(summary['jev_slice']['metrics']['macro_f1'])}；"
        f"false-satisfied {summary['jev_slice']['false_satisfied']}；insufficient recall "
        f"{_f(summary['jev_slice']['insufficient_recall'])}（support {summary['jev_slice']['insufficient_support']}）；"
        f"誤導翻轉 {summary['jev_slice']['misleading']['flips']}/{summary['jev_slice']['misleading']['variants']}",
        "",
        "## 門檻",
        "",
        "| 層 | 門檻 | 值 | 條件 | 結果 |",
        "|---|---|---|---|---|",
    ]
    for layer_key, layer in (("router", "路由安全"), ("jev_slice", "JEV 切片"), ("hybrid", "混合系統"), ("value", "資源收益")):
        for name, gate in decision[layer_key].items():
            lines.append(f"| {layer} | {name} | {gate['value']} | {gate['threshold']} | {'PASS' if gate['pass'] else 'FAIL'} |")
    return "\n".join(lines) + "\n"
