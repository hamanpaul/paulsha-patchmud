"""Task 12 RED：strategy enforcer 與 gaming 向量全覆蓋（spec §6、plan Task 12）。

鎖定：
- T1 valid red（§6.1 防 gaming 硬化，F6）：`error` 不算 red（import error
  案例）；red 後改測試檔 → 該檔全部 red evidence 重置；`assert False` 假 red
  在測試檔 hash 不變前提下永不轉綠 → `tdd_compliant` 永 False。
- P1（F7）：第一次 PATCH 前必須有已凍結 plan；重複提交／已 PATCH 後提交 illegal。
- R1（F8）：空 diff review 不滿足 gate；合格 review（schema-valid + 非空 diff +
  findings 已 render）後 COMMIT 才合法。
- F5：T0 不禁止自發 red-first；observed_tdd_workflow 於所有 cell 記錄。
- P0/T0/R0：PLAY PLAN / SUMMON REVIEWER 對應 illegal（T0 的 PLAY TDD 由
  protocol parser 擋下，不進 enforcer）。
- 敘事 reason 一律出自 zh-TW render pack。
"""

from __future__ import annotations

import pytest

from patchmud.engine import render_zh_tw as zh
from patchmud.engine.plan_schema import PlanArtifact
from patchmud.engine.protocol import (
    Commit,
    Look,
    Patch,
    PlayPlan,
    Rollback,
    RunTest,
    SummonReviewer,
    Triage,
    WriteTest,
)
from patchmud.engine.strategy import (
    SOLO,
    EnforcementState,
    Loadout,
    StrategyEnforcer,
)
from patchmud.sandbox.probes import ProbeResults
from tests.evaluator.helpers import make_outcome

HASH_A = "aaaa1111"
HASH_B = "bbbb2222"
TEST_FILE = "tests/agent/test_x.py"
FAILED = "failed"
PASSED = "passed"
ERROR = "error"

STATE = EnforcementState()
PATCHED_STATE = EnforcementState(production_patch_applied=True)

patch_action = Patch(payload="--- a/src/x.py\n+++ b/src/x.py\n")

PLAN = PlanArtifact(
    requirements=("MAIN-1",),
    invariants=("snapshot 不因刪除失敗而改變",),
    files_to_inspect=("src/snapshot.py",),
    risks=("KeyError 邊界條件易誤判",),
    test_targets=("tests/agent/test_new.py",),
)


def agent_test(status: str, *nodeids: str) -> ProbeResults:
    """agent 自建測試的 nodeid 級結果（loop 以 junitxml 逐 nodeid 展開）。"""
    return ProbeResults({n: make_outcome(status) for n in nodeids})


def enforcer(P: int = 0, T: int = 0, R: int = 0) -> StrategyEnforcer:
    return StrategyEnforcer(Loadout(plan=bool(P), tdd=bool(T), reviewer=bool(R)))


# ---------------------------------------------------------------------------
# Loadout：三 bit 契約與字串表示
# ---------------------------------------------------------------------------


class TestLoadout:
    def test_from_string_roundtrip(self) -> None:
        lo = Loadout.from_string("P1T0R1")
        assert lo == Loadout(plan=True, tdd=False, reviewer=True)
        assert lo.name == "P1T0R1"

    def test_solo_is_all_zero(self) -> None:
        assert SOLO == Loadout(plan=False, tdd=False, reviewer=False)
        assert SOLO.name == "P0T0R0"

    @pytest.mark.parametrize("bad", ["P2T0R0", "p1t0r0", "P1T0", "", "T1P1R1"])
    def test_invalid_string_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            Loadout.from_string(bad)


# ---------------------------------------------------------------------------
# T1：valid red 判定與 green 閉環（§6.1，F6）
# ---------------------------------------------------------------------------


