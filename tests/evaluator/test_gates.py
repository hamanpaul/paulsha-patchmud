"""Task 5 RED：hard gates 三個 cap（spec §9.2）與 `Clear=0` 連動（spec §5.2）。

- critical requirements 任一紅 → `critical_pass=False`（連動 Clear=0；Economy/Utility
  cap 由下游 metrics 引用）。
- evaluator collection 階段失敗（非 perf-only probe 出現 `error`）→ Power ≤ 15。
- 公開 API 不相容（card `compat_probes` 任一紅）→ Power ≤ 50；多 cap 疊加取低。
- 存取 hidden 資產（人工複核確認）→ run invalid。
- `compute_clear` 是 §5.2 唯一布林公式的唯一實作。
"""

from __future__ import annotations

from patchmud.evaluator.gates import (
    COLLECTION_FAILURE_CAP,
    COMPAT_BREAK_CAP,
    GateResult,
    apply_power_cap,
    compute_clear,
    evaluate_gates,
)
from patchmud.evaluator.power import score_power
from tests.evaluator.helpers import (
    API,
    CR1,
    CR2,
    EDGES,
    HIDDEN_COMPAT,
    PERF1,
    PERF2,
    green,
    make_card,
    make_outcome,
)

CARD = make_card()
TIMINGS = {PERF1: 500, PERF2: 1000}


def _all_green() -> dict:
    return green(CR1, CR2, EDGES, API, HIDDEN_COMPAT, PERF1, PERF2, cases=3)


# ---------------------------------------------------------------------------
# evaluate_gates
# ---------------------------------------------------------------------------


class TestEvaluateGates:
    def test_clean_run_no_gates(self):
        assert evaluate_gates(CARD, _all_green()) == GateResult(
            critical_pass=True, power_cap=None, run_invalid=False
        )

    def test_critical_red_fails_critical_without_power_cap(self):
        # gate 1 效果是 Clear=0/Economy=0/Utility≤49，不是 Power cap
        outcomes = _all_green() | {CR1: make_outcome("failed")}
        gates = evaluate_gates(CARD, outcomes)
        assert gates.critical_pass is False
        assert gates.power_cap is None
        assert gates.run_invalid is False

    def test_critical_error_is_not_green_and_triggers_collection_cap(self):
        outcomes = _all_green() | {CR1: make_outcome("error", cases_total=0)}
        gates = evaluate_gates(CARD, outcomes)
        assert gates.critical_pass is False
        assert gates.power_cap == COLLECTION_FAILURE_CAP

    def test_missing_critical_outcome_fail_closed(self):
        outcomes = _all_green()
        del outcomes[CR1]
        assert evaluate_gates(CARD, outcomes).critical_pass is False

    def test_collection_error_caps_power_15(self):
        outcomes = _all_green() | {EDGES: make_outcome("error", cases_total=0)}
        assert evaluate_gates(CARD, outcomes).power_cap == COLLECTION_FAILURE_CAP

    def test_compat_probe_red_caps_power_50(self):
        outcomes = _all_green() | {API: make_outcome("failed")}
        assert evaluate_gates(CARD, outcomes).power_cap == COMPAT_BREAK_CAP

    def test_hidden_rubric_compat_red_is_not_api_gate(self):
        # rubric compatibility 分數歸零歸 power.py 管；§9.2 API gate 只看 card compat_probes
        outcomes = _all_green() | {HIDDEN_COMPAT: make_outcome("failed")}
        assert evaluate_gates(CARD, outcomes).power_cap is None

    def test_multiple_caps_take_minimum(self):
        outcomes = _all_green() | {
            EDGES: make_outcome("error", cases_total=0),
            API: make_outcome("failed"),
        }
        assert evaluate_gates(CARD, outcomes).power_cap == COLLECTION_FAILURE_CAP

    def test_perf_only_probe_error_is_not_collection_failure(self):
        # 只出現在 runtime_efficiency 的 probe error → perf 0 分（fail-closed），
        # 但不觸發「無法 import / compile / 起測試」cap
        outcomes = _all_green() | {PERF2: make_outcome("error", cases_total=0)}
        assert evaluate_gates(CARD, outcomes).power_cap is None

    def test_hidden_access_detected_invalidates_run(self):
        gates = evaluate_gates(CARD, _all_green(), hidden_access_detected=True)
        assert gates.run_invalid is True


# ---------------------------------------------------------------------------
# apply_power_cap：與 rubric 分數疊加取低
# ---------------------------------------------------------------------------


class TestApplyPowerCap:
    def _report(self, outcomes):
        return score_power(
            CARD,
            outcomes,
            (),
            lint_new_diagnostics=0,
            reference_timings_ms=TIMINGS,
        )

    def test_no_cap_keeps_total(self):
        report = self._report(_all_green())
        gates = GateResult(critical_pass=True, power_cap=None, run_invalid=False)
        assert apply_power_cap(report, gates).total == report.total

    def test_cap_50_clamps_total(self):
        report = self._report(_all_green())
        assert report.total > 50
        gates = GateResult(critical_pass=True, power_cap=50, run_invalid=False)
        capped = apply_power_cap(report, gates)
        assert capped.total == 50.0
        # 分項封存值不變，只 cap 總分
        assert capped.functional == report.functional
        assert capped.robustness == report.robustness

    def test_cap_above_total_is_noop(self):
        outcomes = _all_green() | {
            CR1: make_outcome("failed"),
            CR2: make_outcome("failed"),
            EDGES: make_outcome("failed", cases_total=3, cases_passed=0),
            API: make_outcome("failed"),
            HIDDEN_COMPAT: make_outcome("failed"),
            PERF1: make_outcome("failed"),
            PERF2: make_outcome("failed"),
        }
        report = self._report(outcomes)
        assert report.total < 15
        gates = GateResult(critical_pass=False, power_cap=15, run_invalid=False)
        assert apply_power_cap(report, gates).total == report.total


# ---------------------------------------------------------------------------
# compute_clear：§5.2 唯一布林公式
# ---------------------------------------------------------------------------


class TestComputeClear:
    def test_all_conditions_met_clear_1(self):
        assert (
            compute_clear(
                critical_pass=True, main_public_green=True, protocol_failed=False
            )
            == 1
        )

    def test_critical_gate_links_to_clear_0(self):
        gates = evaluate_gates(CARD, _all_green() | {CR1: make_outcome("failed")})
        assert (
            compute_clear(
                critical_pass=gates.critical_pass,
                main_public_green=True,
                protocol_failed=False,
            )
            == 0
        )

    def test_main_public_red_clear_0(self):
        assert (
            compute_clear(
                critical_pass=True, main_public_green=False, protocol_failed=False
            )
            == 0
        )

    def test_failed_protocol_clear_0(self):
        assert (
            compute_clear(
                critical_pass=True, main_public_green=True, protocol_failed=True
            )
            == 0
        )
