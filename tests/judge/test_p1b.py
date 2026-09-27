"""P1b：路由、拆子句判決、題庫組成、凍結、混合系統推導與 go 門檻；Codex judge 解析。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from patchmud.judge import p1b
from patchmud.judge.bank import BankError, load_bank
from patchmud.judge.llm_cli import CodexCliJudge
from patchmud.judge.p1b_scoring import decide_p1b, render_p1b_report, summarize_p1b
from patchmud.judge.runner import load_records, run_tasks
from patchmud.judge.structured import ChoiceAnswer, JudgeCost, StructuredJudgeAdapter, StructuredResponse

REPO_P1B_BANK = Path(__file__).resolve().parents[2] / "experiments" / "jev-p1b" / "bank"


def _item(item_id, gold, ac_type, route, *, evidence=None, clauses=None, variant_of=None, traps=None):
    data = {
        "id": item_id,
        "gold": gold,
        "ac_type": ac_type,
        "route_label": route,
        "clauses": clauses or [f"claim for {item_id}"],
        "traps": traps or [],
        "source": {"kind": "public-fixture", "ref": "synthetic"},
        "rationale": "r",
        "criterion": f"criterion {item_id}",
        "evidence": evidence or [{"id": "e1", "kind": "log", "content": "observed"}],
    }
    if variant_of:
        data["variant_of"] = variant_of
    return data


def _write(root: Path, public: list, hidden: list):
    root.mkdir(parents=True, exist_ok=True)
    (root / "public.yaml").write_text(yaml.safe_dump({"items": public}, allow_unicode=True))
    (root / "hidden.yaml").write_text(yaml.safe_dump({"items": hidden}, allow_unicode=True))
    return load_bank(root)


PREFIX = {"code_behavior": "code", "non_code": "nc"}


def _full_hidden():
    items = []
    for ac_type, route in (("code_behavior", "LLM_REQUIRED"), ("non_code", "JEV_ELIGIBLE")):
        for gold in ("satisfied", "not_satisfied", "insufficient"):
            for n in range(4):
                items.append(_item(f"{PREFIX[ac_type]}-{gold.replace('_', '-')[:4]}-{n}", gold, ac_type, route))
    for ac_type, route in (("code_behavior", "LLM_REQUIRED"), ("non_code", "JEV_ELIGIBLE")):
        for n in range(4):
            base = f"{PREFIX[ac_type]}-sati-{n}"
            items.append(_item(f"v-{base}", "satisfied", ac_type, route, variant_of=base, traps=["looks_fixed"]))
    return items


def test_manifest_flags_follow_kind_and_router_hides_contents(tmp_path):
    evidence = [
        {"id": "e1", "kind": "diff", "content": "SECRET-DIFF"},
        {"id": "e2", "kind": "log", "content": "SECRET-LOG"},
        {"id": "e3", "kind": "reviewer_note", "content": "SECRET-NOTE"},
    ]
    bank = _write(tmp_path, [_item("a", "satisfied", "code_behavior", "LLM_REQUIRED", evidence=evidence)], [])
    item = bank.items[0]
    assert p1b.manifest(item) == [
        {"id": "e1", "kind": "diff", "contains_executable_content": True, "is_direct_observation": False, "is_claim_only": False},
        {"id": "e2", "kind": "log", "contains_executable_content": False, "is_direct_observation": True, "is_claim_only": False},
        {"id": "e3", "kind": "reviewer_note", "contains_executable_content": False, "is_direct_observation": False, "is_claim_only": True},
    ]
    text = json.dumps(p1b.route_request(item).to_payload())
    assert "SECRET" not in text and set(p1b.route_request(item).questions) == set(p1b.ROUTER_QUESTIONS)


def _answers(**choices):
    return StructuredResponse(
        answers={k: ChoiceAnswer(choice=v, confidence=0.9) for k, v in choices.items()},
        provider="jev", model="m", wall_ms=10, response_sha256="x", cost=JudgeCost(usd=None, basis="b"),
    )


@pytest.mark.parametrize(
    "r1,r2,route",
    [("not_required", "yes", "JEV"), ("not_required", "uncertain", "LLM"), ("uncertain", "yes", "LLM"), ("required", "yes", "LLM")],
)
def test_route_is_conservative(r1, r2, route):
    arm = p1b.router_arm(None)
    out = arm.interpret(None, _answers(r1_execution_reasoning=r1, r2_direct_evidence_path=r2))
    assert out["details"]["route"] == route and out["verdict"] is None


def test_decomposed_request_drops_claims_and_short_circuits(tmp_path):
    only_claims = [{"id": "e1", "kind": "pull_request", "content": "fixed!"}, {"id": "e2", "kind": "ticket", "content": "done"}]
    mixed = only_claims + [{"id": "e3", "kind": "record", "content": "status=ok"}]
    bank = _write(tmp_path, [
        _item("claims", "insufficient", "non_code", "JEV_ELIGIBLE", evidence=only_claims),
        _item("mixed", "satisfied", "non_code", "JEV_ELIGIBLE", evidence=mixed, clauses=["c1", "c2"]),
    ], [])
    items = bank.by_id()
    assert p1b.decomposed_request(items["claims"]) is None
    arm = p1b.decomposed_arm(None)
    assert arm.interpret(items["claims"], None)["verdict"] == "insufficient"
    request = p1b.decomposed_request(items["mixed"])
    assert [e["id"] for e in request.state["evidence"]] == ["e3"]
    assert request.state["claims"] == ["c1", "c2"] and set(request.questions) == {"c0", "c1"}


@pytest.mark.parametrize(
    "relations,verdict",
    [(["supports", "supports"], "satisfied"), (["supports", "contradicts"], "not_satisfied"),
     (["supports", "says_nothing"], "insufficient"), ([], "insufficient")],
)
def test_aggregation(relations, verdict):
    assert p1b.aggregate(relations) == verdict


def test_composition_and_variant_runs(tmp_path):
    bank = _write(tmp_path, [_item("dev", "satisfied", "code_behavior", "LLM_REQUIRED")], _full_hidden())
    assert p1b.check_p1b_composition(bank) == []
    tasks = p1b.plan_p1b_tasks(bank, ["codex"], split="hidden")
    assert len(tasks) == 24 + 8 * 2
    assert p1b.count_calls(bank, tasks) == {"codex": 40}
    broken = _write(tmp_path / "b", [], _full_hidden()[:-1])
    assert any("誤導變體" in p for p in p1b.check_p1b_composition(broken))


def test_variant_must_match_base_gold(tmp_path):
    hidden = [_item("base", "satisfied", "non_code", "JEV_ELIGIBLE"), _item("v", "insufficient", "non_code", "JEV_ELIGIBLE", variant_of="base")]
    with pytest.raises(BankError):
        _write(tmp_path, [], hidden)


def test_freeze_and_verify_protocol(tmp_path):
    bank = _write(tmp_path, [], _full_hidden())
    spec = p1b.p1b_spec(codex_model="gpt-6-luna", codex_effort="max", claude_model="sonnet")
    record = p1b.freeze_p1b(bank, spec, {"t": 1}, now=dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc))
    assert record["frozen_at"] == "2026-09-27T00:00:00Z" and record["deadline"] == "2026-10-03"
    assert len(record["hidden_case_ids"]) == 32
    p1b.verify_p1b_frozen(bank, spec, {"t": 1})
    with pytest.raises(BankError):
        p1b.verify_p1b_frozen(bank, spec, {"t": 2})
    other = p1b.p1b_spec(codex_model="gpt-6-luna", codex_effort="high", claude_model="sonnet")
    with pytest.raises(BankError):
        p1b.verify_p1b_frozen(bank, other, {"t": 1})


class Fixed(StructuredJudgeAdapter):
    def __init__(self, provider, answers):
        self.provider, self.model, self.answers = provider, provider, answers

    def judge(self, request):
        return StructuredResponse(
            answers={q: ChoiceAnswer(choice=self.answers[q], confidence=0.8) for q in request.questions},
            provider=self.provider, model=self.model, wall_ms=5, response_sha256="h", cost=JudgeCost(usd=None, basis="b"),
        )


def test_run_tasks_records_code_decided_verdicts(tmp_path):
    bank = _write(tmp_path, [_item("claims", "insufficient", "non_code", "JEV_ELIGIBLE", evidence=[{"id": "e1", "kind": "ticket", "content": "t"}])], [])
    arms = {"jdiag": p1b.decomposed_arm(Fixed("jev", {}))}
    out = tmp_path / "r.jsonl"
    stats = run_tasks(bank, arms, p1b.plan_p1b_tasks(bank, ["jdiag"], split="public"), out)
    record = load_records(out)[0]
    assert stats == {"written": 1, "errors": 0}
    assert record["verdict"] == "insufficient" and record["request_sha256"] is None
    assert record["cost"]["basis"] == "decided_in_code" and record["details"]["decided_in_code"]


# ---- 混合系統推導與門檻 ----

def _records_for(bank, *, route, jdiag, codex, claude, codex_ms=8000, jev_ms=300):
    rows = []
    for item in bank.items:
        if item.split != "hidden":
            continue
        for run in range(p1b.runs_for(item)):
            base = {"item_id": item.id, "variant": "clean", "run_index": run, "split": "hidden", "error": None}
            rows.append({**base, "provider": "router", "verdict": None, "wall_ms": jev_ms, "details": {"route": route(item)}})
            rows.append({**base, "provider": "jdiag", "verdict": jdiag(item), "wall_ms": jev_ms})
            rows.append({**base, "provider": "codex", "verdict": codex(item), "wall_ms": codex_ms})
            rows.append({**base, "provider": "claude", "verdict": claude(item), "wall_ms": 7000})
    return rows


def _perfect_route(item):
    return "JEV" if item.route_label == "JEV_ELIGIBLE" else "LLM"


def _gold(item):
    return item.gold


def test_perfect_hybrid_is_go(tmp_path):
    bank = _write(tmp_path, [], _full_hidden())
    summary = summarize_p1b(bank, _records_for(bank, route=_perfect_route, jdiag=_gold, codex=_gold, claude=_gold))
    assert summary["router"]["unsafe_route"] == 0 and summary["router"]["eligible_routed_rate"] == 1.0
    assert summary["llm_calls"] == {"hybrid": 20, "codex_all": 40}
    assert summary["systems"]["hybrid"]["mean_wall_ms"] < summary["systems"]["codex"]["mean_wall_ms"]
    decision = decide_p1b(summary)
    assert decision["decision"] == "go", decision["reasons"]
    assert "**go**" in render_p1b_report(summary, decision, {"run_id": "t"})


def test_unsafe_route_blocks_go(tmp_path):
    bank = _write(tmp_path, [], _full_hidden())
    records = _records_for(bank, route=lambda i: "JEV", jdiag=_gold, codex=_gold, claude=_gold)
    summary = summarize_p1b(bank, records)
    assert summary["router"]["unsafe_route"] == 20
    assert decide_p1b(summary)["decision"] == "no-go"


def test_slice_false_satisfied_blocks_go(tmp_path):
    bank = _write(tmp_path, [], _full_hidden())
    jdiag = lambda i: "satisfied" if i.ac_type == "non_code" else i.gold
    summary = summarize_p1b(bank, _records_for(bank, route=_perfect_route, jdiag=jdiag, codex=_gold, claude=_gold))
    assert summary["jev_slice"]["false_satisfied"] > 0 and summary["systems"]["hybrid"]["false_satisfied"] > 0
    decision = decide_p1b(summary)
    assert decision["decision"] == "no-go" and not decision["jev_slice"]["false_satisfied"]["pass"]


def test_hybrid_that_routes_everything_to_llm_has_no_value(tmp_path):
    bank = _write(tmp_path, [], _full_hidden())
    summary = summarize_p1b(bank, _records_for(bank, route=lambda i: "LLM", jdiag=_gold, codex=_gold, claude=_gold))
    decision = decide_p1b(summary)
    assert decision["decision"] == "no-go"
    assert not decision["router"]["eligible_routed_rate"]["pass"] and not decision["hybrid"]["resource_value"]["pass"]


def test_router_failure_falls_back_to_llm_and_incomplete_is_no_go(tmp_path):
    bank = _write(tmp_path, [], _full_hidden())
    records = _records_for(bank, route=_perfect_route, jdiag=_gold, codex=_gold, claude=_gold)
    first_router = next(r for r in records if r["provider"] == "router" and r["details"]["route"] == "JEV")
    first_router["error"] = {"kind": "transport"}
    summary = summarize_p1b(bank, records)
    assert summary["router"]["router_failures"] == 1
    assert summary["router"]["routes"][f"{first_router['item_id']}#{first_router['run_index']}"] == "LLM"
    assert decide_p1b(summary)["decision"] == "no-go"


def test_misleading_flip_counts_against_base(tmp_path):
    bank = _write(tmp_path, [], _full_hidden())
    jdiag = lambda i: "insufficient" if i.variant_of else i.gold
    summary = summarize_p1b(bank, _records_for(bank, route=_perfect_route, jdiag=jdiag, codex=_gold, claude=_gold))
    assert summary["jev_slice"]["misleading"] == {"variants": 8, "flips": 8}


# ---- Codex judge ----

def test_codex_argv_and_parse(tmp_path):
    runs = []

    def runner(argv, cwd):
        runs.append(argv)
        return "\n".join([
            json.dumps({"type": "thread.started"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": '{"answers":{"verdict":{"choice":"no","confidence":1}}}'}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 19000, "output_tokens": 120, "reasoning_output_tokens": 97}}),
        ])

    from patchmud.judge.structured import StructuredRequest
    request = StructuredRequest(state={"x": 1}, questions={"verdict": p1b.ChoiceQuestion(instructions="?", criteria={"yes": None, "no": None})})
    response = CodexCliJudge(binary="codex", runner=runner).judge(request)
    argv = runs[0]
    assert argv[:2] == ["codex", "exec"] and argv[argv.index("-m") + 1] == "gpt-6-luna"
    assert "model_reasoning_effort=max" in argv and "--json" in argv
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"][-1] == "shell_tool"
    assert response.answers["verdict"].choice == "no" and response.model == "gpt-6-luna@max"
    assert response.cost.usd is None and response.cost.basis == "codex_subscription_tokens_only"


# ---- 實際 P1b 題庫 ----

def test_repo_p1b_bank_is_frozen_and_conforming():
    bank = load_bank(REPO_P1B_BANK)
    assert p1b.check_p1b_composition(bank) == []
    from patchmud.judge.__main__ import P1B_MODELS
    from patchmud.judge.p1b_scoring import P1B_THRESHOLDS
    spec = p1b.p1b_spec(**P1B_MODELS, revision="r1")
    p1b.verify_p1b_frozen(bank, spec, P1B_THRESHOLDS)


@pytest.mark.parametrize(
    "r0,r1,r2,route",
    [("no", "not_required", "yes", "JEV"), ("yes", "not_required", "yes", "LLM"), ("uncertain", "not_required", "yes", "LLM"),
     ("no", "required", "yes", "LLM")],
)
def test_r2_route_requires_non_software_criterion(r0, r1, r2, route):
    arm = p1b.router_arm(None, "r2")
    out = arm.interpret(None, _answers(r0_software_behavior=r0, r1_execution_reasoning=r1, r2_direct_evidence_path=r2))
    assert out["details"]["route"] == route and out["details"]["r0"] == r0


def test_r2_revision_adds_r0_without_changing_r1_spec(tmp_path):
    bank = _write(tmp_path, [_item("a", "satisfied", "non_code", "JEV_ELIGIBLE")], [])
    item = bank.items[0]
    assert set(p1b.route_request(item, revision="r2").questions) == {"r0_software_behavior", *p1b.ROUTER_QUESTIONS}
    assert set(p1b.router_arm(None, "r2").build(item).questions) == set(p1b.ROUTER_REVISIONS["r2"])
    r1 = p1b.p1b_spec(codex_model="m", codex_effort="max", claude_model="c")
    r2 = p1b.p1b_spec(codex_model="m", codex_effort="max", claude_model="c", revision="r2")
    assert "revision" not in r1 and r2["revision"] == "r2"
    assert r1["router_questions"] != r2["router_questions"]
    tasks = p1b.plan_p1b_tasks(bank, ["router"], split="public")
    assert p1b.count_calls(bank, tasks, "r2") == {"jev_typed_judgments": 3}


def test_repo_p1b_r2_bank_is_frozen_and_conforming():
    from patchmud.judge.__main__ import P1B_MODELS
    from patchmud.judge.p1b_scoring import P1B_THRESHOLDS

    bank = load_bank(REPO_P1B_BANK.parents[1] / "jev-p1b-r2" / "bank")
    assert p1b.check_p1b_composition(bank) == []
    p1b.verify_p1b_frozen(bank, p1b.p1b_spec(**P1B_MODELS, revision="r2"), P1B_THRESHOLDS)
