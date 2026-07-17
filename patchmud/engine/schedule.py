"""Pilot 排程：全域單一隨機排列的 sealed schedule（spec §11、F21）。

- ``build_schedule(matrix, seed)``：seed 經 PRNG（``random.Random``）決定
  **完整 run 排程**——整個 encounters × loadouts × models 矩陣的全域執行順序
  單一隨機排列，encounter、loadout、model 三軸都被打散（非只有 block 內
  模型順序）。
- schedule 在任何 run 開跑前落盤封存（``schedule.yaml`` + sha256）；已封存
  即不可覆寫。讀取時重算 hash 與檔內 hash 比對，不符（遭竄改或非本矩陣
  產物）一律 fail-closed :class:`ScheduleError`——runner 拒跑。
- run_id 由三軸座標決定（``<encounter>--<loadout>--<model>``），與排列無關，
  供 run registry 冪等續跑（spec §11）。
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from patchmud.engine.strategy import Loadout

__all__ = [
    "SCHEDULE_SCHEMA_VERSION",
    "RunMatrix",
    "Schedule",
    "ScheduleError",
    "ScheduleItem",
    "build_schedule",
    "load_schedule",
    "save_schedule",
]

SCHEDULE_SCHEMA_VERSION = 1


class ScheduleError(ValueError):
    """schedule 契約違反：矩陣非法、檔案缺失、hash 不符、封存覆寫等。"""


@dataclass(frozen=True)
class ScheduleItem:
    """矩陣中一格 = 一場 run；run_id 由座標決定、與執行順序無關。"""

    run_id: str
    encounter: str
    loadout: str
    model: str


@dataclass(frozen=True)
class RunMatrix:
    """run 矩陣三軸（encounters × loadouts × models）；軸值非空且不重複。"""

    encounters: tuple[str, ...]
    loadouts: tuple[str, ...]
    models: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("encounters", "loadouts", "models"):
            values = getattr(self, name)
            object.__setattr__(self, name, tuple(values))
            values = getattr(self, name)
            if not values:
                raise ScheduleError(f"矩陣軸 {name} 不得為空")
            for value in values:
                if not isinstance(value, str) or not value:
                    raise ScheduleError(f"矩陣軸 {name} 的值必須是非空字串：{value!r}")
            if len(set(values)) != len(values):
                raise ScheduleError(f"矩陣軸 {name} 的值不得重複")
        for loadout in self.loadouts:
            try:
                Loadout.from_string(loadout)
            except ValueError as exc:
                raise ScheduleError(str(exc)) from exc


@dataclass(frozen=True)
class Schedule:
    """sealed 全域執行順序；hash 覆蓋 seed 與完整排列（F21）。"""

    seed: int
    items: tuple[ScheduleItem, ...]

    def payload(self) -> dict:
        """canonical 序列化內容（hash 的覆蓋範圍）。"""
        return {
            "schema_version": SCHEDULE_SCHEMA_VERSION,
            "seed": self.seed,
            "items": [asdict(item) for item in self.items],
        }

    @property
    def sha256(self) -> str:
        canonical = json.dumps(self.payload(), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_schedule(matrix: RunMatrix, seed: int) -> Schedule:
    """展開矩陣並以 seed 產生全域單一隨機排列（同 seed 同矩陣 → 同排列）。"""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScheduleError(f"seed 必須是 int：{seed!r}")
    items = [
        ScheduleItem(
            run_id=f"{encounter}--{loadout}--{model}",
            encounter=encounter,
            loadout=loadout,
            model=model,
        )
        for encounter in matrix.encounters
        for loadout in matrix.loadouts
        for model in matrix.models
    ]
    random.Random(seed).shuffle(items)
    return Schedule(seed=seed, items=tuple(items))


def save_schedule(schedule: Schedule, path: Path) -> None:
    """落盤封存（YAML + sha256）；已存在即拒絕覆寫（sealed 語意）。"""
    path = Path(path)
    if path.exists():
        raise ScheduleError(f"schedule 已封存，拒絕覆寫：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {**schedule.payload(), "sha256": schedule.sha256}
    path.write_text(
        yaml.safe_dump(doc, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )


def load_schedule(path: Path) -> Schedule:
    """讀取封存 schedule；檔案缺失、schema 不符或 hash 不符一律 fail-closed。"""
    path = Path(path)
    if not path.is_file():
        raise ScheduleError(f"schedule 檔不存在：{path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ScheduleError(f"schedule 檔內容必須是 mapping：{path}")
    if raw.get("schema_version") != SCHEDULE_SCHEMA_VERSION:
        raise ScheduleError(
            f"schedule schema_version 不符：{raw.get('schema_version')!r}"
        )
    seed = raw.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ScheduleError(f"schedule seed 必須是 int：{seed!r}")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise ScheduleError("schedule 必須含非空 items 列表")
    items: list[ScheduleItem] = []
    for entry in items_raw:
        if not isinstance(entry, dict):
            raise ScheduleError(f"schedule item 必須是 mapping：{entry!r}")
        fields = {}
        for key in ("run_id", "encounter", "loadout", "model"):
            value = entry.get(key)
            if not isinstance(value, str) or not value:
                raise ScheduleError(f"schedule item 缺欄位或值非字串：{key}")
            fields[key] = value
        if set(entry) != {"run_id", "encounter", "loadout", "model"}:
            raise ScheduleError(f"schedule item 含未知欄位：{sorted(entry)}")
        items.append(ScheduleItem(**fields))
    schedule = Schedule(seed=seed, items=tuple(items))
    stored = raw.get("sha256")
    if schedule.sha256 != stored:
        raise ScheduleError(
            "schedule hash 不符（檔案遭竄改或非本矩陣產物），拒絕執行（F21）"
        )
    return schedule
