"""Task 14 RED：Flood 計量（spec §8.2、報告 §7.3–7.4；plan Task 14 Step 1）。

鎖定（F13／F14 情境直接入測）：
- dual area：`FloodArea_total = Σ_t B_t`（Δt = 1 turn）與
  `FloodArea_excess = Σ_t (B_t − M_t)` 只計自生債務存續面積（F13）。
- FTR token 歸屬以 **turn 開始時** backlog 狀態判定（F14）：
  `FTR = Σ_t ΔT_t·1(B_{t−1} > B_0) / Σ_t ΔT_t`；並平行發布
  `flood_create_tokens`／`flood_repair_tokens`。
- Control 使用 excess：完美 PLAN/TDD run 不因初始 MAIN 存續被扣分（F13）；
  `Control = 100·exp(−F/τ)`，τ 未校準時使用 1.0 並標記 uncalibrated。
- 係數檔版本化、review-debt 權重固定 0（§6.2）、schema 不符 fail-closed。

metrics 只讀 store 落盤 events（queue snapshots + per-turn ledger），
不執行任何 candidate code（plan invariant 3、4）。
"""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from patchmud.metrics.flood import (
    FloodError,
    FloodMetrics,
    flood_metrics,
    load_flood_coeffs,
)
from tests.evaluator.helpers import make_card

CARD = make_card()  # difficulty_scale=1.0
COEFFS = load_flood_coeffs()

#: `IssueQueue.snapshot()` counters 的零值形狀（真 snapshot 同構）。
ZERO_COUNTERS = {
    "reopen": 0,
    "regression": 0,
    "duplicate": 0,
    "churn_events": 0,
    "failed_claims": 0,
    "scope_hard_open": 0,
    "s_scope_loc": 0,
    "reverted_loc": 0,
}


def _snapshot(b_t: int, m_t: int, counters: dict | None = None) -> dict:
    return {
        "b_t": b_t,
        "m_t": m_t,
        "open_items": [],
        "counters": {**ZERO_COUNTERS, **(counters or {})},
    }


def _ledger(author_tokens: int, reviewer_tokens: int | None = None) -> dict:
    entry = {
        "billed_input_total": author_tokens,
        "billed_output_total": 0,
    }
    reviewer = None
    if reviewer_tokens is not None:
        reviewer = {
            "billed_input_total": reviewer_tokens,
            "billed_output_total": 0,
        }
    return {"author": entry, "reviewer": reviewer}