class TestTddGate:
    def test_assert_false_never_compliant(self) -> None:
        # plan Task 12 指定測試：假 red 無法在不改測試檔前提下轉綠（F6）
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))  # valid red
        assert e.check(patch_action, STATE).legal
        # 改測試檔讓它變綠 → red evidence 重置
        e.record_write_test({TEST_FILE: HASH_B}, nodeids=["test_red"])
        e.on_final(
            agent_test(PASSED, "test_red"), file_hashes={TEST_FILE: HASH_B}
        )
        assert e.tdd_state.compliant is False
        assert e.observed_tdd_workflow is False

    def test_patch_illegal_before_any_red(self) -> None:
        e = enforcer(T=1)
        verdict = e.check(patch_action, STATE)
        assert not verdict.legal
        assert verdict.reason == zh.text("strategy.tdd_red_required")

    def test_error_status_is_not_red(self) -> None:
        # T1 下 error 態（collection / import / syntax）不算 red（§6.1.2）
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(ERROR, "test_red"))
        assert not e.check(patch_action, STATE).legal
        assert e.tdd_state.red_nodeids == ()

    def test_unrecorded_failed_probe_is_not_red(self) -> None:
        # 公開 probe 紅不是 agent 自建測試的 red evidence
        e = enforcer(T=1)
        e.on_probe_results(
            ProbeResults({"tests/public/test_main.py": make_outcome(FAILED)})
        )
        assert not e.check(patch_action, STATE).legal

    def test_red_evidence_reset_makes_patch_illegal_again(self) -> None:
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        assert e.check(patch_action, STATE).legal
        # 期間任何修改即重置該檔全部 red evidence（§6.1.4a）
        e.record_write_test({TEST_FILE: HASH_B}, nodeids=["test_red"])
        assert not e.check(patch_action, STATE).legal
        assert e.tdd_state.red_nodeids == ()

    def test_same_hash_rerecord_keeps_evidence(self) -> None:
        # hash 未變 = 無修改：不重置既有 red evidence
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_more"])
        assert e.check(patch_action, STATE).legal
        assert "test_red" in e.tdd_state.red_nodeids

    def test_tdd_state_exposes_red_evidence(self) -> None:
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        state = e.tdd_state
        assert state.red_nodeids == ("test_red",)
        assert state.red_file_hashes == {TEST_FILE: HASH_A}
        assert state.compliant is False  # 終局前恆 False

    def test_green_closure_with_intact_hash_is_compliant(self) -> None:
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        e.on_final(
            agent_test(PASSED, "test_red"), file_hashes={TEST_FILE: HASH_A}
        )
        assert e.tdd_state.compliant is True

    def test_final_still_red_not_compliant(self) -> None:
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        e.on_final(
            agent_test(FAILED, "test_red"), file_hashes={TEST_FILE: HASH_A}
        )
        assert e.tdd_state.compliant is False

    def test_deleted_test_file_not_compliant(self) -> None:
        # red 測試檔終局時不存在（hash 缺席）→ 閉環不成立（§6.1.6）
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        e.on_final(agent_test(PASSED, "test_red"), file_hashes={})
        assert e.tdd_state.compliant is False

    def test_out_of_band_test_edit_at_final_not_compliant(self) -> None:
        # red 後測試檔經非 WRITE_TEST 管道改動（workspace 保護區不含
        # tests/agent/**，production PATCH / ROLLBACK 可觸及測試檔而不經
        # record_write_test）：終局檔案仍存在但 hash 與 red 時不同 →
        # 閉環不成立。§6.1.4a 的終局 hash 完全一致比對是此向量唯一防線，
        # 「檔案存在」弱化（path in file_hashes）必須被本測試抓到（F6）。
        e = enforcer(T=1)
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        e.record_patch_applied()
        e.on_final(
            agent_test(PASSED, "test_red"), file_hashes={TEST_FILE: HASH_B}
        )
        assert e.tdd_state.compliant is False
        assert e.observed_tdd_workflow is False


# ---------------------------------------------------------------------------
# F5：observed_tdd_workflow 於所有 cell 記錄；T0 不禁止自發 red-first
# ---------------------------------------------------------------------------


class TestObservedWorkflow:
    def test_t0_spontaneous_red_first_is_legal_and_observed(self) -> None:
        e = enforcer(T=0)
        assert e.check(patch_action, STATE).legal  # T0：PATCH 隨時合法
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        assert e.check(patch_action, STATE).legal
        e.record_patch_applied()
        e.on_final(
            agent_test(PASSED, "test_red"), file_hashes={TEST_FILE: HASH_A}
        )
        assert e.observed_tdd_workflow is True
        assert e.tdd_state.compliant is True

    def test_no_red_means_not_observed(self) -> None:
        e = enforcer(T=0)
        e.record_patch_applied()
        e.on_final(ProbeResults({}), file_hashes={})
        assert e.observed_tdd_workflow is False

    def test_patch_only_before_red_is_not_observed(self) -> None:
        # 序列必須是 valid-red → production → green；red 前的 patch 不算
        e = enforcer(T=0)
        e.record_patch_applied()
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        e.on_final(
            agent_test(PASSED, "test_red"), file_hashes={TEST_FILE: HASH_A}
        )
        assert e.observed_tdd_workflow is False


# ---------------------------------------------------------------------------
# P1／P0：plan gate（F7）與 observed_planning
# ---------------------------------------------------------------------------


