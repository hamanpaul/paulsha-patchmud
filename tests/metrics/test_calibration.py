"""Task 19：校準 estimators（spec §10.4、F4）——pre-registration 與凍結語意。

RED 鎖定（plan Task 19 Step 1）：

- `CalibrationRun` 契約 fail-closed：clear 非 0/1、cost 非 Decimal／為 0
  （F18）、work_tokens 為 0（NA 不記 0，§10.1）、負 LOC／負 F 一律拒絕。
- `reference_cost_i`（C_ref）＝ encounter i 上全部 successful clear run 的
  `C_run` **中位數**（Decimal，禁 float）；成功數 < 3 → 該 encounter 標記
  不產 Economy（reference_cost None、列入 uncalibrated）。
- `difficulty_scale_i`（D）＝ `clamp(median(成功 run 的 final diff LOC)/40,
  0.5, 4.0)`；成功數 < 3 → 維持 1.0 並標註未校準。
- `τ` ＝ 全體 run（含失敗 run）中 F > 0 者的 F 中位數；無 F>0 樣本 →
  τ=1.0＋標記；一律發布 τ×{0.5, 1, 2} 敏感度。
- `EuTB B` ＝ successful clear 的 T^work 之 P95（nearest-rank，整數域計算）
  向上取整至 10k tokens；積分網格 = 256 點；無任何 successful clear →
  fail-closed（無法註冊預算）。
- 凍結語意：`freeze_calibration` 輸出 `calibration.yaml` 與
  `eutb_budget.yaml`（皆含 canonical-JSON sha256）並把 C_ref／D 寫回 deck
  card.yaml；registered 檔已存在或 card 已含 reference_cost → 拒絕覆寫
  （`FrozenCalibrationError`），且全有全無——不得留下半套輸出。
- `eutb_budget.yaml` 必須能被 metrics 層 `load_eutb_budget` 讀回（report
  EuTB 榜同一契約，§19.9）。
- CLI `patchmud calibrate --runs <glob> --out <dir> --pricing <snapshot>`：
  真封存 run 目錄 → estimator → 凍結輸出；`--out` 目錄缺 pre-registered
  `estimators.yaml` → 拒絕產出（F4）；重複 calibrate → exit 2。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from patchmud.cli import main
from patchmud.deck.loader import load_card
from patchmud.ledger.cost import compute_run_cost
from patchmud.ledger.pricing import PricingSnapshot
from patchmud.ledger.tokens import LedgerEntry
from patchmud.metrics.calibration import (
    EUTB_GRID_POINTS,
    CalibrationError,
    CalibrationRun,
    FrozenCalibrationError,
    calibrate,
    freeze_calibration,
)
from patchmud.metrics.efficiency import load_eutb_budget
from patchmud.store.run_store import RunStore

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "mini_encounter"
PRICING_PATH = REPO_ROOT / "pricing" / "example" / "2026-07-16.yaml"

ENC = "mini-inventory-v1"
ENC2 = "mini-inventory-v2"


def make_run(
    run_id: str,
    *,
    encounter: str = ENC,
    clear: int = 1,
    cost: str = "1.00",
    loc: int = 40,
    flood: float = 0.0,
    tokens: int = 10_000,
) -> CalibrationRun:
    return CalibrationRun(
        run_id=run_id,
        encounter=encounter,
        clear=clear,
        cost=Decimal(cost),
        final_diff_loc=loc,
        flood_index=flood,
        work_tokens=tokens,
    )


def successes(n: int, **overrides) -> list[CalibrationRun]:
    return [make_run(f"r{i}", **overrides) for i in range(n)]


# ---------------------------------------------------------------------------
# CalibrationRun 契約（fail-closed）
# ---------------------------------------------------------------------------


class TestCalibrationRunContract:
    def test_clear_must_be_binary(self) -> None:
        with pytest.raises(CalibrationError):
            make_run("r1", clear=2)

    def test_cost_must_be_decimal(self) -> None:
        with pytest.raises(CalibrationError):
            CalibrationRun(
                run_id="r1",
                encounter=ENC,
                clear=1,
                cost=1.0,  # float 金額禁止（invariant 6）
                final_diff_loc=40,
                flood_index=0.0,
                work_tokens=10_000,
            )

    def test_zero_cost_rejected(self) -> None:
        # C_run = 0 是計價設定錯誤（F18 除零防線）
        with pytest.raises(CalibrationError):
            make_run("r1", cost="0")

    def test_zero_work_tokens_rejected(self) -> None:
        # NA 必須記 None／缺席，不得記 0（§10.1）；0 token run 直接拒絕
        with pytest.raises(CalibrationError):
            make_run("r1", tokens=0)

    def test_negative_loc_rejected(self) -> None:
        with pytest.raises(CalibrationError):
            make_run("r1", loc=-1)

    def test_negative_flood_index_rejected(self) -> None:
        with pytest.raises(CalibrationError):
            make_run("r1", flood=-0.5)

    def test_empty_runs_rejected(self) -> None:
        with pytest.raises(CalibrationError):
            calibrate([])

    def test_duplicate_run_id_rejected(self) -> None:
        runs = successes(3) + [make_run("r0")]
        with pytest.raises(CalibrationError):
            calibrate(runs)


# ---------------------------------------------------------------------------
# C_ref：成功 run 成本中位數（§10.4 表列 estimator）
# ---------------------------------------------------------------------------


class TestReferenceCost:
    def test_median_of_successful_costs_odd(self) -> None:
        runs = [
            make_run("r1", cost="1.00"),
            make_run("r2", cost="3.00"),
            make_run("r3", cost="2.00"),
        ]
        result = calibrate(runs)
        assert result.reference_costs[ENC] == Decimal("2.00")
        assert isinstance(result.reference_costs[ENC], Decimal)

    def test_median_of_successful_costs_even(self) -> None:
        runs = [
            make_run("r1", cost="1.00"),
            make_run("r2", cost="2.00"),
            make_run("r3", cost="3.00"),
            make_run("r4", cost="10.00"),
        ]
        result = calibrate(runs)
        assert result.reference_costs[ENC] == Decimal("2.5")
        assert isinstance(result.reference_costs[ENC], Decimal)

    def test_failed_runs_excluded_from_reference_cost(self) -> None:
        runs = successes(3, cost="1.00") + [
            make_run("fail", clear=0, cost="100.00")
        ]
        result = calibrate(runs)
        assert result.reference_costs[ENC] == Decimal("1.00")

    def test_fewer_than_three_successes_marks_no_economy(self) -> None:
        # 成功數 < 3 → 該 encounter 不產 Economy 分數（只報原始成本）
        runs = successes(2) + [make_run("fail", clear=0)]
        result = calibrate(runs)
        assert result.reference_costs[ENC] is None
        assert ENC in result.uncalibrated


# ---------------------------------------------------------------------------
# D：clamp(median(成功 run 的 final diff LOC) / 40, 0.5, 4.0)
# ---------------------------------------------------------------------------


class TestDifficultyScale:
    def test_formula_median_over_40(self) -> None:
        runs = [
            make_run("r1", loc=30),
            make_run("r2", loc=80),
            make_run("r3", loc=130),
        ]
        result = calibrate(runs)
        assert result.difficulty_scales[ENC] == pytest.approx(2.0)

    def test_clamp_lower_bound(self) -> None:
        result = calibrate(
            [make_run(f"r{i}", loc=2) for i in range(3)]
        )
        assert result.difficulty_scales[ENC] == 0.5

    def test_clamp_upper_bound(self) -> None:
        result = calibrate(
            [make_run(f"r{i}", loc=400) for i in range(3)]
        )
        assert result.difficulty_scales[ENC] == 4.0

    def test_failed_runs_excluded_from_loc_median(self) -> None:
        runs = successes(3, loc=80) + [make_run("fail", clear=0, loc=4000)]
        result = calibrate(runs)
        assert result.difficulty_scales[ENC] == pytest.approx(2.0)

    def test_fewer_than_three_successes_keeps_one(self) -> None:
        # 成功數 < 3 → 維持 1.0 並標註未校準
        runs = successes(2, loc=400) + [make_run("fail", clear=0)]
        result = calibrate(runs)
        assert result.difficulty_scales[ENC] == 1.0
        assert ENC in result.uncalibrated


# ---------------------------------------------------------------------------
# τ：全體 run 中 F > 0 者的 F 中位數
# ---------------------------------------------------------------------------


class TestTau:
    def test_median_of_positive_flood_indices(self) -> None:
        runs = [
            make_run("r1", flood=0.0),
            make_run("r2", flood=2.0),
            make_run("r3", flood=6.0),
            make_run("fail", clear=0, flood=4.0),  # 失敗 run 也進 τ 樣本
        ]
        result = calibrate(runs)
        assert result.tau == pytest.approx(4.0)
        assert result.tau_uncalibrated is False
        assert result.tau_sensitivity == pytest.approx((2.0, 4.0, 8.0))

    def test_no_positive_samples_falls_back_to_one(self) -> None:
        # 無 F>0 樣本 → τ = 1.0（標註未校準）
        result = calibrate(successes(3, flood=0.0))
        assert result.tau == 1.0
        assert result.tau_uncalibrated is True
        assert result.tau_sensitivity == pytest.approx((0.5, 1.0, 2.0))


# ---------------------------------------------------------------------------
# EuTB B：successful clear 的 T^work P95 向上取整 10k；網格 256 點
# ---------------------------------------------------------------------------


class TestEutbBudget:
    def test_p95_rounded_up_to_10k(self) -> None:
        # 20 筆成功 tokens = 1000..20000：nearest-rank P95 = 第 19 筆 =
        # 19000 → 向上取整 10k → 20000
        runs = [
            make_run(f"r{k}", tokens=1000 * k) for k in range(1, 21)
        ]
        result = calibrate(runs)
        assert result.eutb_budget == 20_000

    def test_exact_multiple_stays(self) -> None:
        result = calibrate(successes(3, tokens=40_000))
        assert result.eutb_budget == 40_000

    def test_non_multiple_rounds_up(self) -> None:
        result = calibrate(successes(3, tokens=40_001))
        assert result.eutb_budget == 50_000

    def test_failed_run_tokens_excluded(self) -> None:
        runs = successes(3, tokens=5_000) + [
            make_run("fail", clear=0, tokens=1_000_000)
        ]
        result = calibrate(runs)
        assert result.eutb_budget == 10_000

    def test_grid_is_256_points(self) -> None:
        result = calibrate(successes(3))
        assert result.grid == 256
        assert EUTB_GRID_POINTS == 256

    def test_no_successful_clear_fail_closed(self) -> None:
        # 無 successful clear → EuTB 預算無法註冊（fail-closed）
        with pytest.raises(CalibrationError):
            calibrate([make_run("r1", clear=0), make_run("r2", clear=0)])


# ---------------------------------------------------------------------------
# 凍結語意：輸出寫入 deck 與 registered dir（含 hash），已存在拒絕覆寫
# ---------------------------------------------------------------------------


def make_deck(tmp_path: Path) -> dict[str, Path]:
    """兩個 encounter 的 tmp deck（fixture 複本；ENC2 只改 issue_id）。"""
    deck: dict[str, Path] = {}
    for issue_id in (ENC, ENC2):
        dest = tmp_path / "deck" / issue_id
        shutil.copytree(FIXTURE, dest)
        if issue_id != ENC:
            card_path = dest / "card.yaml"
            data = yaml.safe_load(card_path.read_text(encoding="utf-8"))
            data["issue_id"] = issue_id
            card_path.write_text(
                yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
                encoding="utf-8",
            )
        deck[issue_id] = dest
    return deck


def two_encounter_result():
    """ENC 校準成功（3 成功）、ENC2 未達門檻（1 成功）。"""
    runs = [
        make_run("a1", cost="1.00", loc=30, tokens=8_000),
        make_run("a2", cost="2.00", loc=80, tokens=12_000),
        make_run("a3", cost="3.00", loc=130, tokens=15_000),
        make_run("b1", encounter=ENC2, cost="5.00"),
        make_run("b2", encounter=ENC2, clear=0, cost="5.00"),
    ]
    return calibrate(runs)


def canonical_sha256(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TestFreezeSemantics:
    def test_freeze_writes_registered_files_with_hash(self, tmp_path) -> None:
        deck = make_deck(tmp_path)
        registered = tmp_path / "registered"
        result = two_encounter_result()

        calibration_path, budget_path = freeze_calibration(
            result, registered, deck
        )
        assert calibration_path == registered / "calibration.yaml"
        assert budget_path == registered / "eutb_budget.yaml"

        data = yaml.safe_load(calibration_path.read_text(encoding="utf-8"))
        stored = data.pop("sha256")
        assert stored == canonical_sha256(data)
        assert data["reference_costs"][ENC] == "2.00"
        assert data["reference_costs"][ENC2] == "NA"
        assert data["difficulty_scales"][ENC] == pytest.approx(2.0)
        assert data["difficulty_scales"][ENC2] == 1.0
        assert data["uncalibrated"] == [ENC2]
        assert data["tau_uncalibrated"] is True

        budget_data = yaml.safe_load(budget_path.read_text(encoding="utf-8"))
        stored_budget = budget_data.pop("sha256")
        assert stored_budget == canonical_sha256(budget_data)

    def test_budget_file_round_trips_via_metrics_loader(self, tmp_path) -> None:
        deck = make_deck(tmp_path)
        registered = tmp_path / "registered"
        result = two_encounter_result()
        _, budget_path = freeze_calibration(result, registered, deck)
        budget = load_eutb_budget(budget_path)
        # P95(nearest-rank) of {8000, 12000, 15000} = 15000 → 20000
        assert budget.budget_tokens == 20_000
        assert budget.grid_points == 256

    def test_freeze_updates_calibrated_cards_only(self, tmp_path) -> None:
        deck = make_deck(tmp_path)
        registered = tmp_path / "registered"
        freeze_calibration(two_encounter_result(), registered, deck)

        calibrated = load_card(deck[ENC] / "card.yaml")
        assert calibrated.reference_cost == Decimal("2.00")
        assert calibrated.difficulty_scale == pytest.approx(2.0)

        untouched = load_card(deck[ENC2] / "card.yaml")
        assert untouched.reference_cost is None
        assert untouched.difficulty_scale == 1.0

    def test_second_freeze_refused(self, tmp_path) -> None:
        deck = make_deck(tmp_path)
        registered = tmp_path / "registered"
        result = two_encounter_result()
        freeze_calibration(result, registered, deck)
        before = (registered / "calibration.yaml").read_bytes()
        with pytest.raises(FrozenCalibrationError):
            freeze_calibration(result, registered, deck)
        assert (registered / "calibration.yaml").read_bytes() == before

    def test_precalibrated_card_refused_all_or_nothing(self, tmp_path) -> None:
        deck = make_deck(tmp_path)
        card_path = deck[ENC] / "card.yaml"
        data = yaml.safe_load(card_path.read_text(encoding="utf-8"))
        data["reference_cost"] = "9.99"  # deck 已凍結過
        card_path.write_text(
            yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        registered = tmp_path / "registered"
        with pytest.raises(FrozenCalibrationError):
            freeze_calibration(two_encounter_result(), registered, deck)
        # 全有全無：registered 檔不得留下半套輸出
        assert not (registered / "calibration.yaml").exists()
        assert not (registered / "eutb_budget.yaml").exists()

    def test_missing_encounter_dir_rejected(self, tmp_path) -> None:
        registered = tmp_path / "registered"
        with pytest.raises(CalibrationError):
            freeze_calibration(two_encounter_result(), registered, {})


# ---------------------------------------------------------------------------
# CLI：patchmud calibrate --runs <glob> --out <dir> --pricing <snapshot>
# ---------------------------------------------------------------------------

PRICING = PricingSnapshot.load(PRICING_PATH)

_COUNTERS = {
    "regression": 0,
    "reopen": 0,
    "duplicate": 0,
    "s_scope_loc": 0,
    "reverted_loc": 0,
}


def ledger_line(*, billed_in: int, billed_out: int) -> dict:
    return {
        "schema_version": 1,
        "turn": 1,
        "role": "author",
        "input_uncached": billed_in,
        "input_cached": 0,
        "output_visible": billed_out,
        "reasoning": 0,
        "billed_input_total": billed_in,
        "billed_output_total": billed_out,
        "unallocated": 0,
        "api_calls": 1,
        "tool_calls": 0,
        "wall_clock_ms": 100,
        "prompt_bytes": 0,
        "generated_bytes": 0,
        "pricing_snapshot_ref": PRICING.content_hash,
    }


def expected_cost(line: dict) -> Decimal:
    entry = LedgerEntry(
        **{k: v for k, v in line.items() if k != "schema_version"}
    )
    return compute_run_cost([entry], PRICING).total


def make_run_dir(
    runs_root: Path,
    encounter_dir: Path,
    run_id: str,
    *,
    clear: int = 1,
    work_tokens: int = 10_000,
    production_loc: int = 40,
    billed_in: int = 100_000,
    billed_out: int = 50_000,
) -> dict:
    """合成封存 run 目錄（store 契約真落盤；不執行任何 candidate code）。"""
    store = RunStore.create(
        {
            "run_id": run_id,
            "frozen_sha": "f" * 40,
            "pricing_hash": PRICING.content_hash,
            "harness_prompt_version": "hp-test",
            "schedule_ref": "NA",
            "encounter_dir": str(encounter_dir),
            "model": "scripted:test",
            "loadout": "P0T0R0",
        },
        runs_root,
    )
    store.append_event(
        {"type": "baseline", "queue": {"b_t": 1, "m_t": 1, "counters": _COUNTERS}}
    )
    store.write_result(
        {
            "mode": "run",
            "human": False,
            "clear": clear,
            "loadout": "P0T0R0",
            "power": {"maintainability_breakdown": {"production_loc": production_loc}},
            "ledger": {"work_tokens": work_tokens},
        }
    )
    line = ledger_line(billed_in=billed_in, billed_out=billed_out)
    (store.run_dir / "ledger.jsonl").write_text(
        json.dumps(line, sort_keys=True) + "\n", encoding="utf-8"
    )
    return line


def make_cli_fixture(tmp_path: Path) -> tuple[Path, Path, Path, list[Decimal]]:
    """tmp deck（fixture 複本）＋ 3 成功 1 失敗的封存 runs ＋ registered dir。"""
    encounter_dir = tmp_path / "deck" / ENC
    shutil.copytree(FIXTURE, encounter_dir)
    runs_root = tmp_path / "runs"

    costs: list[Decimal] = []
    for i, (tokens, loc) in enumerate(
        ((8_000, 30), (12_000, 80), (15_000, 130)), start=1
    ):
        line = make_run_dir(
            runs_root,
            encounter_dir,
            f"pilot-ok-{i}",
            work_tokens=tokens,
            production_loc=loc,
            billed_in=100_000 * i,
            billed_out=50_000 * i,
        )
        costs.append(expected_cost(line))
    make_run_dir(
        runs_root, encounter_dir, "pilot-fail", clear=0, work_tokens=5_000
    )

    registered = tmp_path / "registered"
    registered.mkdir()
    (registered / "estimators.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    return encounter_dir, runs_root, registered, costs


def run_calibrate(runs_root: Path, registered: Path) -> int:
    return main(
        [
            "calibrate",
            "--runs",
            str(runs_root / "*"),
            "--out",
            str(registered),
            "--pricing",
            str(PRICING_PATH),
        ]
    )


class TestCalibrateCli:
    def test_calibrate_cli_end_to_end(self, tmp_path) -> None:
        encounter_dir, runs_root, registered, costs = make_cli_fixture(tmp_path)
        rc = run_calibrate(runs_root, registered)
        assert rc == 0

        data = yaml.safe_load(
            (registered / "calibration.yaml").read_text(encoding="utf-8")
        )
        median_cost = sorted(costs)[1]  # 3 筆成功成本的中位數
        assert data["reference_costs"][ENC] == str(median_cost)
        assert data["difficulty_scales"][ENC] == pytest.approx(2.0)
        assert data["uncalibrated"] == []
        # 合成 events 無 flooding → 全體 F=0 → τ 未校準
        assert data["tau"] == 1.0
        assert data["tau_uncalibrated"] is True

        budget = load_eutb_budget(registered / "eutb_budget.yaml")
        assert budget.budget_tokens == 20_000  # P95{8k,12k,15k} → 20k
        assert budget.grid_points == 256

        card = load_card(encounter_dir / "card.yaml")
        assert card.reference_cost == median_cost
        assert card.difficulty_scale == pytest.approx(2.0)

    def test_calibrate_cli_refuses_second_run(self, tmp_path) -> None:
        _, runs_root, registered, _ = make_cli_fixture(tmp_path)
        assert run_calibrate(runs_root, registered) == 0
        before = (registered / "calibration.yaml").read_bytes()
        assert run_calibrate(runs_root, registered) == 2  # 凍結拒絕覆寫
        assert (registered / "calibration.yaml").read_bytes() == before

    def test_calibrate_cli_requires_registered_estimators(self, tmp_path) -> None:
        _, runs_root, registered, _ = make_cli_fixture(tmp_path)
        (registered / "estimators.yaml").unlink()
        # F4：estimator 未 pre-register → 拒絕產出校準值
        assert run_calibrate(runs_root, registered) == 2
        assert not (registered / "calibration.yaml").exists()
