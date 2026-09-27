"""題庫：schema 驗證、組成門檻、request 不洩漏標註、誘導變體、凍結 digest。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from patchmud.judge import bank as bank_mod
from patchmud.judge.bank import (
    INDUCED_NOTES,
    BankError,
    build_request,
    check_composition,
    freeze,
    load_bank,
    verify_frozen,
)

REPO_BANK = Path(__file__).resolve().parents[2] / "experiments" / "jev-p1" / "bank"


def _item(item_id: str, gold: str, *, traps=None, criterion="The parser rejects empty input."):
    return {
        "id": item_id,
        "gold": gold,
        "traps": traps or [],
        "source": {"kind": "public-fixture", "ref": "synthetic"},
        "rationale": f"GOLD-RATIONALE-{item_id}",
        "criterion": criterion,
        "evidence": [{"id": "e1", "kind": "diff", "content": f"diff for {item_id}"}],
    }


def _write_bank(root: Path, public: list, hidden: list) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "public.yaml").write_text(yaml.safe_dump({"items": public}, allow_unicode=True))
    (root / "hidden.yaml").write_text(yaml.safe_dump({"items": hidden}, allow_unicode=True))
    return root


def _full_bank(root: Path) -> Path:
    public, hidden = [], []
    for verdict in bank_mod.VERDICTS:
        for n in range(12):
            item = _item(f"{verdict.replace('_', '-')}-{n:02d}", verdict, traps=["looks_fixed"] if n < 4 else None)
            (hidden if n >= 8 else public).append(item)
    return _write_bank(root, public, hidden)


def test_load_bank_assigns_split_by_file_and_sorts(tmp_path):
    bank = load_bank(_write_bank(tmp_path, [_item("b-item", "satisfied")], [_item("a-item", "insufficient")]))
    assert [(i.id, i.split) for i in bank.items] == [("a-item", "hidden"), ("b-item", "public")]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(gold="maybe"),
        lambda d: d.update(id="Bad ID"),
        lambda d: d.update(traps=["nonsense"]),
        lambda d: d.update(source={"kind": "private", "ref": "x"}),
        lambda d: d.update(criterion=" "),
        lambda d: d.update(evidence=[]),
        lambda d: d.update(evidence=[{"id": "e1", "kind": "diff"}]),
        lambda d: d.update(unexpected="field"),
    ],
)
def test_invalid_items_are_rejected(tmp_path, mutate):
    item = _item("x-item", "satisfied")
    mutate(item)
    with pytest.raises(BankError):
        load_bank(_write_bank(tmp_path, [item], []))


def test_duplicate_ids_across_splits_are_rejected(tmp_path):
    with pytest.raises(BankError):
        load_bank(_write_bank(tmp_path, [_item("dup", "satisfied")], [_item("dup", "insufficient")]))


def test_composition_thresholds(tmp_path):
    assert check_composition(load_bank(_full_bank(tmp_path / "ok"))) == []
    small = load_bank(_write_bank(tmp_path / "small", [_item("one", "satisfied")], []))
    problems = check_composition(small)
    assert any("hidden" in p for p in problems) and any("誤導" in p for p in problems)


def test_request_never_contains_evaluator_fields(tmp_path):
    bank = load_bank(_full_bank(tmp_path))
    for item in bank.items:
        for variant in bank_mod.VARIANTS:
            text = json.dumps(build_request(item, variant).to_payload(), ensure_ascii=False)
            assert "GOLD-RATIONALE" not in text
            assert '"gold"' not in text and '"traps"' not in text and '"split"' not in text


def test_induced_variant_argues_against_gold(tmp_path):
    bank = load_bank(_full_bank(tmp_path))
    for item in bank.items:
        clean = build_request(item, "clean").state["evidence"]
        induced = build_request(item, "induced").state["evidence"]
        assert induced[:-1] == clean
        expected = INDUCED_NOTES["not_satisfied" if item.gold == "satisfied" else "satisfied"]
        assert induced[-1] == {"id": "e-review", "kind": "reviewer_note", "content": expected}
    with pytest.raises(ValueError):
        build_request(bank.items[0], "weird")


def test_freeze_and_verify_detect_drift(tmp_path):
    root = _full_bank(tmp_path)
    record = freeze(load_bank(root), now=dt.datetime(2026, 9, 27, tzinfo=dt.timezone.utc))
    assert record["frozen_at"] == "2026-09-27T00:00:00Z"
    assert record["counts"]["hidden"] == 12
    assert verify_frozen(load_bank(root))["bank_sha256"] == record["bank_sha256"]
    with pytest.raises(BankError):
        freeze(load_bank(root))
    data = yaml.safe_load((root / "public.yaml").read_text())
    data["items"][0]["criterion"] = "changed after freezing"
    (root / "public.yaml").write_text(yaml.safe_dump(data, allow_unicode=True))
    with pytest.raises(BankError):
        verify_frozen(load_bank(root))


def test_freeze_refuses_non_conforming_bank(tmp_path):
    with pytest.raises(BankError):
        freeze(load_bank(_write_bank(tmp_path, [_item("one", "satisfied")], [])))


def test_judge_spec_digest_covers_prompt_text(monkeypatch):
    before = bank_mod.judge_spec()
    monkeypatch.setattr(bank_mod.llm_cli, "SYSTEM_PROMPT", "changed")
    assert bank_mod.judge_spec() != before


# ---- 實際題庫（experiments/jev-p1/bank）----


def test_repo_bank_meets_composition_and_is_frozen():
    bank = load_bank(REPO_BANK)
    assert check_composition(bank) == []
    verify_frozen(bank)


def test_repo_bank_sources_are_public_or_scrubbed():
    bank = load_bank(REPO_BANK)
    text = "\n".join((REPO_BANK / name).read_text(encoding="utf-8") for name in ("public.yaml", "hidden.yaml"))
    assert "/home/" not in text and "/Users/" not in text
    for item in bank.items:
        assert item.source["kind"] in ("public-fixture", "public-issue", "scrubbed-private")


def test_repo_bank_requests_fit_jev_state_budget():
    # 32k tokens 的 state 上限以保守的 4 bytes／token 換算；遠低於上限才不會被截斷。
    for item in load_bank(REPO_BANK).items:
        size = len(json.dumps(build_request(item, "induced").to_payload(), ensure_ascii=False).encode("utf-8"))
        assert size < 40_000, item.id


def test_repo_bank_plain_scalars_have_no_yaml_comment_truncation():
    # YAML 會把純文字值裡「空格＋#」之後當成註解截掉（例如 "issue #91"）；值含 # 時必須加引號。
    import re

    pattern = re.compile(r"^\s+(- )?(criterion|rationale|content|ref|id|gold):\s+[^'\"|>{\[\s].* #")
    for root in (REPO_BANK, REPO_BANK.parents[1] / "jev-p1b" / "bank"):
        for name in ("public.yaml", "hidden.yaml"):
            for number, line in enumerate((root / name).read_text(encoding="utf-8").splitlines(), 1):
                assert not pattern.match(line), f"{root.parent.name}/{name}:{number}: {line.strip()}"
