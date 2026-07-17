"""Pilot runner：sealed schedule 逐項執行、registry 冪等續跑、preflight gates
（spec §10.4–10.5、§11）。

- **啟動前 gates（任一不滿足即拒絕啟動，fail-closed）**：
  1. `analysis/registered/estimators.yaml` 已 commit（F4：校準 estimator 必須
     在 pilot 開跑前 pre-register；以 git 追蹤且無未 commit 變更為準）。
  2. sandbox namespace 能力齊備（mount＋net＋pid，§7；degraded 時 ranked／
     pilot run 拒絕啟動）。
  3. 地端模型（``local: true``）已註冊 ``cost_scenarios: {low, mid, high}``
     （F18；$/hour serving rate，金額 `decimal.Decimal`）。
- **schedule fail-closed**：runner 只吃已封存的 ``schedule.yaml``（Task 18
  schedule 模組）；檔案不存在或 hash 不符即拒跑，並依序執行、不得改變順序
  （F21）。
- **run registry（JSONL，append-only，write-ahead）**：每場 run 執行**前**
  先追加 ``status: "started"`` 行、execute 成功返回後追加 ``status: "done"``
  行。重啟時已 done 的 run_id 冪等跳過，只補殘餘；started 而無 done 的
  attempt 視為中斷殘留（kill／provider 錯誤上拋時 run 目錄可能已建立）——
  續跑取「已見最大 attempt + 1」為新 attempt，使 cli 端以 attempt 導出的
  run 目錄（``<run_id>--attempt<N>``）絕不與 partial run 目錄相撞：partial
  目錄一律保留、不需手動清理、不需 ``force``。重跑已完成 run_id 必須顯式
  ``force``（同樣取新 attempt；舊 registry 行與舊 run 目錄一律保留）。
  registry 綁定 schedule hash，跨 schedule 混用即拒絕。
- 執行面走注入 seam（``execute(item, attempt)``）：unit tests 全 fake，不啟
  真 namespace、不打真模型 API（plan invariant 3）；真佈線在 cli
  （`patchmud pilot`）。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from patchmud.engine.schedule import Schedule, ScheduleItem, load_schedule
from patchmud.sandbox.isolate import Capabilities

__all__ = [
    "ESTIMATORS_RELPATH",
    "MODELS_SCHEMA_VERSION",
    "REGISTRY_SCHEMA_VERSION",
    "ExecuteFn",
    "GateError",
    "ModelEntry",
    "PilotError",
    "PilotReport",
    "PilotRunner",
    "check_gates",
    "load_models",
]

MODELS_SCHEMA_VERSION = 1
REGISTRY_SCHEMA_VERSION = 1

#: F4：pre-registered estimator 檔的 repo-relative 路徑（spec §10.4）。
ESTIMATORS_RELPATH = "analysis/registered/estimators.yaml"

_COST_SCENARIO_KEYS = ("low", "mid", "high")
_MODEL_KEYS = {"id", "adapter", "local", "cost_scenarios"}
_REGISTRY_RESERVED_KEYS = {
    "schema_version",
    "run_id",
    "attempt",
    "schedule_sha256",
    "status",
}


class PilotError(ValueError):
    """pilot 契約違反：models 檔非法、registry 損毀／錯配、force 目標不明等。"""


class GateError(PilotError):
    """preflight gate 不滿足 → 拒絕啟動 pilot（fail-closed，spec §11）。"""


@dataclass(frozen=True)
class ModelEntry:
    """models.yaml 一列：adapter spec ＋ 地端計費註冊（F18）。"""

    id: str
    adapter: str
    local: bool = False
    #: 地端模型 $/hour serving rate 三情境（Decimal）；非地端可為 None。
    cost_scenarios: dict[str, Decimal] | None = None


#: 單場 run 執行 seam：(item, attempt) → registry 附加欄位（run_dir 等）。
ExecuteFn = Callable[[ScheduleItem, int], Mapping[str, object]]


@dataclass(frozen=True)
class PilotReport:
    """一次 `PilotRunner.run` 的摘要：依 schedule 順序的執行／跳過清單。"""

    schedule_sha256: str
    executed: tuple[str, ...]
    skipped: tuple[str, ...]


# ---------------------------------------------------------------------------
# models.yaml
# ---------------------------------------------------------------------------


def load_models(path: Path) -> tuple[ModelEntry, ...]:
    """讀取 models.yaml；schema 錯誤一律 fail-closed :class:`PilotError`。"""
    path = Path(path)
    if not path.is_file():
        raise PilotError(f"models 檔不存在：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PilotError(f"models 檔內容必須是 mapping：{path}")
    if raw.get("schema_version") != MODELS_SCHEMA_VERSION:
        raise PilotError(
            f"models schema_version 不符：{raw.get('schema_version')!r}"
        )
    models_raw = raw.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        raise PilotError("models 檔必須含非空 models 列表")

    entries: list[ModelEntry] = []
    seen: set[str] = set()
    for entry in models_raw:
        if not isinstance(entry, dict):
            raise PilotError(f"models 列表項必須是 mapping：{entry!r}")
        unknown = set(entry) - _MODEL_KEYS
        if unknown:
            raise PilotError(f"models 列表項含未知欄位：{sorted(unknown)}")
        model_id = entry.get("id")
        adapter = entry.get("adapter")
        for key, value in (("id", model_id), ("adapter", adapter)):
            if not isinstance(value, str) or not value:
                raise PilotError(f"models 列表項缺欄位或值非字串：{key}")
        if model_id in seen:
            raise PilotError(f"models id 重複：{model_id}")
        seen.add(model_id)
        local = entry.get("local", False)
        if not isinstance(local, bool):
            raise PilotError(f"模型 {model_id} 的 local 必須是 bool：{local!r}")
        scenarios_raw = entry.get("cost_scenarios")
        scenarios = (
            None
            if scenarios_raw is None
            else _parse_cost_scenarios(scenarios_raw, model_id)
        )
        entries.append(
            ModelEntry(
                id=model_id, adapter=adapter, local=local, cost_scenarios=scenarios
            )
        )
    return tuple(entries)


def _parse_cost_scenarios(raw: object, model_id: str) -> dict[str, Decimal]:
    """cost_scenarios 恰含 low/mid/high 三情境；金額 Decimal、禁 float（F18）。"""
    if not isinstance(raw, dict) or set(raw) != set(_COST_SCENARIO_KEYS):
        raise PilotError(
            f"模型 {model_id} 的 cost_scenarios 必須恰含 low/mid/high 三情境（F18）"
        )
    scenarios: dict[str, Decimal] = {}
    for key in _COST_SCENARIO_KEYS:
        value = raw[key]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise PilotError(
                f"模型 {model_id} 的 cost_scenarios.{key} 金額必須是字串或整數"
                f"（Decimal 契約，禁 float）：{value!r}"
            )
        try:
            amount = Decimal(str(value))
        except InvalidOperation as exc:
            raise PilotError(
                f"模型 {model_id} 的 cost_scenarios.{key} 不是合法金額：{value!r}"
            ) from exc
        if amount <= 0:
            raise PilotError(
                f"模型 {model_id} 的 cost_scenarios.{key} 必須 > 0：{value!r}"
            )
        scenarios[key] = amount
    return scenarios


# ---------------------------------------------------------------------------
# preflight gates（spec §11：任一不滿足即拒絕啟動）
# ---------------------------------------------------------------------------


def check_gates(
    *,
    repo_root: Path,
    capabilities: Capabilities,
    models: Sequence[ModelEntry],
    estimators_relpath: str = ESTIMATORS_RELPATH,
) -> None:
    """三個 preflight gates；任一不滿足 raise :class:`GateError`（fail-closed）。"""
    _gate_estimators_committed(Path(repo_root), estimators_relpath)
    _gate_capabilities(capabilities)
    _gate_local_cost_scenarios(models)


def _gate_estimators_committed(repo_root: Path, relpath: str) -> None:
    """F4：estimators.yaml 必須存在、被 git 追蹤且無未 commit 變更。"""
    path = repo_root / relpath
    if not path.is_file():
        raise GateError(f"estimators 未 pre-register：{path} 不存在（F4），拒絕啟動")
    tracked = _git(repo_root, "ls-files", "--error-unmatch", relpath)
    if tracked.returncode != 0:
        raise GateError(
            f"estimators 檔未被 git 追蹤（未 commit）：{relpath}（F4），拒絕啟動"
        )
    status = _git(repo_root, "status", "--porcelain", "--", relpath)
    if status.returncode != 0:
        raise GateError(f"git status 失敗，無法驗證 estimators commit 狀態：{relpath}")
    if status.stdout.strip():
        raise GateError(
            f"estimators 檔有未 commit 的變更：{relpath}（F4），拒絕啟動"
        )


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args], capture_output=True, text=True
    )


def _gate_capabilities(capabilities: Capabilities) -> None:
    """§7：mount＋net＋pid namespace 齊備；degraded 即拒絕啟動 pilot run。"""
    if not (capabilities.mount_ns and capabilities.net_ns and capabilities.pid_ns):
        raise GateError(
            "sandbox namespace 能力不足（degraded）：pilot run 拒絕啟動（§7）"
        )


def _gate_local_cost_scenarios(models: Sequence[ModelEntry]) -> None:
    """F18：地端模型必須於 pilot 前註冊 cost_scenarios 三情境。"""
    for entry in models:
        if entry.local and entry.cost_scenarios is None:
            raise GateError(
                f"地端模型 {entry.id} 未註冊 cost_scenarios（F18），拒絕啟動"
            )


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


@dataclass
class PilotRunner:
    """依 sealed schedule 逐項執行；registry 冪等續跑、force 顯式重跑。"""

    registry_path: Path
    execute: ExecuteFn
    models: Sequence[ModelEntry]
    capabilities: Callable[[], Capabilities]
    repo_root: Path
    estimators_relpath: str = ESTIMATORS_RELPATH

    def run(
        self, schedule_path: Path, *, force: Collection[str] = ()
    ) -> PilotReport:
        """gates → 載入 sealed schedule → 依序執行（write-ahead registry）。

        每場 run 先追加 ``started`` 行再呼叫 execute、成功返回後追加
        ``done`` 行；中斷（execute 例外）直接上拋——重啟時 done 行使已完成
        run 自然跳過，殘留的 ``started`` 行使續跑改用新 attempt（中斷留下
        的 partial run 目錄一律保留、絕不相撞）。
        """
        check_gates(
            repo_root=self.repo_root,
            capabilities=self.capabilities(),
            models=self.models,
            estimators_relpath=self.estimators_relpath,
        )
        schedule = load_schedule(Path(schedule_path))
        force_set = set(force)
        unknown = force_set - {item.run_id for item in schedule.items}
        if unknown:
            raise PilotError(
                f"force 指定的 run_id 不在 schedule 內：{sorted(unknown)}"
            )
        done_attempts, started_attempts = self._load_registry(schedule)

        executed: list[str] = []
        skipped: list[str] = []
        for item in schedule.items:
            done = done_attempts.get(item.run_id, 0)
            if done > 0 and item.run_id not in force_set:
                skipped.append(item.run_id)
                continue
            attempt = max(done, started_attempts.get(item.run_id, 0)) + 1
            self._append_registry(item, attempt, schedule.sha256, "started", {})
            extra = dict(self.execute(item, attempt))
            self._append_registry(item, attempt, schedule.sha256, "done", extra)
            done_attempts[item.run_id] = attempt
            executed.append(item.run_id)
        return PilotReport(
            schedule_sha256=schedule.sha256,
            executed=tuple(executed),
            skipped=tuple(skipped),
        )

    def _load_registry(
        self, schedule: Schedule
    ) -> tuple[dict[str, int], dict[str, int]]:
        """讀 registry JSONL → (done, started) 各自的 run_id → 最大 attempt。

        done 決定冪等跳過；started（write-ahead 行）決定續跑新 attempt 的
        下限——started 而無對應 done 即中斷殘留。損毀／錯配 fail-closed。
        """
        path = Path(self.registry_path)
        done_attempts: dict[str, int] = {}
        started_attempts: dict[str, int] = {}
        if not path.exists():
            return done_attempts, started_attempts
        known = {item.run_id for item in schedule.items}
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PilotError(
                    f"registry 第 {lineno} 行不是合法 JSON：{path}"
                ) from exc
            if not isinstance(record, dict):
                raise PilotError(f"registry 第 {lineno} 行必須是 JSON object")
            if record.get("schema_version") != REGISTRY_SCHEMA_VERSION:
                raise PilotError(
                    f"registry schema_version 不符：{record.get('schema_version')!r}"
                )
            status = record.get("status")
            if status not in ("started", "done"):
                raise PilotError(
                    f"registry 第 {lineno} 行 status 非 started/done：{status!r}"
                )
            if record.get("schedule_sha256") != schedule.sha256:
                raise PilotError(
                    "registry 屬於不同 schedule（hash 不符），拒絕續跑"
                )
            run_id = record.get("run_id")
            attempt = record.get("attempt")
            if not isinstance(run_id, str) or run_id not in known:
                raise PilotError(
                    f"registry 第 {lineno} 行 run_id 不在 schedule 內：{run_id!r}"
                )
            if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
                raise PilotError(
                    f"registry 第 {lineno} 行 attempt 必須是正整數：{attempt!r}"
                )
            target = done_attempts if status == "done" else started_attempts
            target[run_id] = max(target.get(run_id, 0), attempt)
        return done_attempts, started_attempts

    def _append_registry(
        self,
        item: ScheduleItem,
        attempt: int,
        schedule_sha256: str,
        status: str,
        extra: dict[str, object],
    ) -> None:
        collisions = set(extra) & _REGISTRY_RESERVED_KEYS
        if collisions:
            raise PilotError(
                f"execute 回傳欄位與 registry 保留欄位衝突：{sorted(collisions)}"
            )
        record = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "run_id": item.run_id,
            "attempt": attempt,
            "schedule_sha256": schedule_sha256,
            "status": status,
            **extra,
        }
        try:
            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        except TypeError as exc:
            raise PilotError(f"registry 行無法 JSON 序列化：{exc}") from exc
        path = Path(self.registry_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
