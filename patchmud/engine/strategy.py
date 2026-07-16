"""Strategy enforcer：forced loadout `(P, T, R)` 的可執行化（spec §6）。

- `check(action, state)` 只判 **策略層** legality（P／T／R 三 gate）；路徑白名
  單、apply 失敗、rollback 空 stack 等 workspace 語意是 sandbox／turn loop 的
  事（§5.1）。illegal 動作照樣消耗 turn（§5.2）——那是 loop 的記帳，本層只出
  verdict。
- T1 valid red（§6.1 防 gaming 硬化，F6）：`WRITE_TEST` 落地後 loop 以
  `record_write_test` 登記新增 pytest nodeid 與測試檔 content hash；隨後執行
  中該 nodeid 以 pytest ``failed``（assertion 失敗）收場才算 red——``error``
  （collection／import／syntax）永不算（invariant 1）。red evidence 綁定
  red 當下的測試檔 hash；期間該檔任何修改（hash 變動）即重置該檔全部 red
  evidence。green 閉環（§6.1.4）：終局至少一個 red nodeid 測試檔 hash 與 red
  時完全一致且結果 ``passed`` → `tdd_compliant`；`assert False` 假 red 因此
  被結構性排除。
- F5 treatment 語意：T0 不禁止自發 red-first；`observed_tdd_workflow`（
  valid-red → production → green 序列確實發生）與 `observed_planning` 於所有
  cell 記錄，供 per-protocol 次要分析。
- R1 合格 review（F8）：schema-valid ＋ 輸入 diff 非空（至少一個 production
  patch 已套用）＋ findings 已在 COMMIT 前 render 給作者；三者由 reviewer
  subcall（Task 13）以 `record_review` 回報事實，本層只聚合 gate。
- 敘事 reason 一律出自 zh-TW render pack（§5.4）；loadout 名（`P1T0R1`）為
  結構化協定，維持英文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from patchmud.engine import render_zh_tw as zh
from patchmud.engine.plan_schema import PlanArtifact
from patchmud.engine.protocol import (
    Action,
    Commit,
    Patch,
    PlayPlan,
    SummonReviewer,
)
from patchmud.sandbox.probes import ProbeOutcome

__all__ = [
    "SOLO",
    "EnforcementState",
    "Loadout",
    "StrategyEnforcer",
    "StrategyError",
    "TddState",
    "Verdict",
]

_LOADOUT_RE = re.compile(r"^P([01])T([01])R([01])$")


class StrategyError(Exception):
    """enforcer 契約誤用（如重複凍結 plan），fail-closed。"""


@dataclass(frozen=True)
class Loadout:
    """forced loadout 三 bit（spec §6）；SOLO = P0T0R0（報告 §8.1）。"""

    plan: bool
    tdd: bool
    reviewer: bool

    @classmethod
    def from_string(cls, text: str) -> "Loadout":
        """解析 `P<0|1>T<0|1>R<0|1>`（treatment cell 名，英文結構化協定）。"""
        match = _LOADOUT_RE.match(text)
        if match is None:
            raise ValueError(f"loadout 格式必須為 P<0|1>T<0|1>R<0|1>：{text!r}")
        p, t, r = match.groups()
        return cls(plan=p == "1", tdd=t == "1", reviewer=r == "1")

    @property
    def name(self) -> str:
        return f"P{int(self.plan)}T{int(self.tdd)}R{int(self.reviewer)}"


SOLO = Loadout(plan=False, tdd=False, reviewer=False)


@dataclass(frozen=True)
class EnforcementState:
    """check 時點由 turn loop 提供的 workspace 事實（enforcer 不碰檔案系統）。"""

    #: 至少一個 production patch 已成功套用（PLAY PLAN 時間閘，§5.1）。
    production_patch_applied: bool = False


@dataclass(frozen=True)
class Verdict:
    """策略判定：illegal 時 reason 為 zh-TW 敘事（render pack）。"""

    legal: bool
    reason: str | None = None


@dataclass(frozen=True)
class TddState:
    """目前 red evidence 視圖與終局 compliant 判定（§6.1）。"""

    red_nodeids: tuple[str, ...]
    red_file_hashes: dict[str, str]
    #: 終局 green 閉環（§6.1.4）；`on_final` 前恆 False。
    compliant: bool


@dataclass
class _RedEvidence:
    """單一 nodeid 的 red evidence：檔案 hash 綁定＋failure fingerprint 封存。"""

    file_hashes: dict[str, str]
    fingerprints: tuple[str, ...]
    #: red 持有期間是否有 production patch 套用（observed workflow 序列用）。
    patched_after: bool = False


def _illegal(key: str) -> Verdict:
    return Verdict(legal=False, reason=zh.text(key))


class StrategyEnforcer:
    """run 開始載入 loadout、逐 action 強制執行 P／T／R 三 gate（spec §6）。"""

    def __init__(self, loadout: Loadout) -> None:
        self._loadout = loadout
        self._plan: PlanArtifact | None = None
        #: nodeid → 所屬 WRITE_TEST 觸及檔案的最新 hash（record 時綁定）。
        self._nodeid_files: dict[str, dict[str, str]] = {}
        #: nodeid → red evidence（達成 valid red 的封存，§6.1.2）。
        self._evidence: dict[str, _RedEvidence] = {}
        self._review_qualified = False
        self._final_compliant = False
        self._final_observed = False

    # ---- 對外視圖 -----------------------------------------------------------

    @property
    def loadout(self) -> Loadout:
        return self._loadout

    @property
    def plan(self) -> PlanArtifact | None:
        """已凍結 plan（reviewer 輸入白名單成員，§6.2）；未凍結為 None。"""
        return self._plan

    @property
    def tdd_state(self) -> TddState:
        hashes: dict[str, str] = {}
        for evidence in self._evidence.values():
            hashes.update(evidence.file_hashes)
        return TddState(
            red_nodeids=tuple(self._evidence),
            red_file_hashes=hashes,
            compliant=self._final_compliant,
        )

    @property
    def red_fingerprints(self) -> dict[str, tuple[str, ...]]:
        """red evidence 的 failure fingerprints（event log 封存用，§6.1.2）。"""
        return {n: e.fingerprints for n, e in self._evidence.items()}

    @property
    def reviewer_gate_satisfied(self) -> bool:
        return self._review_qualified

    @property
    def observed_tdd_workflow(self) -> bool:
        """valid-red → production → green 序列確實發生（所有 cell 記錄，F5）。"""
        return self._final_observed

    @property
    def observed_planning(self) -> bool:
        """P0 下 agent 無法提交 PlanArtifact，此欄恆 False（欄位對稱，F5）。"""
        return self._plan is not None

    # ---- 策略判定 -----------------------------------------------------------

    def check(self, action: Action, state: EnforcementState) -> Verdict:
        """action 執行前的策略 legality；illegal 的 turn 記帳由 loop 負責。"""
        if isinstance(action, PlayPlan):
            if not self._loadout.plan:
                return _illegal("strategy.plan_forbidden")
            if self._plan is not None:
                return _illegal("strategy.plan_duplicate")
            if state.production_patch_applied:
                return _illegal("strategy.plan_after_patch")
            return Verdict(legal=True)
        if isinstance(action, Patch):
            if self._loadout.plan and self._plan is None:
                return _illegal("strategy.plan_required_before_patch")
            if self._loadout.tdd and not self._evidence:
                return _illegal("strategy.tdd_red_required")
            return Verdict(legal=True)
        if isinstance(action, SummonReviewer):
            if not self._loadout.reviewer:
                return _illegal("strategy.reviewer_forbidden")
            return Verdict(legal=True)
        if isinstance(action, Commit):
            if self._loadout.reviewer and not self._review_qualified:
                return _illegal("strategy.review_required_before_commit")
            return Verdict(legal=True)
        # LOOK / INSPECT / WRITE_TEST / RUN_TEST / TRIAGE / ROLLBACK：
        # 策略層無限制（T0 的 PLAY TDD 由 protocol parser 擋下，不進本層）。
        return Verdict(legal=True)

    # ---- run 事實登記（turn loop 於動作落地後呼叫） --------------------------

    def record_plan(self, plan: PlanArtifact) -> None:
        """凍結通過 §6.3 schema 的 plan；重複凍結是 loop 契約錯誤。"""
        if self._plan is not None:
            raise StrategyError("plan 已凍結，不得重複 record_plan")
        self._plan = plan

    def record_write_test(
        self, file_hashes: Mapping[str, str], nodeids: Sequence[str]
    ) -> None:
        """WRITE_TEST 落地：登記新增 nodeid 與測試檔 content hash（§6.1.1）。

        觸及檔案 hash 變動 → 該檔全部 red evidence 重置（§6.1.4a：期間任何
        修改即重置），既有 nodeid 的檔案綁定同步更新為新 hash。
        """
        for path, digest in file_hashes.items():
            for nodeid, evidence in list(self._evidence.items()):
                bound = evidence.file_hashes.get(path)
                if bound is not None and bound != digest:
                    del self._evidence[nodeid]
        for assoc in self._nodeid_files.values():
            for path, digest in file_hashes.items():
                if path in assoc:
                    assoc[path] = digest
        for nodeid in nodeids:
            self._nodeid_files[nodeid] = dict(file_hashes)

    def record_patch_applied(self) -> None:
        """production patch 成功套用：標記目前持有的 red evidence（序列追蹤）。"""
        for evidence in self._evidence.values():
            evidence.patched_after = True

    def record_review(
        self, *, schema_valid: bool, diff_nonempty: bool, findings_rendered: bool
    ) -> None:
        """reviewer subcall 完成：三條件全真才構成合格 review（F8）。"""
        if schema_valid and diff_nonempty and findings_rendered:
            self._review_qualified = True

    # ---- probe 觀測 -----------------------------------------------------------

    def on_probe_results(self, results: Mapping[str, ProbeOutcome]) -> None:
        """追蹤 valid red：登記過的 nodeid 以 ``failed`` 收場 → red evidence。

        ``error`` 永不算 red（§6.1.2、invariant 1）；未登記的 probe（公開套件
        等）不構成 evidence。
        """
        for nodeid, assoc in self._nodeid_files.items():
            if nodeid in self._evidence:
                continue
            outcome = results.get(nodeid)
            if outcome is not None and outcome.status == "failed":
                self._evidence[nodeid] = _RedEvidence(
                    file_hashes=dict(assoc),
                    fingerprints=tuple(outcome.failure_fingerprints),
                )

    def on_final(
        self,
        results: Mapping[str, ProbeOutcome],
        *,
        file_hashes: Mapping[str, str],
    ) -> None:
        """終局 green 閉環判定（§6.1.4）：定案 compliant 與 observed workflow。

        閉環 = red evidence nodeid 之（a）綁定測試檔終局 hash 與 red 時完全
        一致（缺檔即不成立）且（b）終局結果 ``passed``。observed workflow 另
        要求該 evidence 於 red 持有期間有 production patch 套用（F5 序列）。
        """
        closures = [
            evidence
            for nodeid, evidence in self._evidence.items()
            if (outcome := results.get(nodeid)) is not None
            and outcome.status == "passed"
            and all(
                file_hashes.get(path) == digest
                for path, digest in evidence.file_hashes.items()
            )
        ]
        self._final_compliant = bool(closures)
        self._final_observed = any(e.patched_after for e in closures)
