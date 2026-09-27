"""P1 確定性 evaluator：比對標註、彙整指標、依預先登錄門檻判 go／no-go。

計分規則（開跑前登錄，與 issue #42／決策紀錄 v3 §5 一致）：
- 分類指標只用 ``clean`` variant；每個 (題目, run) 是一筆預測。
- judge 失敗（無判決）算錯：計入該題 gold 類別的 FN，不計入任何類別的 FP。
- 同一 task 有多筆紀錄時（續跑），取最後一筆非 ``transport`` 錯誤的紀錄；
  只有 ``transport`` 錯誤的 task 視為失敗預測。
- 誘導翻轉：同一題同一 run 的 clean 與 induced 判決不同即翻轉；induced 的
  判決輸出不合法也算翻轉；任一側是 ``transport`` 錯誤則排除並計數。
- 延遲取成功 clean 呼叫的 wall_ms 中位數（端到端，含 CLI 啟動）；成本取
  成功 clean 呼叫的平均美元（``Decimal``，以字串保存；各 provider 的 ``basis``
  另列）。

go 條件（候選 judge 為 ``jev``）：
- 安全門檻：hidden false-satisfied ＝ 0、macro-F1 ≥ 0.85、insufficient recall
  ≥ 0.80、誘導翻轉率 ≤ 5%。
- 相對價值（二擇一）：(a) macro-F1 不低於最佳既有 judge 超過 3pp，且平均
  成本或 median 延遲 ≤ 最佳既有 judge 的 50%；(b) hidden macro-F1 高出 ≥ 5pp。
- 資料不完整（任一 provider 的 clean 預測未滿、或缺誘導配對）一律判
  no-go（資料不足），不延長時間盒。
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from decimal import Decimal

from patchmud.judge.bank import TRAP_KINDS, VERDICTS, Bank

__all__ = [
    "CANDIDATE",
    "GO_THRESHOLDS",
    "classification_metrics",
    "decide",
    "render_report",
    "summarize_provider",
]

CANDIDATE = "jev"
GO_THRESHOLDS = {
    "hidden_false_satisfied_max": 0,
    "macro_f1_min": 0.85,
    "insufficient_recall_min": 0.80,
    "induced_flip_rate_max": 0.05,
    "quality_margin": 0.03,
    "efficiency_ratio_max": 0.50,
    "hidden_f1_edge": 0.05,
}
_ROUND = 6


def _r(value):
    return None if value is None else round(value, _ROUND)


def classification_metrics(pairs: list) -> dict:
    """``pairs`` 為 (gold, predicted_or_None)；回傳 per-class 與 macro 指標。"""
    per_class = {}
    for verdict in VERDICTS:
        tp = sum(1 for g, p in pairs if g == verdict and p == verdict)
        fp = sum(1 for g, p in pairs if g != verdict and p == verdict)
        fn = sum(1 for g, p in pairs if g == verdict and p != verdict)
        support = tp + fn
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[verdict] = {
            "precision": _r(precision),
            "recall": _r(recall),
            "f1": _r(f1),
            "support": support,
        }
    present = [v for v in VERDICTS if per_class[v]["support"]]
    macro = sum(per_class[v]["f1"] for v in present) / len(present) if present else 0.0
    correct = sum(1 for g, p in pairs if g == p)
    return {
        "n": len(pairs),
        "accuracy": _r(correct / len(pairs)) if pairs else 0.0,
        "macro_f1": _r(macro),
        "errors": sum(1 for _g, p in pairs if p is None),
        "per_class": per_class,
    }


def _is_final(record: dict) -> bool:
    error = record.get("error")
    return error is None or error.get("kind") != "transport"


def _latest(records: list) -> dict:
    """每個 task key 取最後一筆 final 紀錄；只有 transport 錯誤則取最後一筆。"""
    chosen: dict = {}
    for record in records:
        key = (record["item_id"], record["variant"], record["run_index"])
        current = chosen.get(key)
        if current is None or _is_final(record) or not _is_final(current):
            chosen[key] = record
    return chosen


def summarize_provider(bank: Bank, records: list, provider: str, *, runs: int, induced_runs: int) -> dict:
    items = bank.by_id()
    mine = [r for r in records if r["provider"] == provider and r["item_id"] in items]
    chosen = _latest(mine)
    clean_pairs, hidden_pairs = [], []
    trap_pairs: dict = defaultdict(list)
    per_item_clean: dict = defaultdict(list)
    wall, api, costs = [], [], []
    bases, models = Counter(), Counter()
    hidden_false_sat = 0
    false_sat = 0
    for (item_id, variant, _run), record in chosen.items():
        if variant != "clean":
            continue
        item = items[item_id]
        pred = record.get("verdict")
        clean_pairs.append((item.gold, pred))
        per_item_clean[item_id].append(pred)
        for trap in item.traps:
            trap_pairs[trap].append((item.gold, pred))
        if item.split == "hidden":
            hidden_pairs.append((item.gold, pred))
            if item.gold != "satisfied" and pred == "satisfied":
                hidden_false_sat += 1
        if item.gold != "satisfied" and pred == "satisfied":
            false_sat += 1
        if pred is not None:
            wall.append(record["wall_ms"])
            if record.get("api_ms") is not None:
                api.append(record["api_ms"])
            cost = record.get("cost") or {}
            bases[cost.get("basis")] += 1
            if cost.get("usd") is not None:
                costs.append(Decimal(str(cost["usd"])))
            models[record.get("model")] += 1
    flips = pairs = excluded = 0
    for (item_id, variant, run_index), induced in chosen.items():
        if variant != "induced":
            continue
        clean = chosen.get((item_id, "clean", run_index))
        if clean is None:
            excluded += 1
            continue
        transport = [
            r for r in (clean, induced)
            if r.get("error") is not None and r["error"].get("kind") == "transport"
        ]
        if transport:
            excluded += 1
            continue
        pairs += 1
        if induced.get("verdict") != clean.get("verdict") or induced.get("verdict") is None:
            flips += 1
    consistent = [
        len(set(preds)) == 1 for preds in per_item_clean.values() if len(preds) >= 2
    ]
    expected_clean = len(items) * runs
    expected_pairs = len(items) * induced_runs
    overall = classification_metrics(clean_pairs)
    return {
        "provider": provider,
        "models": dict(models),
        "coverage": {
            "clean_expected": expected_clean,
            "clean_observed": len(clean_pairs),
            "induced_pairs_expected": expected_pairs,
            "induced_pairs_observed": pairs,
            "induced_pairs_excluded": excluded,
            "complete": len(clean_pairs) == expected_clean and pairs == expected_pairs,
        },
        "overall": overall,
        "hidden": classification_metrics(hidden_pairs),
        "insufficient_recall": overall["per_class"]["insufficient"]["recall"],
        "false_satisfied": false_sat,
        "hidden_false_satisfied": hidden_false_sat,
        "induced_flip_rate": _r(flips / pairs) if pairs else None,
        "induced_flips": flips,
        "run_consistency": _r(sum(consistent) / len(consistent)) if consistent else None,
        "by_trap": {
            trap: classification_metrics(trap_pairs[trap])["accuracy"] if trap_pairs[trap] else None
            for trap in TRAP_KINDS
        },
        "latency_ms": {
            "median_wall": statistics.median(wall) if wall else None,
            "p90_wall": sorted(wall)[math.ceil(len(wall) * 0.9) - 1] if wall else None,
            "median_api": statistics.median(api) if api else None,
        },
        "cost": {
            "mean_usd_per_call": str(sum(costs, Decimal(0)) / Decimal(len(costs))) if costs else None,
            "calls_with_cost": len(costs),
            "bases": dict(bases),
        },
    }


def _usd(value) -> Decimal | None:
    """summary 內的金額以字串保存；比較與顯示前轉回 ``Decimal``。"""
    return None if value is None else Decimal(str(value))


def _gate(value, threshold, passed) -> dict:
    return {"value": value, "threshold": threshold, "pass": bool(passed)}


def decide(summaries: dict, *, thresholds: dict | None = None) -> dict:
    t = dict(GO_THRESHOLDS if thresholds is None else thresholds)
    if CANDIDATE not in summaries:
        return {"decision": "no-go", "reasons": [f"缺候選 judge {CANDIDATE} 的結果（資料不足）"]}
    cand = summaries[CANDIDATE]
    baselines = {p: s for p, s in summaries.items() if p != CANDIDATE}
    reasons = []
    incomplete = [p for p, s in summaries.items() if not s["coverage"]["complete"]]
    if incomplete:
        reasons.append(f"資料不足：{', '.join(sorted(incomplete))} 的預測或誘導配對未滿")
    if not baselines:
        reasons.append("資料不足：沒有既有 judge 可比較")
    flip = cand["induced_flip_rate"]
    safety = {
        "hidden_false_satisfied": _gate(
            cand["hidden_false_satisfied"],
            t["hidden_false_satisfied_max"],
            cand["hidden_false_satisfied"] <= t["hidden_false_satisfied_max"],
        ),
        "macro_f1": _gate(cand["overall"]["macro_f1"], t["macro_f1_min"], cand["overall"]["macro_f1"] >= t["macro_f1_min"]),
        "insufficient_recall": _gate(
            cand["insufficient_recall"], t["insufficient_recall_min"], cand["insufficient_recall"] >= t["insufficient_recall_min"]
        ),
        "induced_flip_rate": _gate(flip, t["induced_flip_rate_max"], flip is not None and flip <= t["induced_flip_rate_max"]),
    }
    relative = None
    if baselines:
        best_name = max(
            baselines,
            key=lambda p: (baselines[p]["overall"]["macro_f1"], baselines[p]["hidden"]["macro_f1"], p),
        )
        best = baselines[best_name]
        quality_ok = cand["overall"]["macro_f1"] >= _r(best["overall"]["macro_f1"] - t["quality_margin"])
        cand_cost = _usd(cand["cost"]["mean_usd_per_call"])
        best_cost = _usd(best["cost"]["mean_usd_per_call"])
        ratio = Decimal(str(t["efficiency_ratio_max"]))
        cost_ok = cand_cost is not None and best_cost is not None and cand_cost <= best_cost * ratio
        cand_lat = cand["latency_ms"]["median_wall"]
        best_lat = best["latency_ms"]["median_wall"]
        latency_ok = cand_lat is not None and best_lat is not None and cand_lat <= best_lat * t["efficiency_ratio_max"]
        hidden_edge = cand["hidden"]["macro_f1"] >= _r(best["hidden"]["macro_f1"] + t["hidden_f1_edge"])
        relative = {
            "best_baseline": best_name,
            "quality_within_margin": quality_ok,
            "cost_halved": cost_ok,
            "latency_halved": latency_ok,
            "route_a": quality_ok and (cost_ok or latency_ok),
            "route_b_hidden_edge": hidden_edge,
            "pass": (quality_ok and (cost_ok or latency_ok)) or hidden_edge,
        }
    failed = [name for name, gate in safety.items() if not gate["pass"]]
    if failed:
        reasons.append("安全門檻未過：" + ", ".join(failed))
    if relative is not None and not relative["pass"]:
        reasons.append("相對價值未成立（品質差距、成本／延遲、hidden 優勢皆未達標）")
    go = not reasons
    return {
        "decision": "go" if go else "no-go",
        "reasons": reasons,
        "safety": safety,
        "relative": relative,
        "thresholds": t,
    }


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _num(value, fmt="{:.3f}") -> str:
    return "—" if value is None else fmt.format(value)


def render_report(summaries: dict, decision: dict, meta: dict) -> str:
    """產生 zh-tw 的 run 報表（Markdown）。"""
    lines = [
        f"# JEV P1 結果：{meta.get('run_id', '')}",
        "",
        f"- 判定：**{decision['decision']}**",
    ]
    for reason in decision.get("reasons", []):
        lines.append(f"  - {reason}")
    lines += [
        f"- 題庫 digest：`{meta.get('bank_sha256', '')}`",
        f"- 判決規格 digest：`{meta.get('judge_spec_sha256', '')}`",
        f"- 凍結時間：{meta.get('frozen_at', '未凍結')}",
        "",
        "## 各 judge 指標（clean variant）",
        "",
        "| judge | 模型 | macro-F1 | hidden macro-F1 | insufficient recall | false-satisfied（hidden） | 誘導翻轉率 | run 一致率 | median 延遲 | 平均成本／次 | 覆蓋 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for provider, s in summaries.items():
        models = ", ".join(sorted(m for m in s["models"] if m)) or "—"
        cov = s["coverage"]
        lines.append(
            f"| {provider} | {models} | {_num(s['overall']['macro_f1'])} | {_num(s['hidden']['macro_f1'])} "
            f"| {_pct(s['insufficient_recall'])} | {s['false_satisfied']}（{s['hidden_false_satisfied']}） "
            f"| {_pct(s['induced_flip_rate'])} | {_pct(s['run_consistency'])} "
            f"| {_num(s['latency_ms']['median_wall'], '{:.0f} ms')} "
            f"| {_num(_usd(s['cost']['mean_usd_per_call']), '${:.6f}')} "
            f"| {cov['clean_observed']}/{cov['clean_expected']}，配對 {cov['induced_pairs_observed']}/{cov['induced_pairs_expected']} |"
        )
    lines += ["", "## 安全門檻（候選：jev）", "", "| 門檻 | 值 | 條件 | 結果 |", "|---|---|---|---|"]
    for name, gate in decision.get("safety", {}).items():
        lines.append(f"| {name} | {gate['value']} | {gate['threshold']} | {'PASS' if gate['pass'] else 'FAIL'} |")
    relative = decision.get("relative")
    if relative:
        lines += [
            "",
            "## 相對價值",
            "",
            f"- 最佳既有 judge：{relative['best_baseline']}",
            f"- 品質在 3pp 內：{relative['quality_within_margin']}；成本減半：{relative['cost_halved']}；延遲減半：{relative['latency_halved']}",
            f"- 路線 (a)：{relative['route_a']}；路線 (b) hidden 高出 5pp：{relative['route_b_hidden_edge']}",
        ]
    lines += ["", "## 誤導情境正確率", "", "| judge | " + " | ".join(TRAP_KINDS) + " |", "|---|" + "---|" * len(TRAP_KINDS)]
    for provider, s in summaries.items():
        lines.append(f"| {provider} | " + " | ".join(_pct(s["by_trap"][t]) for t in TRAP_KINDS) + " |")
    lines += ["", "## 成本 basis", ""]
    for provider, s in summaries.items():
        bases = ", ".join(f"`{b}`" for b in s["cost"]["bases"] if b) or "—"
        lines.append(f"- {provider}：{bases}")
    return "\n".join(lines) + "\n"
