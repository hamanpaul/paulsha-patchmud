"""Task 11 RED：issue queue 引擎 deterministic 觸發規則（spec §8.1、plan Task 11）。

鎖定（§8.1 逐條）：
- MAIN：run 開始每個 public_requirements[] 一項；綁定 probe 全綠 resolve。
- REOPENED：曾 resolved 的 MAIN 其 probe 再紅 → 原 item 關閉、spawn REOPENED。
- failed_claim：PATCH 宣告 MAIN/REOPENED/REGRESSION 而 probe 執行後仍紅 →
  計數器 +1，與 probe 語意的 REOPENED 分開記（F11）。
- REGRESSION：regression/compat probe 相對引擎上次執行結果（含 turn-0
  baseline）綠→紅才 spawn；baseline 紅的 probe「更紅」不 spawn（F11）。
- SCOPE(hard)：allowed_paths 外 production 檔案聚合單一 item 附檔案清單；
  越界變更全數撤回 → resolve。
- SCOPE(soft)：allowed 內、expected 外的 LOC 進 `s_scope_loc`，不生 item（F12）。
- CHURN：單回合 revert ≥ 10 行自己先前新增的行 → 事件性 item，下回合自動關閉。
- DUPLICATE：PATCH 的 TARGET_ISSUES 引用已 resolved / 已關閉 item；TRIAGE 關閉。
- snapshot：B_t = 開放 item 數；M_t = 其中從未 resolved 過的原始 MAIN 數（§8.2）。

queue 更新零 LLM 參與：規則只依 probe 結果、diff 幾何與宣告欄位運作。
"""

from __future__ import annotations

import pytest

from patchmud.engine.protocol import Patch, Triage
from patchmud.engine.queue import (
    DiffGeometry,
    IssueQueue,
    QueueError,
)
from patchmud.evaluator.power import FileChange
from patchmud.sandbox.probes import ProbeResults
from tests.evaluator.helpers import make_card, make_outcome

# make_card 綁定的 public probe id（見 tests/evaluator/helpers.py）
MAIN_PROBE = "tests/public/test_main.py"
REG_PROBE = "tests/starter/"
COMPAT_PROBE = "tests/public/test_api.py"

NO_DIFF = DiffGeometry(file_changes=(), reverted_loc=0)


def results(
    main: str = "failed", reg: str = "passed", compat: str = "passed"
) -> ProbeResults:
    """全套 public probe 結果（引擎每 mutating action 後自動全跑，§5.2）。"""
    return ProbeResults(
        {
            MAIN_PROBE: make_outcome(main),
            REG_PROBE: make_outcome(reg),
            COMPAT_PROBE: make_outcome(compat),
        }
    )


def queue_with_main_red() -> IssueQueue:
    """turn-0 baseline：MAIN 紅、regression/compat 綠（典型 encounter 起點）。"""
    return IssueQueue.from_card(make_card(), results(main="failed"))


def patch_claiming(*ids: str) -> Patch:
    return Patch(target_issues=tuple(ids))


# ---------------------------------------------------------------------------
# MAIN：spawn 於 run 開始、resolve 於綁定 probe 全綠
# ---------------------------------------------------------------------------


class TestMain:
    def test_from_card_spawns_main_per_public_requirement(self) -> None:
        q = queue_with_main_red()
        items = q.open_items()
        assert [(i.item_id, i.type) for i in items] == [("MAIN-1", "MAIN")]
        assert items[0].probe == MAIN_PROBE

    def test_main_resolved_on_green(self) -> None:
        q = queue_with_main_red()
        d = q.update(results(main="passed"), NO_DIFF, Patch())
        assert [i.item_id for i in d.resolved] == ["MAIN-1"]
        assert q.open_items() == []

    def test_main_error_state_does_not_resolve(self) -> None:
        # 三態：error 不是綠（invariant 1）；MAIN 維持 open
        q = queue_with_main_red()
        d = q.update(results(main="error"), NO_DIFF, Patch())
        assert d.resolved == () and d.spawned == ()
        assert [i.item_id for i in q.open_items()] == ["MAIN-1"]

    def test_from_card_missing_baseline_probe_fail_closed(self) -> None:
        with pytest.raises(QueueError):
            IssueQueue.from_card(make_card(), ProbeResults({}))


# ---------------------------------------------------------------------------
# REOPENED 與 failed_claim（plan Task 11 指定測試）
# ---------------------------------------------------------------------------


