"""runner：task 規劃、紀錄內容、續跑、錯誤分類與 config 中止。"""

from __future__ import annotations

import pytest
import yaml

from decimal import Decimal

from patchmud.judge.bank import load_bank
from patchmud.judge.runner import RECORD_SCHEMA, load_records, plan_tasks, run_benchmark
from patchmud.judge.structured import (
    ChoiceAnswer,
    JudgeCost,
    JudgeError,
    StructuredJudgeAdapter,
    StructuredResponse,
)


def _bank(tmp_path):
    def item(item_id, gold):
        return {
            "id": item_id,
            "gold": gold,
            "source": {"kind": "public-fixture", "ref": "synthetic"},
            "rationale": "r",
            "criterion": f"criterion {item_id}",
            "evidence": [{"id": "e1", "kind": "log", "content": "c"}],
        }

    root = tmp_path / "bank"
    root.mkdir()
    (root / "public.yaml").write_text(yaml.safe_dump({"items": [item("alpha", "satisfied"), item("beta", "insufficient")]}))
    (root / "hidden.yaml").write_text(yaml.safe_dump({"items": [item("gamma", "not_satisfied")]}))
    return load_bank(root)


class Scripted(StructuredJudgeAdapter):
    def __init__(self, provider, verdicts=None, errors=None):
        self.provider = provider
        self.model = f"{provider}-model"
        self.verdicts = verdicts or {}
        self.errors = dict(errors or {})
        self.requests = []

    def judge(self, request):
        self.requests.append(request)
        key = request.state["acceptance_criterion"].split()[-1]
        if key in self.errors:
            raise self.errors.pop(key)
        return StructuredResponse(
            answers={"verdict": ChoiceAnswer(choice=self.verdicts.get(key, "satisfied"), confidence=0.6)},
            provider=self.provider,
            model=f"{self.provider}-reported",
            wall_ms=42,
            response_sha256="f" * 64,
            cost=JudgeCost(usd=Decimal("0.001"), basis="test"),
        )


def test_plan_tasks_counts_and_item_filter(tmp_path):
    bank = _bank(tmp_path)
    tasks = plan_tasks(bank, ["jev", "claude"], runs=2, induced_runs=1)
    assert len(tasks) == 2 * 3 * (2 + 1)
    assert {t.variant for t in tasks} == {"clean", "induced"}
    assert len(plan_tasks(bank, ["jev"], runs=1, induced_runs=0, item_ids=["beta"])) == 1
    with pytest.raises(ValueError):
        plan_tasks(bank, ["jev"], item_ids=["nope"])


def test_records_carry_hashes_costs_and_no_gold(tmp_path):
    bank = _bank(tmp_path)
    out = tmp_path / "run" / "records.jsonl"
    stats = run_benchmark(bank, {"jev": Scripted("jev")}, out, runs=1, induced_runs=1, now=lambda: "T")
    records = load_records(out)
    assert stats == {"written": 6, "errors": 0} and len(records) == 6
    record = records[0]
    assert record["schema"] == RECORD_SCHEMA
    assert record["model"] == "jev-reported"
    assert len(record["request_sha256"]) == 64 and record["bank_sha256"] == bank.sha256()
    assert record["cost"] == {"usd": "0.001", "basis": "test", "units": {}}
    assert "gold" not in record and "rationale" not in record
    clean = {r["request_sha256"] for r in records if r["variant"] == "clean"}
    induced = {r["request_sha256"] for r in records if r["variant"] == "induced"}
    assert clean.isdisjoint(induced)


def test_same_request_hash_across_providers(tmp_path):
    bank = _bank(tmp_path)
    out = tmp_path / "records.jsonl"
    run_benchmark(bank, {"jev": Scripted("jev"), "claude": Scripted("claude")}, out, runs=1, induced_runs=0)
    by_item = {}
    for r in load_records(out):
        by_item.setdefault(r["item_id"], set()).add(r["request_sha256"])
    assert all(len(hashes) == 1 for hashes in by_item.values())


def test_resume_skips_final_records_and_retries_transport_errors(tmp_path):
    bank = _bank(tmp_path)
    out = tmp_path / "records.jsonl"
    flaky = Scripted(
        "jev",
        errors={
            "alpha": JudgeError("transport", "boom", retryable=True),
            "beta": JudgeError("invalid_output", "garbage"),
        },
    )
    first = run_benchmark(bank, {"jev": flaky}, out, runs=1, induced_runs=0)
    assert first == {"written": 3, "errors": 2}
    second = run_benchmark(bank, {"jev": flaky}, out, runs=1, induced_runs=0)
    assert second == {"written": 1, "errors": 0}
    records = load_records(out)
    assert [r["item_id"] for r in records[-1:]] == ["alpha"]
    errors = [r["error"]["kind"] for r in records if r["error"]]
    assert sorted(errors) == ["invalid_output", "transport"]


def test_config_error_aborts_run(tmp_path):
    bank = _bank(tmp_path)
    broken = Scripted("jev", errors={"alpha": JudgeError("config", "missing key")})
    with pytest.raises(JudgeError):
        run_benchmark(bank, {"jev": broken}, tmp_path / "records.jsonl", runs=1, induced_runs=0)
