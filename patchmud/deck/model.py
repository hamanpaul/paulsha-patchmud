"""Deck domain model：IssueCard 與巢狀契約（spec §4.2 全欄位）。

所有結構皆為 frozen dataclass；集合欄位以 tuple 落地，維持不可變。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CARD_SCHEMA_VERSION = 1


class DeckError(ValueError):
    """deck 契約違反：schema 缺欄位、路徑邊界、public/hidden 重疊等。"""


@dataclass(frozen=True)
class PublicRequirement:
    """public_requirements 一項：MAIN item，綁一組 public probe。"""

    id: str
    text: str
    probe: str


@dataclass(frozen=True)
class CriticalRequirement:
    """critical_requirements 一項：hard gate，由 hidden evaluator 判定。"""

    id: str
    hidden_probe: str


@dataclass(frozen=True)
class RegressionProbe:
    """regression_probes 一項：pytest 路徑或 smoke 命令（二擇一）。"""

    path: str | None = None
    smoke: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CompatProbe:
    """compat_probes 一項：公開 API 相容檢查。"""

    probe: str


@dataclass(frozen=True)
class RubricGroup:
    """functional rubric 的一組：all-or-nothing 得分。"""

    id: str
    points: int
    probe: str


@dataclass(frozen=True)
class FunctionalRubric:
    points: int
    groups: tuple[RubricGroup, ...]


@dataclass(frozen=True)
class RobustnessRubric:
    points: int
    probes: tuple[str, ...]


@dataclass(frozen=True)
class CompatibilityRubric:
    points: int
    probes: tuple[str, ...]


@dataclass(frozen=True)
class MaintainabilityRubric:
    points: int


@dataclass(frozen=True)
class RuntimeEfficiencyRubric:
    points: int
    probes: tuple[str, ...]
    timeout_factor: float


@dataclass(frozen=True)
class PowerRubric:
    functional: FunctionalRubric
    robustness: RobustnessRubric
    compatibility: CompatibilityRubric
    maintainability: MaintainabilityRubric
    runtime_efficiency: RuntimeEfficiencyRubric


@dataclass(frozen=True)
class IssueCard:
    """card.yaml 的完整契約（spec §4.2）。"""

    schema_version: int
    issue_id: str
    archetype: str
    difficulty: str
    difficulty_scale: float
    expected_patch_loc: tuple[int, int]
    wall_clock_seconds: int
    max_turns: int
    allowed_paths: tuple[str, ...]
    expected_paths: tuple[str, ...]
    public_requirements: tuple[PublicRequirement, ...]
    critical_requirements: tuple[CriticalRequirement, ...]
    regression_probes: tuple[RegressionProbe, ...]
    compat_probes: tuple[CompatProbe, ...]
    power_rubric: PowerRubric
    reference_cost: Decimal | None = None
    #: 選填的一句話 bug 說明（白話戰報用；缺省 None 時退回第一條 public 需求）。
    briefing: str | None = None