class TestReopenAndClaims:
    def test_reopen_after_resolved(self) -> None:
        q = queue_with_main_red()
        q.update(results(main="passed"), NO_DIFF, Patch())  # resolved
        d = q.update(results(main="failed"), NO_DIFF, Patch())  # 再紅
        assert [i.type for i in d.spawned] == ["REOPENED"]
        # 原 item 關閉（spec §8.1）
        assert [i.item_id for i in d.closed] == ["MAIN-1"]
        assert q.counters.reopen == 1

    def test_failed_claim_counts_without_reopen(self) -> None:
        q = queue_with_main_red()
        q.update(results(main="failed"), NO_DIFF, patch_claiming("MAIN-1"))
        assert q.counters.failed_claims == 1 and q.counters.reopen == 0

    def test_claim_that_resolves_is_not_failed_claim(self) -> None:
        q = queue_with_main_red()
        d = q.update(results(main="passed"), NO_DIFF, patch_claiming("MAIN-1"))
        assert [i.item_id for i in d.resolved] == ["MAIN-1"]
        assert q.counters.failed_claims == 0

    def test_reopened_resolves_and_can_reopen_again(self) -> None:
        q = queue_with_main_red()
        q.update(results(main="passed"), NO_DIFF, Patch())
        q.update(results(main="failed"), NO_DIFF, Patch())  # 首次 REOPENED
        d = q.update(results(main="passed"), NO_DIFF, Patch())
        assert [i.type for i in d.resolved] == ["REOPENED"]
        d2 = q.update(results(main="failed"), NO_DIFF, Patch())
        assert [i.type for i in d2.spawned] == ["REOPENED"]
        assert q.counters.reopen == 2


# ---------------------------------------------------------------------------
# REGRESSION：只認相對引擎上次執行結果的綠→紅（F11）
# ---------------------------------------------------------------------------


class TestRegression:
    def test_baseline_red_compat_more_red_never_spawns(self) -> None:
        # turn-0 baseline 紅的 compat probe：「更紅」無綠→紅轉換，不 spawn
        base = results(main="failed", compat="failed")
        q = IssueQueue.from_card(make_card(), base)
        d1 = q.update(results(compat="error"), NO_DIFF, Patch())
        assert d1.spawned == () and q.counters.regression == 0
        # 首綠：無 item 可 resolve、也不 spawn
        d2 = q.update(results(compat="passed"), NO_DIFF, Patch())
        assert d2.spawned == () and d2.resolved == ()
        # 首綠後再紅 → spawn REGRESSION
        d3 = q.update(results(compat="failed"), NO_DIFF, Patch())
        assert [i.type for i in d3.spawned] == ["REGRESSION"]
        assert d3.spawned[0].probe == COMPAT_PROBE
        assert q.counters.regression == 1

    def test_regression_resolves_when_probe_green_again(self) -> None:
        q = queue_with_main_red()  # baseline REG 綠
        d1 = q.update(results(reg="failed"), NO_DIFF, Patch())
        assert [i.type for i in d1.spawned] == ["REGRESSION"]
        item = d1.spawned[0]
        assert item.probe == REG_PROBE
        # 持續紅：不重複 spawn
        d2 = q.update(results(reg="failed"), NO_DIFF, Patch())
        assert d2.spawned == ()
        # 再綠 → resolve
        d3 = q.update(results(reg="passed"), NO_DIFF, Patch())
        assert [i.item_id for i in d3.resolved] == [item.item_id]
        assert q.counters.regression == 1


# ---------------------------------------------------------------------------
# SCOPE：hard 聚合單一 item、soft 只進 s_scope_loc（F12）
# ---------------------------------------------------------------------------


class TestScope:
    def test_scope_hard_single_aggregated_item_and_revert_resolves(self) -> None:
        q = queue_with_main_red()
        geo = DiffGeometry(
            file_changes=(
                FileChange("lib/other.py", 5, 1),
                FileChange("setup.py", 2, 0),
            ),
            reverted_loc=0,
        )
        d1 = q.update(results(), geo, Patch())
        scope_items = [i for i in d1.spawned if i.type == "SCOPE(hard)"]
        assert len(scope_items) == 1
        assert scope_items[0].files == ("lib/other.py", "setup.py")
        assert q.counters.scope_hard_open == 2
        # 同一越界持續存在：不重複 spawn（聚合單一 item）
        d2 = q.update(results(), geo, Patch())
        assert not [i for i in d2.spawned if i.type == "SCOPE(hard)"]
        # 越界變更全數撤回 → resolve
        d3 = q.update(results(), NO_DIFF, Patch())
        assert [i.type for i in d3.resolved] == ["SCOPE(hard)"]
        assert q.counters.scope_hard_open == 0

    def test_soft_scope_loc_counts_without_item(self) -> None:
        q = queue_with_main_red()
        geo = DiffGeometry(
            file_changes=(
                FileChange("src/helper.py", 7, 2),  # allowed 內、expected 外
                FileChange("src/snapshot.py", 3, 1),  # expected 內：不計
            ),
            reverted_loc=0,
        )
        d = q.update(results(), geo, Patch())
        assert d.spawned == ()
        assert q.counters.s_scope_loc == 9
        # 對應變更撤回 → 歸零
        q.update(results(), NO_DIFF, Patch())
        assert q.counters.s_scope_loc == 0

    def test_agent_tests_never_count_as_scope(self) -> None:
        q = queue_with_main_red()
        geo = DiffGeometry(
            file_changes=(FileChange("tests/agent/test_mine.py", 30, 0),),
            reverted_loc=0,
        )
        d = q.update(results(), geo, Patch())
        assert d.spawned == ()
        assert q.counters.scope_hard_open == 0 and q.counters.s_scope_loc == 0