def events(
    B: list[int] | None = None,
    M: list[int] | None = None,
    B0: int | None = None,
    turns: list[tuple[int, int]] | None = None,
    counters: dict | None = None,
) -> list[dict]:
    """組合 fake store events（baseline + turn events，schema 同 loop 落盤）。

    兩種模式：
    - ``B=[...], M=[...]``：每 turn 固定 ΔT=100 token。
    - ``B0=…, turns=[(ΔT, B_t), …]``：FTR 情境；m_t 一律 0（不影響 FTR）。

    ``counters`` 疊加在最後一個 turn event 的 queue counters（累計語意）。
    """
    out: list[dict] = []
    if turns is not None:
        assert B0 is not None
        out.append({"type": "baseline", "queue": _snapshot(B0, B0)})
        rows = [(dt, b_t, 0) for dt, b_t in turns]
    else:
        assert B is not None
        m = M if M is not None else list(B)
        b0 = B0 if B0 is not None else B[0]
        out.append({"type": "baseline", "queue": _snapshot(b0, b0)})
        rows = [(100, b_t, m_t) for b_t, m_t in zip(B, m, strict=True)]
    for turn, (dt, b_t, m_t) in enumerate(rows, start=1):
        is_last = turn == len(rows)
        out.append(
            {
                "type": "turn",
                "turn": turn,
                "queue": _snapshot(b_t, m_t, counters if is_last else None),
                "ledger": _ledger(dt),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Dual area（F13）
# ---------------------------------------------------------------------------


class TestDualArea:
    def test_perfect_tdd_run_has_zero_excess_area(self) -> None:
        # WRITE_TEST→red→PATCH→綠：全程只有原始 MAIN 存續
        ev = events(B=[1, 1, 1, 0], M=[1, 1, 1, 0])
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.area_excess == 0 and m.area_total == 3

    def test_excess_area_counts_self_inflicted_debt(self) -> None:
        # REGRESSION/REOPENED 等自生債務：B_t − M_t 的存續面積
        ev = events(B=[2, 3, 1, 0], M=[1, 1, 0, 0])
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.area_total == 6
        assert m.area_excess == 4  # (2−1)+(3−1)+(1−0)+(0−0)

    def test_zero_turn_run_has_zero_area(self) -> None:
        ev = events(B=[], M=[], B0=1)
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.area_total == 0 and m.area_excess == 0
        assert m.ftr == 0.0
        assert m.control == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# FTR：start-of-turn 歸屬（F14）
# ---------------------------------------------------------------------------


class TestFtr:
    def test_ftr_uses_start_of_turn_backlog(self) -> None:
        # turn 1：起手 B_0=1（未淹水）花 1000 造成 B→2；
        # turn 2：起手 B_1=2 > B_0 花 9000 修回 B→1。
        ev = events(B0=1, turns=[(1000, 2), (9000, 1)])
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.ftr == pytest.approx(0.9)
        assert m.flood_create_tokens == 1000 and m.flood_repair_tokens == 9000

    def test_perfect_run_has_zero_ftr_and_split_tokens(self) -> None:
        # B 從未高於 B_0：無 flood token；末回合 1→0 是解 MAIN，非 repair
        ev = events(B=[1, 1, 1, 0], M=[1, 1, 1, 0])
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.ftr == 0.0
        assert m.flood_create_tokens == 0 and m.flood_repair_tokens == 0

    def test_reviewer_tokens_count_in_turn_delta(self) -> None:
        ev = events(B0=1, turns=[(600, 2), (500, 1)])
        ev[2]["ledger"] = _ledger(500, reviewer_tokens=400)  # turn 2 共 900
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.ftr == pytest.approx(900 / 1500)
        assert m.flood_repair_tokens == 900

    def test_zero_total_tokens_yields_zero_ftr(self) -> None:
        ev = events(B0=1, turns=[(0, 2), (0, 1)])
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.ftr == 0.0


# ---------------------------------------------------------------------------
# Flood Index 與 Control（報告 §7.4；excess 代入、review-debt 權重 0）
# ---------------------------------------------------------------------------


class TestControl:
    def test_control_is_100_for_perfect_run_despite_total_area(self) -> None:
        # P1/T1 完美 run：total>0 但 excess=0 → Control 不被結構性扣分（F13）
        ev = events(B=[1, 1, 0], M=[1, 1, 0])
        m = flood_metrics(ev, CARD, COEFFS)
        assert m.area_total == 2
        assert m.flood_index == 0.0
        assert m.control == pytest.approx(100.0)
        assert m.tau_uncalibrated is True

    def test_flood_index_formula(self) -> None:
        ev = events(
            B=[2, 3, 1, 0],
            M=[1, 1, 0, 0],
            counters={
                "regression": 1,
                "reopen": 2,
                "duplicate": 1,
                "reverted_loc": 50,
                "s_scope_loc": 3,
            },
        )
        m = flood_metrics(ev, CARD, COEFFS)
        # F = (4 + 2.0·1 + 1.5·2 + 0.5·1 + 0.02·50 + 3) / 1.0
        assert m.flood_index == pytest.approx(13.5)
        assert m.control == pytest.approx(100 * math.exp(-13.5))

    def test_flood_index_divides_by_difficulty_scale(self) -> None:
        ev = events(B=[2, 0], M=[1, 0], counters={"regression": 1})
        card = replace(CARD, difficulty_scale=2.0)
        m = flood_metrics(ev, card, COEFFS)
        assert m.flood_index == pytest.approx((1 + 2.0) / 2.0)

    def test_calibrated_tau_changes_control(self) -> None:
        ev = events(B=[2, 0], M=[1, 0])
        m = flood_metrics(ev, CARD, COEFFS, tau=2.0)
        assert m.tau == pytest.approx(2.0)
        assert m.tau_uncalibrated is False
        assert m.control == pytest.approx(100 * math.exp(-1 / 2.0))

    def test_result_type(self) -> None:
        m = flood_metrics(events(B=[1, 0]), CARD, COEFFS)
        assert isinstance(m, FloodMetrics)


# ---------------------------------------------------------------------------
# 係數檔：版本化、review-debt 權重 0、fail-closed
# ---------------------------------------------------------------------------


class TestCoeffs:
    def test_default_coeffs_match_report_7_4(self) -> None:
        c = load_flood_coeffs()
        assert c.regression == pytest.approx(2.0)
        assert c.reopen == pytest.approx(1.5)
        assert c.review_debt == 0.0  # §6.2：reviewer findings 不進 Control
        assert c.duplicate == pytest.approx(0.5)
        assert c.reverted_loc == pytest.approx(0.02)

    def test_nonzero_review_debt_weight_rejected(self, tmp_path) -> None:
        path = tmp_path / "coeffs.yaml"
        path.write_text(
            "schema_version: 1\n"
            "weights:\n"
            "  regression: 2.0\n"
            "  reopen: 1.5\n"
            "  review_debt: 1.0\n"
            "  duplicate: 0.5\n"
            "  reverted_loc: 0.02\n",
            encoding="utf-8",
        )
        with pytest.raises(FloodError):
            load_flood_coeffs(path)

    def test_missing_weight_rejected(self, tmp_path) -> None:
        path = tmp_path / "coeffs.yaml"
        path.write_text(
            "schema_version: 1\nweights:\n  regression: 2.0\n",
            encoding="utf-8",
        )
        with pytest.raises(FloodError):
            load_flood_coeffs(path)

    def test_unknown_schema_version_rejected(self, tmp_path) -> None:
        path = tmp_path / "coeffs.yaml"
        path.write_text(
            "schema_version: 99\n"
            "weights:\n"
            "  regression: 2.0\n"
            "  reopen: 1.5\n"
            "  review_debt: 0.0\n"
            "  duplicate: 0.5\n"
            "  reverted_loc: 0.02\n",
            encoding="utf-8",
        )
        with pytest.raises(FloodError):
            load_flood_coeffs(path)


# ---------------------------------------------------------------------------
# events fail-closed
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_missing_baseline_rejected(self) -> None:
        ev = [e for e in events(B=[1, 0]) if e["type"] != "baseline"]
        with pytest.raises(FloodError):
            flood_metrics(ev, CARD, COEFFS)

    def test_m_t_exceeding_b_t_rejected(self) -> None:
        ev = events(B=[1, 0])
        ev[1]["queue"]["m_t"] = 5
        with pytest.raises(FloodError):
            flood_metrics(ev, CARD, COEFFS)

    def test_missing_billed_totals_rejected(self) -> None:
        ev = events(B=[1, 0])
        del ev[1]["ledger"]["author"]["billed_output_total"]
        with pytest.raises(FloodError):
            flood_metrics(ev, CARD, COEFFS)

    def test_missing_counters_key_rejected(self) -> None:
        ev = events(B=[1, 0])
        del ev[-1]["queue"]["counters"]["reverted_loc"]
        with pytest.raises(FloodError):
            flood_metrics(ev, CARD, COEFFS)

    def test_nonpositive_difficulty_scale_rejected(self) -> None:
        card = replace(CARD, difficulty_scale=0.0)
        with pytest.raises(FloodError):
            flood_metrics(events(B=[1, 0]), card, COEFFS)

    def test_nonpositive_tau_rejected(self) -> None:
        with pytest.raises(FloodError):
            flood_metrics(events(B=[1, 0]), CARD, COEFFS, tau=0.0)
