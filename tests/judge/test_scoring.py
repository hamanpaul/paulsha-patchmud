"""確定性 evaluator：手算 F1、失敗預測、翻轉、續跑取值與 go／no-go 門檻。"""

from __future__ import annotations

import pytest
import yaml

from patchmud.judge.bank import load_bank
from patchmud.judge.scoring import (
    classification_metrics,
    decide,
    render_report,
    summarize_provider,
)


def test_classification_metrics_hand_computed():
    pairs = [
        ("satisfied", "satisfied"),
        ("satisfied", "not_satisfied"),
        ("not_satisfied", "not_satisfied"),
        ("not_satisfied", "satisfied"),
        ("insufficient", "insufficient"),
        ("insufficient", None),
    ]
    m = classification_metrics(pairs)
    assert m["per_class"]["satisfied"]["f1"] == pytest.approx(0.5)
    assert m["per_class"]["not_satisfied"]["f1"] == pytest.approx(0.5)
    assert m["per_class"]["insufficient"] == {"precision": 1.0, "recall": 0.5, "f1": pytest.approx(0.666667), "support": 2}
    assert m["macro_f1"] == pytest.approx(0.555556)
    assert m["accuracy"] == 0.5 and m["errors"] == 1


@pytest.fixture
def bank(tmp_path):
    def item(item_id, gold, traps=()):
        return {
            "id": item_id,
            "gold": gold,
            "traps": list(traps),
            "source": {"kind": "public-fixture", "ref": "synthetic"},
            "rationale": "r",
            "criterion": "c",
            "evidence": [{"id": "e1", "kind": "log", "content": "x"}],
        }

    root = tmp_path / "bank"
    root.mkdir()
    (root / "public.yaml").write_text(yaml.safe_dump({"items": [item("alpha", "satisfied"), item("beta", "insufficient")]}))
    (root / "hidden.yaml").write_text(yaml.safe_dump({"items": [item("gamma", "not_satisfied", ["looks_fixed"])]}))
    return load_bank(root)


GOLD = {"alpha": "satisfied", "beta": "insufficient", "gamma": "not_satisfied"}


def _rec(provider, item, variant, run, verdict, *, wall=400, usd="0.00002", error=None):
    return {
        "provider": provider,
        "item_id": item,
        "variant": variant,
        "run_index": run,
        "verdict": None if error else verdict,
        "wall_ms": wall,
        "api_ms": None,
        "cost": None if error else {"usd": usd, "basis": f"{provider}-basis"},
        "model": f"{provider}-m",
        "error": {"kind": error} if error else None,
    }


def _records(provider, *, clean=None, induced=None, wall=400, usd="0.00002"):
    clean = clean or GOLD
    induced = induced or GOLD
    out = [_rec(provider, i, "clean", r, clean[i], wall=wall, usd=usd) for r in range(2) for i in GOLD]
    out += [_rec(provider, i, "induced", 0, induced[i], wall=wall, usd=usd) for i in GOLD]
    return out


def _summaries(bank, records):
    return {
        p: summarize_provider(bank, records, p, runs=2, induced_runs=1)
        for p in sorted({r["provider"] for r in records}, key=lambda p: p != "jev")
    }


def test_perfect_and_faster_candidate_is_go(bank):
    records = _records("jev") + _records("claude", wall=5000, usd="0.002")
    summaries = _summaries(bank, records)
    jev = summaries["jev"]
    assert jev["overall"]["macro_f1"] == 1.0 and jev["hidden_false_satisfied"] == 0
    assert jev["induced_flip_rate"] == 0.0 and jev["run_consistency"] == 1.0
    assert jev["coverage"]["complete"] and jev["by_trap"]["looks_fixed"] == 1.0
    decision = decide(summaries)
    assert decision["decision"] == "go", decision["reasons"]
    assert decision["relative"]["best_baseline"] == "claude"
    assert decision["relative"]["cost_halved"] and decision["relative"]["latency_halved"]
    assert "**go**" in render_report(summaries, decision, {"run_id": "t"})


def test_hidden_false_satisfied_blocks_go(bank):
    wrong = dict(GOLD, gamma="satisfied")
    summaries = _summaries(bank, _records("jev", clean=wrong) + _records("claude", wall=5000))
    assert summaries["jev"]["hidden_false_satisfied"] == 2
    decision = decide(summaries)
    assert decision["decision"] == "no-go"
    assert not decision["safety"]["hidden_false_satisfied"]["pass"]


def test_induced_flip_rate_counts_flips_and_invalid_outputs(bank):
    records = _records("jev", induced=dict(GOLD, alpha="not_satisfied"))
    records[-1] = _rec("jev", "gamma", "induced", 0, None, error="invalid_output")
    summary = summarize_provider(bank, records, "jev", runs=2, induced_runs=1)
    assert summary["induced_flips"] == 2 and summary["induced_flip_rate"] == pytest.approx(2 / 3)


def test_transport_error_pairs_are_excluded_and_incomplete(bank):
    records = _records("jev")
    records[-1] = _rec("jev", "gamma", "induced", 0, None, error="transport")
    summary = summarize_provider(bank, records, "jev", runs=2, induced_runs=1)
    assert summary["coverage"]["induced_pairs_excluded"] == 1
    assert not summary["coverage"]["complete"]
    decision = decide({"jev": summary, "claude": summarize_provider(bank, _records("claude"), "claude", runs=2, induced_runs=1)})
    assert decision["decision"] == "no-go"
    assert any("資料不足" in r for r in decision["reasons"])


def test_resumed_success_replaces_transport_failure(bank):
    records = _records("jev")
    failed = _rec("jev", "alpha", "clean", 0, None, error="transport")
    records.insert(0, failed)
    summary = summarize_provider(bank, records, "jev", runs=2, induced_runs=1)
    assert summary["overall"]["errors"] == 0 and summary["coverage"]["complete"]


def test_similar_quality_without_efficiency_gain_is_no_go(bank):
    summaries = _summaries(bank, _records("jev", wall=400, usd="0.002") + _records("claude", wall=500, usd="0.002"))
    decision = decide(summaries)
    assert decision["decision"] == "no-go"
    assert decision["relative"]["quality_within_margin"] and not decision["relative"]["route_a"]


def test_missing_candidate_is_no_go(bank):
    assert decide({"claude": summarize_provider(bank, _records("claude"), "claude", runs=2, induced_runs=1)})["decision"] == "no-go"


def test_mean_cost_is_exact_decimal_string(bank):
    summary = summarize_provider(bank, _records("jev", usd="0.1"), "jev", runs=2, induced_runs=1)
    assert summary["cost"]["mean_usd_per_call"] == "0.1"