class TestPlanGate:
    def test_p1_patch_before_frozen_plan_illegal(self) -> None:
        # plan Task 12 指定：P1 先 PATCH → illegal
        e = enforcer(P=1)
        verdict = e.check(patch_action, STATE)
        assert not verdict.legal
        assert verdict.reason == zh.text("strategy.plan_required_before_patch")
        e.record_plan(PLAN)
        assert e.check(patch_action, STATE).legal

    def test_p1_play_plan_legal_before_patch(self) -> None:
        e = enforcer(P=1)
        assert e.check(PlayPlan(payload="requirements: [MAIN-1]"), STATE).legal

    def test_p1_duplicate_play_plan_illegal(self) -> None:
        # 通過後凍結，後續修改一律 illegal（§6.3）
        e = enforcer(P=1)
        e.record_plan(PLAN)
        assert not e.check(PlayPlan(payload="x"), STATE).legal

    def test_p1_play_plan_after_patch_illegal(self) -> None:
        e = enforcer(P=1)
        assert not e.check(PlayPlan(payload="x"), PATCHED_STATE).legal

    def test_p0_play_plan_illegal(self) -> None:
        e = enforcer(P=0)
        verdict = e.check(PlayPlan(payload="x"), STATE)
        assert not verdict.legal
        assert verdict.reason == zh.text("strategy.plan_forbidden")

    def test_observed_planning(self) -> None:
        e = enforcer(P=1)
        assert e.observed_planning is False
        e.record_plan(PLAN)
        assert e.observed_planning is True
        # P0 下 agent 無法提交 PlanArtifact：此欄恆 false（F5，欄位對稱）
        assert enforcer(P=0).observed_planning is False


# ---------------------------------------------------------------------------
# R1／R0：reviewer gate（F8）
# ---------------------------------------------------------------------------


class TestReviewerGate:
    def test_commit_illegal_without_review(self) -> None:
        e = enforcer(R=1)
        verdict = e.check(Commit(), PATCHED_STATE)
        assert not verdict.legal
        assert verdict.reason == zh.text("strategy.review_required_before_commit")

    def test_empty_diff_review_does_not_satisfy_gate(self) -> None:
        # plan Task 12 指定：R1 空 diff review 不滿足 gate（F8）
        e = enforcer(R=1)
        e.record_review(
            schema_valid=True, diff_nonempty=False, findings_rendered=True
        )
        assert e.reviewer_gate_satisfied is False
        assert not e.check(Commit(), STATE).legal

    def test_invalid_schema_review_does_not_satisfy_gate(self) -> None:
        e = enforcer(R=1)
        e.record_review(
            schema_valid=False, diff_nonempty=True, findings_rendered=True
        )
        assert e.reviewer_gate_satisfied is False

    def test_unrendered_findings_do_not_satisfy_gate(self) -> None:
        # findings 必須在 COMMIT 之前的某個 turn render 給作者（F8）
        e = enforcer(R=1)
        e.record_review(
            schema_valid=True, diff_nonempty=True, findings_rendered=False
        )
        assert e.reviewer_gate_satisfied is False

    def test_qualified_review_opens_commit(self) -> None:
        e = enforcer(R=1)
        e.record_review(
            schema_valid=True, diff_nonempty=True, findings_rendered=True
        )
        assert e.reviewer_gate_satisfied is True
        assert e.check(Commit(), PATCHED_STATE).legal

    def test_r1_summon_legal(self) -> None:
        e = enforcer(R=1)
        assert e.check(SummonReviewer(), STATE).legal

    def test_r0_summon_illegal(self) -> None:
        e = enforcer(R=0)
        verdict = e.check(SummonReviewer(), STATE)
        assert not verdict.legal
        assert verdict.reason == zh.text("strategy.reviewer_forbidden")


# ---------------------------------------------------------------------------
# SOLO 與全開 loadout 的端到端合法性序列
# ---------------------------------------------------------------------------


class TestLoadoutSemantics:
    def test_solo_has_no_extra_restrictions(self) -> None:
        # SOLO = P0T0R0：除 P0/R0 禁用命令外無任何額外限制（報告 §8.1）
        e = StrategyEnforcer(SOLO)
        for action in (
            Look(),
            WriteTest(payload="d"),
            patch_action,
            RunTest(),
            Triage(),
            Rollback(),
            Commit(),
        ):
            verdict = e.check(action, STATE)
            assert verdict.legal and verdict.reason is None

    def test_full_loadout_flow(self) -> None:
        e = enforcer(P=1, T=1, R=1)
        assert not e.check(patch_action, STATE).legal  # 無 plan 無 red
        e.record_plan(PLAN)
        assert not e.check(patch_action, STATE).legal  # 有 plan 仍無 red
        e.record_write_test({TEST_FILE: HASH_A}, nodeids=["test_red"])
        e.on_probe_results(agent_test(FAILED, "test_red"))
        assert e.check(patch_action, STATE).legal
        e.record_patch_applied()
        assert not e.check(Commit(), PATCHED_STATE).legal  # 無合格 review
        e.record_review(
            schema_valid=True, diff_nonempty=True, findings_rendered=True
        )
        assert e.check(Commit(), PATCHED_STATE).legal
        e.on_final(
            agent_test(PASSED, "test_red"), file_hashes={TEST_FILE: HASH_A}
        )
        assert e.observed_tdd_workflow is True
        assert e.tdd_state.compliant is True
