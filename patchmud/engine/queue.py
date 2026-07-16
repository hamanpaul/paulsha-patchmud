"""Issue queue 引擎：deterministic 觸發規則（spec §8.1、§8.2 資料源）。

- 規則只依 probe 結果（三態）、diff 幾何與宣告欄位運作，**queue 更新零 LLM
  參與**（spec §8.1）。
- 所有「綠→紅」判定相對**引擎上一次執行結果**（含 turn-0 baseline，F11）；
  green = ``status == "passed"``，`failed` 與 `error` 都是「不綠」但永不混同
  （plan invariant 1）。
- `DiffGeometry` 是 turn loop（Task 13）自 workspace cumulative diff numstat
  組出的幾何視圖：per-file `FileChange`（SCOPE 判定需逐檔 LOC 歸屬，聚合的
  `DiffStats` 不夠）＋ 累計 `reverted_loc`（CHURN 以每回合增量判定）。
- churn 門檻 card 可調（spec §8.1）；card schema 尚無該欄位，先以
  `from_card(churn_threshold=...)` 注入、預設 10（待整合進 deck 契約）。
- item id／type 為結構化協定（英文）；敘事文案是 render 層的事（§5.4），
  本模組不產生任何 agent-facing 文字。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field, replace
from typing import Literal, Mapping, Sequence

from patchmud.deck.model import IssueCard, PublicRequirement
from patchmud.engine.protocol import Action, Patch, Triage
from patchmud.evaluator.power import FileChange
from patchmud.sandbox.probes import ProbeOutcome, ProbeResults, smoke_probe_id

__all__ = [
    "DEFAULT_CHURN_THRESHOLD",
    "DiffGeometry",
    "IssueItem",
    "IssueQueue",
    "QueueCounters",
    "QueueDelta",
    "QueueError",
]

IssueType = Literal[
    "MAIN", "REOPENED", "REGRESSION", "SCOPE(hard)", "CHURN", "DUPLICATE"
]
_ItemStatus = Literal["open", "resolved", "closed"]

#: 單回合 revert 自己先前新增行的 CHURN 門檻（spec §8.1；card 可調，待入契約）。
DEFAULT_CHURN_THRESHOLD = 10

#: agent 自寫測試路徑；非 production，不進 SCOPE 判定（與 evaluator 對齊）。
_AGENT_TEST_PREFIX = "tests/agent/"

#: 可被 PATCH TARGET_ISSUES 宣告修復的 item 類型（failed_claim 語意，§8.1）。
_CLAIMABLE_TYPES: frozenset[str] = frozenset({"MAIN", "REOPENED", "REGRESSION"})


class QueueError(Exception):
    """queue 契約違反（baseline 缺 card 綁定 probe 等），fail-closed。"""


@dataclass(frozen=True)
class DiffGeometry:
    """cumulative diff 的幾何視圖：逐檔變更量＋累計自我撤銷行數。"""

    file_changes: tuple[FileChange, ...] = ()
    #: workspace 累計 reverted_loc（`DiffStats.reverted_loc` 同源）；
    #: CHURN 以「本回合增量 = 本值 − 上回合值」判定。
    reverted_loc: int = 0


@dataclass(frozen=True)
class IssueItem:
    """queue 一項；id／type 英文（結構化協定），敘事交給 render 層。"""

    item_id: str
    type: IssueType
    #: 綁定 probe（MAIN／REOPENED／REGRESSION）；其餘類型 None。
    probe: str | None = None
    #: 所屬 public requirement id（MAIN／REOPENED）。
    requirement_id: str | None = None
    #: card 的 requirement 原文（MAIN／REOPENED；render 素材）。
    text: str = ""
    #: SCOPE(hard) 聚合的越界檔案清單（排序穩定）。
    files: tuple[str, ...] = ()
    #: DUPLICATE 引用的已 resolved／已關閉 item id。
    reference: str | None = None


@dataclass(frozen=True)
class QueueDelta:
    """一次 update 的變化：resolve／spawn／close（依規則、非 resolve 的關閉）。"""

    resolved: tuple[IssueItem, ...] = ()
    spawned: tuple[IssueItem, ...] = ()
    closed: tuple[IssueItem, ...] = ()


@dataclass(frozen=True)
class QueueCounters:
    """spec §8.1 計數器；reopen/regression/duplicate/churn 為累計 spawn 數。"""

    reopen: int = 0
    regression: int = 0
    duplicate: int = 0
    churn_events: int = 0
    #: PATCH 宣告修復但 probe 執行後仍紅（與 REOPENED 分開記，F11）。
    failed_claims: int = 0
    #: 目前 open 的 SCOPE(hard) 越界檔案數（0 = 無殘留）。
    scope_hard_open: int = 0
    #: 目前 cumulative diff 中 allowed 內、expected 外的 production LOC（F12）。
    s_scope_loc: int = 0


@dataclass
class _Tracked:
    item: IssueItem
    status: _ItemStatus
    spawn_turn: int


@dataclass
class _ReqState:
    """單一 public requirement 的生命週期狀態。"""

    requirement: PublicRequirement
    open_id: str | None
    last_resolved_id: str | None = None
    ever_resolved: bool = False
    reopen_seq: int = 0


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    """與 deck loader／evaluator 相同的 glob 語意（fnmatch，`**` 跨層）。"""
    return any(
        path == pattern or fnmatch.fnmatch(path, pattern) for pattern in patterns
    )


class IssueQueue:
    """spec §8.1 的可執行化：MAIN／REOPENED／REGRESSION／SCOPE／CHURN／DUPLICATE。"""

    def __init__(
        self,
        card: IssueCard,
        baseline: ProbeResults,
        *,
        churn_threshold: int = DEFAULT_CHURN_THRESHOLD,
    ) -> None:
        self._card = card
        self._churn_threshold = churn_threshold
        self._items: dict[str, _Tracked] = {}
        self._turn = 0

        # 引擎上一次執行結果（含 baseline）；transitions 的比較基準（F11）。
        self._last_outcomes: dict[str, ProbeOutcome] = {}
        self._last_reverted_loc = 0

        # 計數器
        self._reopen = 0
        self._regression = 0
        self._duplicate = 0
        self._churn_events = 0
        self._failed_claims = 0
        self._s_scope_loc = 0

        # id 序號
        self._regression_seq = 0
        self._scope_seq = 0
        self._churn_seq = 0
        self._duplicate_seq = 0

        # regression／compat probe 監看集合（id 與 ProbeSuite.from_card 同源：
        # smoke id 一律經 smoke_probe_id 建構，杜絕格式漂移的 fail-open）
        watched: list[str] = []
        for rp in card.regression_probes:
            if rp.path is not None:
                watched.append(rp.path)
            elif rp.smoke is not None:
                watched.append(smoke_probe_id(rp.smoke))
        watched.extend(cp.probe for cp in card.compat_probes)
        self._watched: tuple[str, ...] = tuple(dict.fromkeys(watched))
        self._open_regression: dict[str, str] = {}  # probe_id -> item_id
        self._open_scope_id: str | None = None

        # baseline 必須涵蓋 card 綁定的全部 public probes（turn-0 全跑，§5.2）
        bound = [req.probe for req in card.public_requirements] + list(self._watched)
        missing = [p for p in dict.fromkeys(bound) if p not in baseline]
        if missing:
            raise QueueError(
                f"turn-0 baseline 缺 card 綁定 probe：{', '.join(missing)}"
            )

        # MAIN：run 開始時每個 public_requirements[] 一項（spec §8.1）
        self._req_states: dict[str, _ReqState] = {}
        for req in card.public_requirements:
            item = IssueItem(
                item_id=req.id,
                type="MAIN",
                probe=req.probe,
                requirement_id=req.id,
                text=req.text,
            )
            self._items[item.item_id] = _Tracked(item, "open", spawn_turn=0)
            self._req_states[req.id] = _ReqState(requirement=req, open_id=req.id)

        self._last_outcomes.update(dict(baseline))

    # ---- 建構 ---------------------------------------------------------------

    @classmethod
    def from_card(
        cls,
        card: IssueCard,
        baseline: ProbeResults,
        *,
        churn_threshold: int = DEFAULT_CHURN_THRESHOLD,
    ) -> "IssueQueue":
        return cls(card, baseline, churn_threshold=churn_threshold)

    # ---- 對外視圖 -----------------------------------------------------------

    def open_items(self) -> list[IssueItem]:
        return [t.item for t in self._items.values() if t.status == "open"]

    @property
    def counters(self) -> QueueCounters:
        scope_hard_open = 0
        if self._open_scope_id is not None:
            scope_hard_open = len(self._items[self._open_scope_id].item.files)
        return QueueCounters(
            reopen=self._reopen,
            regression=self._regression,
            duplicate=self._duplicate,
            churn_events=self._churn_events,
            failed_claims=self._failed_claims,
            scope_hard_open=scope_hard_open,
            s_scope_loc=self._s_scope_loc,
        )

    def snapshot(self) -> dict:
        """B_t／M_t 與 open items（flood 計量與 turn event 的資料源，§8.2）。"""
        open_items = self.open_items()
        m_t = sum(1 for i in open_items if i.type == "MAIN")
        counters = self.counters
        return {
            "b_t": len(open_items),
            "m_t": m_t,
            "open_items": [
                {"item_id": i.item_id, "type": i.type, "probe": i.probe}
                for i in open_items
            ],
            "counters": {
                "reopen": counters.reopen,
                "regression": counters.regression,
                "duplicate": counters.duplicate,
                "churn_events": counters.churn_events,
                "failed_claims": counters.failed_claims,
                "scope_hard_open": counters.scope_hard_open,
                "s_scope_loc": counters.s_scope_loc,
            },
        }

    # ---- 每回合更新 ---------------------------------------------------------

    def update(
        self,
        probe_results: ProbeResults,
        diff_geometry: DiffGeometry,
        action: Action | None,
    ) -> QueueDelta:
        """author turn 結束的 queue 更新（引擎每 mutating action 後呼叫，§5.2）。"""
        self._turn += 1
        resolved: list[IssueItem] = []
        spawned: list[IssueItem] = []
        closed: list[IssueItem] = []

        # CHURN 事件性 item：下一回合自動關閉（spec §8.1）
        self._autoclose_churn(closed)

        # 宣告語意（DUPLICATE／failed_claim）以行動當下（pre-update）狀態判定
        claimable_probes = {
            t.item.item_id: t.item.probe
            for t in self._items.values()
            if t.status == "open" and t.item.type in _CLAIMABLE_TYPES
        }
        self._spawn_duplicates(action, spawned)
        if isinstance(action, Triage):
            self._close_duplicates(closed)

        self._update_requirements(probe_results, resolved, spawned, closed)
        self._update_regressions(probe_results, resolved, spawned)
        self._update_scope(diff_geometry, resolved, spawned)
        self._detect_churn(diff_geometry, spawned)
        self._count_failed_claims(action, probe_results, claimable_probes)

        self._last_outcomes.update(dict(probe_results))
        self._last_reverted_loc = diff_geometry.reverted_loc
        return QueueDelta(tuple(resolved), tuple(spawned), tuple(closed))

    # ---- 內部規則 -----------------------------------------------------------

    def _track(self, item: IssueItem) -> None:
        if item.item_id in self._items:
            raise QueueError(f"item id 重複：{item.item_id}")
        self._items[item.item_id] = _Tracked(item, "open", spawn_turn=self._turn)

    def _autoclose_churn(self, closed: list[IssueItem]) -> None:
        for tracked in self._items.values():
            if (
                tracked.item.type == "CHURN"
                and tracked.status == "open"
                and tracked.spawn_turn < self._turn
            ):
                tracked.status = "closed"
                closed.append(tracked.item)

    def _spawn_duplicates(
        self, action: Action | None, spawned: list[IssueItem]
    ) -> None:
        """PATCH 的 TARGET_ISSUES 引用已 resolved／已關閉 item → DUPLICATE。"""
        if not isinstance(action, Patch):
            return
        for target in dict.fromkeys(action.target_issues):
            tracked = self._items.get(target)
            if tracked is None or tracked.status == "open":
                continue
            self._duplicate_seq += 1
            item = IssueItem(
                item_id=f"DUP-{self._duplicate_seq}",
                type="DUPLICATE",
                reference=target,
            )
            self._track(item)
            self._duplicate += 1
            spawned.append(item)

    def _close_duplicates(self, closed: list[IssueItem]) -> None:
        for tracked in self._items.values():
            if tracked.item.type == "DUPLICATE" and tracked.status == "open":
                tracked.status = "closed"
                closed.append(tracked.item)

    def _update_requirements(
        self,
        probe_results: Mapping[str, ProbeOutcome],
        resolved: list[IssueItem],
        spawned: list[IssueItem],
        closed: list[IssueItem],
    ) -> None:
        """MAIN resolve／REOPENED spawn·resolve（綁定 probe 全綠 = passed）。"""
        for state in self._req_states.values():
            outcome = probe_results.get(state.requirement.probe)
            if outcome is None:  # 本次未執行（subset）：狀態不變
                continue
            green = outcome.status == "passed"
            if state.open_id is not None and green:
                tracked = self._items[state.open_id]
                tracked.status = "resolved"
                resolved.append(tracked.item)
                state.last_resolved_id = state.open_id
                state.open_id = None
                state.ever_resolved = True
            elif state.open_id is None and not green and state.ever_resolved:
                # 曾 resolved、probe 再紅 → 原 item 關閉、spawn REOPENED
                assert state.last_resolved_id is not None
                prev = self._items[state.last_resolved_id]
                prev.status = "closed"
                closed.append(prev.item)
                state.reopen_seq += 1
                req = state.requirement
                item = IssueItem(
                    item_id=f"{req.id}-R{state.reopen_seq}",
                    type="REOPENED",
                    probe=req.probe,
                    requirement_id=req.id,
                    text=req.text,
                )
                self._track(item)
                state.open_id = item.item_id
                self._reopen += 1
                spawned.append(item)

    def _update_regressions(
        self,
        probe_results: ProbeResults,
        resolved: list[IssueItem],
        spawned: list[IssueItem],
    ) -> None:
        """regression／compat probe 綠→紅（相對上次執行結果）→ REGRESSION。"""
        prev = ProbeResults(self._last_outcomes)
        for transition in probe_results.transitions(prev):
            probe_id = transition.probe_id
            if probe_id not in self._watched:
                continue
            if transition.change != "green_to_red":
                continue
            if probe_id in self._open_regression:
                continue
            self._regression_seq += 1
            item = IssueItem(
                item_id=f"REG-{self._regression_seq}",
                type="REGRESSION",
                probe=probe_id,
            )
            self._track(item)
            self._open_regression[probe_id] = item.item_id
            self._regression += 1
            spawned.append(item)

        # resolve：open REGRESSION 的 probe 本次綠
        for probe_id, item_id in list(self._open_regression.items()):
            outcome = probe_results.get(probe_id)
            if outcome is not None and outcome.status == "passed":
                tracked = self._items[item_id]
                tracked.status = "resolved"
                resolved.append(tracked.item)
                del self._open_regression[probe_id]

    def _update_scope(
        self,
        geometry: DiffGeometry,
        resolved: list[IssueItem],
        spawned: list[IssueItem],
    ) -> None:
        """SCOPE(hard) 聚合單一 item；SCOPE(soft) 只更新 s_scope_loc（F12）。"""
        card = self._card
        production = [
            fc
            for fc in geometry.file_changes
            if not fc.path.startswith(_AGENT_TEST_PREFIX)
        ]
        hard_files = tuple(
            sorted(
                fc.path
                for fc in production
                if not _matches_any(fc.path, card.allowed_paths)
            )
        )
        self._s_scope_loc = sum(
            fc.loc
            for fc in production
            if _matches_any(fc.path, card.allowed_paths)
            and not _matches_any(fc.path, card.expected_paths)
        )

        open_tracked = (
            self._items[self._open_scope_id]
            if self._open_scope_id is not None
            else None
        )
        if hard_files:
            if open_tracked is None:
                self._scope_seq += 1
                item = IssueItem(
                    item_id=f"SCOPE-HARD-{self._scope_seq}",
                    type="SCOPE(hard)",
                    files=hard_files,
                )
                self._track(item)
                self._open_scope_id = item.item_id
                spawned.append(item)
            elif open_tracked.item.files != hard_files:
                # 檔案清單變動：更新聚合 item（同一 item，不重複 spawn）
                open_tracked.item = replace(open_tracked.item, files=hard_files)
        elif open_tracked is not None:
            # 越界變更全數撤回 → resolve
            open_tracked.status = "resolved"
            resolved.append(open_tracked.item)
            self._open_scope_id = None

    def _detect_churn(
        self, geometry: DiffGeometry, spawned: list[IssueItem]
    ) -> None:
        """單回合 revert ≥ 門檻行自己先前新增的行 → 事件性 CHURN item。"""
        reverted_this_turn = geometry.reverted_loc - self._last_reverted_loc
        if reverted_this_turn < self._churn_threshold:
            return
        self._churn_seq += 1
        item = IssueItem(item_id=f"CHURN-{self._churn_seq}", type="CHURN")
        self._track(item)
        self._churn_events += 1
        spawned.append(item)

    def _count_failed_claims(
        self,
        action: Action | None,
        probe_results: Mapping[str, ProbeOutcome],
        claimable_probes: Mapping[str, str | None],
    ) -> None:
        """PATCH 宣告修復（pre-update open 的 MAIN/REOPENED/REGRESSION），
        該 probe 本次執行後仍不綠 → failed_claims +1（claim 語意，F11）。"""
        if not isinstance(action, Patch):
            return
        for target in dict.fromkeys(action.target_issues):
            probe_id = claimable_probes.get(target)
            if probe_id is None:
                continue
            outcome = probe_results.get(probe_id)
            if outcome is not None and outcome.status != "passed":
                self._failed_claims += 1