# ---------------------------------------------------------------------------
# CHURN：單回合 revert ≥ 門檻 → 事件性 item，下回合自動關閉
# ---------------------------------------------------------------------------


class TestChurn:
    def test_churn_spawns_and_autocloses_next_turn(self) -> None:
        q = queue_with_main_red()
        d1 = q.update(results(), DiffGeometry((), reverted_loc=12), Patch())
        churn = [i for i in d1.spawned if i.type == "CHURN"]
        assert len(churn) == 1 and q.counters.churn_events == 1
        # 當回合計入 B_t
        assert churn[0].item_id in [i.item_id for i in q.open_items()]
        # 下一回合（累計 reverted 未增 → 本回合 revert 0）自動關閉
        d2 = q.update(results(), DiffGeometry((), reverted_loc=12), Patch())
        assert churn[0].item_id in [i.item_id for i in d2.closed]
        assert all(i.type != "CHURN" for i in q.open_items())
        assert q.counters.churn_events == 1

    def test_churn_below_threshold_no_item(self) -> None:
        q = queue_with_main_red()
        d = q.update(results(), DiffGeometry((), reverted_loc=9), Patch())
        assert all(i.type != "CHURN" for i in d.spawned)
        assert q.counters.churn_events == 0


# ---------------------------------------------------------------------------
# DUPLICATE：引用已 resolved / 已關閉 item；TRIAGE 關閉
# ---------------------------------------------------------------------------


class TestDuplicate:
    def test_duplicate_when_patch_targets_resolved_item(self) -> None:
        q = queue_with_main_red()
        q.update(results(main="passed"), NO_DIFF, Patch())  # MAIN-1 resolved
        d = q.update(results(main="passed"), NO_DIFF, patch_claiming("MAIN-1"))
        assert [i.type for i in d.spawned] == ["DUPLICATE"]
        assert d.spawned[0].reference == "MAIN-1"
        assert q.counters.duplicate == 1
        # 引用已 resolved item 不是 failed claim
        assert q.counters.failed_claims == 0

    def test_triage_closes_open_duplicates(self) -> None:
        q = queue_with_main_red()
        q.update(results(main="passed"), NO_DIFF, Patch())
        q.update(results(main="passed"), NO_DIFF, patch_claiming("MAIN-1"))
        d = q.update(results(main="passed"), NO_DIFF, Triage())
        assert [i.type for i in d.closed] == ["DUPLICATE"]
        assert q.open_items() == []


# ---------------------------------------------------------------------------
# snapshot：B_t 與 M_t（§8.2 flood 計量的資料源）
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_b_t_m_t(self) -> None:
        q = queue_with_main_red()
        snap0 = q.snapshot()
        assert snap0["b_t"] == 1 and snap0["m_t"] == 1

        # MAIN 仍紅 + REGRESSION spawn
        q.update(results(main="failed", reg="failed"), NO_DIFF, Patch())
        snap1 = q.snapshot()
        assert snap1["b_t"] == 2 and snap1["m_t"] == 1

        # MAIN resolved：只剩自生債務
        q.update(results(main="passed", reg="failed"), NO_DIFF, Patch())
        snap2 = q.snapshot()
        assert snap2["b_t"] == 1 and snap2["m_t"] == 0

        # REOPENED 不算 M_t（M_t 只計從未 resolved 過的原始 MAIN）
        q.update(results(main="failed", reg="failed"), NO_DIFF, Patch())
        snap3 = q.snapshot()
        assert snap3["b_t"] == 2 and snap3["m_t"] == 0
