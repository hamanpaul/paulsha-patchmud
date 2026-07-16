"""Task 7 RED：pricing snapshot 與 run 成本（spec §10.2、報告 §5.2、F16/F18）。

鎖定：
- ``PricingSnapshot.load`` 含 ``content_hash``；schema / 價格型別 fail-closed。
- 成本全程 ``decimal.Decimal``：9 次呼叫、``per_request=0.01`` → 總價差恰
  ``Decimal("0.09")``（F16）。
- 同 entries 換 snapshot（日期、價格不同）→ 成本改變、entries 位元不變。
- ``C_run = 0`` 為設定錯誤 → fail-closed（F18 除零防線）。
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from patchmud.ledger.cost import RunCost, compute_run_cost
from patchmud.ledger.pricing import PricingSnapshot
from patchmud.ledger.tokens import LedgerEntry, LedgerError

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SNAPSHOT = REPO_ROOT / "pricing" / "example" / "2026-07-16.yaml"


def _snapshot_yaml(
    *,
    snapshot_date: str = "2026-07-16",
    input_uncached: str = "3.00",
    input_cached: str = "0.30",
    output: str = "15.00",
    per_request: str = "0.01",
    per_tool_call: str = "0.00",
    minimum_charge_per_call: str = "0.00",
    schema_version: int = 1,
) -> str:
    return (
        f"schema_version: {schema_version}\n"
        f'snapshot_date: "{snapshot_date}"\n'
        'currency: "USD"\n'
        "prices:\n"
        f'  input_uncached_per_mtok: "{input_uncached}"\n'
        f'  input_cached_per_mtok: "{input_cached}"\n'
        f'  output_per_mtok: "{output}"\n'
        f'  per_request: "{per_request}"\n'
        f'  per_tool_call: "{per_tool_call}"\n'
        f'  minimum_charge_per_call: "{minimum_charge_per_call}"\n'
    )


def _load(tmp_path: Path, text: str, name: str = "snap.yaml") -> PricingSnapshot:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return PricingSnapshot.load(path)


def _entry(**overrides) -> LedgerEntry:
    kwargs = dict(
        turn=1,
        role="author",
        input_uncached=1000,
        input_cached=300,
        output_visible=500,
        reasoning=None,
        billed_input_total=1300,
        billed_output_total=500,
        unallocated=0,
        pricing_snapshot_ref="sha256:example",
    )
    kwargs.update(overrides)
    return LedgerEntry(**kwargs)


class TestPricingSnapshotLoad:
    def test_example_snapshot_loads_with_content_hash(self):
        snapshot = PricingSnapshot.load(EXAMPLE_SNAPSHOT)
        raw = EXAMPLE_SNAPSHOT.read_bytes()
        assert snapshot.content_hash == hashlib.sha256(raw).hexdigest()
        assert snapshot.snapshot_date == "2026-07-16"
        assert isinstance(snapshot.per_request, Decimal)
        assert isinstance(snapshot.output_per_mtok, Decimal)

    def test_unknown_schema_version_fails_closed(self, tmp_path):
        with pytest.raises(LedgerError):
            _load(tmp_path, _snapshot_yaml(schema_version=99))

    def test_missing_price_field_fails_closed(self, tmp_path):
        text = _snapshot_yaml().replace(
            '  output_per_mtok: "15.00"\n', ""
        )
        with pytest.raises(LedgerError):
            _load(tmp_path, text)

    def test_float_price_fails_closed(self, tmp_path):
        # 價格必須是字串字面值 → Decimal 精確解析；YAML float 拒收。
        text = _snapshot_yaml().replace('"15.00"', "15.00")
        with pytest.raises(LedgerError):
            _load(tmp_path, text)


class TestComputeRunCost:
    def test_per_request_charges_exact_decimal_difference(self, tmp_path):
        """F16：9 次呼叫、per_request=0.01 → 總價差恰 Decimal("0.09")。"""
        with_fee = _load(tmp_path, _snapshot_yaml(per_request="0.01"), "a.yaml")
        no_fee = _load(tmp_path, _snapshot_yaml(per_request="0.00"), "b.yaml")
        entries = [_entry(turn=i) for i in range(1, 10)]
        assert len(entries) == 9
        cost_with = compute_run_cost(entries, with_fee)
        cost_without = compute_run_cost(entries, no_fee)
        diff = cost_with.total - cost_without.total
        assert diff == Decimal("0.09")
        assert isinstance(cost_with.c_model, Decimal)
        assert isinstance(cost_with.c_reviewer, Decimal)

    def test_token_costs_use_billed_totals_with_cached_split(self, tmp_path):
        snapshot = _load(tmp_path, _snapshot_yaml())
        entries = [_entry()]
        cost = compute_run_cost(entries, snapshot)
        mtok = Decimal(10**6)
        expected = (
            Decimal(300) * Decimal("0.30") / mtok  # cached input
            + Decimal(1000) * Decimal("3.00") / mtok  # 其餘 billed input
            + Decimal(500) * Decimal("15.00") / mtok  # billed output（含 reasoning）
            + Decimal("0.01")  # per_request × 1
        )
        assert cost.c_model == expected
        assert cost.c_reviewer == Decimal("0")

    def test_na_cached_bills_all_input_at_uncached_rate(self, tmp_path):
        snapshot = _load(tmp_path, _snapshot_yaml())
        entries = [_entry(input_cached=None, unallocated=300)]
        cost = compute_run_cost(entries, snapshot)
        mtok = Decimal(10**6)
        expected = (
            Decimal(1300) * Decimal("3.00") / mtok
            + Decimal(500) * Decimal("15.00") / mtok
            + Decimal("0.01")
        )
        assert cost.c_model == expected

    def test_role_split_author_vs_reviewer(self, tmp_path):
        snapshot = _load(tmp_path, _snapshot_yaml())
        entries = [_entry(), _entry(role="reviewer", turn=2)]
        cost = compute_run_cost(entries, snapshot)
        assert cost.c_model > Decimal("0")
        assert cost.c_reviewer > Decimal("0")
        assert cost.c_model == cost.c_reviewer
        assert cost.total == cost.c_model + cost.c_reviewer

    def test_swapping_snapshot_changes_price_not_entries(self, tmp_path):
        snap_a = _load(tmp_path, _snapshot_yaml(), "2026-07-16.yaml")
        snap_b = _load(
            tmp_path,
            _snapshot_yaml(
                snapshot_date="2026-08-01",
                input_uncached="6.00",
                output="30.00",
            ),
            "2026-08-01.yaml",
        )
        assert snap_a.content_hash != snap_b.content_hash
        entries = tuple(_entry(turn=i) for i in range(1, 4))
        before = tuple(entries)
        cost_a = compute_run_cost(list(entries), snap_a)
        cost_b = compute_run_cost(list(entries), snap_b)
        assert cost_a.total != cost_b.total
        # entries 不因換 snapshot 而變：封存 run 不受價改影響（§10.2）。
        assert tuple(entries) == before
        assert all(e.pricing_snapshot_ref == "sha256:example" for e in entries)

    def test_minimum_charge_per_call_floors_each_call(self, tmp_path):
        snapshot = _load(
            tmp_path,
            _snapshot_yaml(per_request="0.00", minimum_charge_per_call="0.05"),
        )
        # token 成本遠低於 0.05 → 每次呼叫以最低消費計。
        entries = [
            _entry(
                input_uncached=1,
                input_cached=0,
                output_visible=1,
                billed_input_total=1,
                billed_output_total=1,
            )
        ]
        cost = compute_run_cost(entries, snapshot)
        assert cost.c_model == Decimal("0.05")

    def test_zero_total_run_cost_fails_closed(self, tmp_path):
        """F18：C_run = 0 是設定錯誤，拒絕輸出。"""
        snapshot = _load(
            tmp_path,
            _snapshot_yaml(
                input_uncached="0",
                input_cached="0",
                output="0",
                per_request="0.00",
            ),
        )
        with pytest.raises(LedgerError):
            compute_run_cost([_entry()], snapshot)

    def test_empty_entries_yield_zero_cost_without_error(self, tmp_path):
        # 離線 score-diff（無 adapter 呼叫）合法：空 ledger → 0 成本，不觸 F18。
        snapshot = _load(tmp_path, _snapshot_yaml())
        cost = compute_run_cost([], snapshot)
        assert cost == RunCost(c_model=Decimal("0"), c_reviewer=Decimal("0"))
