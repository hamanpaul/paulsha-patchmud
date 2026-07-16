"""card.yaml → IssueCard 載入與 schema 驗證（fail-closed）。"""

from __future__ import annotations

import fnmatch
from decimal import Decimal
from pathlib import Path

import yaml

from patchmud.deck.model import (
    CARD_SCHEMA_VERSION,
    CompatibilityRubric,
    CompatProbe,
    CriticalRequirement,
    DeckError,
    FunctionalRubric,
    IssueCard,
    MaintainabilityRubric,
    PowerRubric,
    PublicRequirement,
    RegressionProbe,
    RobustnessRubric,
    RubricGroup,
    RuntimeEfficiencyRubric,
)

__all__ = ["DeckError", "load_card"]

_REQUIRED_FIELDS = (
    "schema_version",
    "issue_id",
    "archetype",
    "difficulty",
    "difficulty_scale",
    "expected_patch_loc",
    "wall_clock_seconds",
    "max_turns",
    "allowed_paths",
    "expected_paths",
    "public_requirements",
    "critical_requirements",
    "regression_probes",
    "compat_probes",
    "power_rubric",
)

_REQUIRED_RUBRIC_SECTIONS = (
    "functional",
    "robustness",
    "compatibility",
    "maintainability",
    "runtime_efficiency",
)

_HIDDEN_PREFIX = "hidden/"


def load_card(path: Path) -> IssueCard:
    """讀取 card.yaml，schema 錯誤一律 raise DeckError（fail-closed）。"""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DeckError(f"card.yaml 解析失敗：{exc}") from exc
    if not isinstance(data, dict):
        raise DeckError("card.yaml 頂層必須是 mapping")

    missing = [key for key in _REQUIRED_FIELDS if key not in data]
    if missing:
        raise DeckError(f"card.yaml 缺必填欄位：{', '.join(missing)}")

    if data["schema_version"] != CARD_SCHEMA_VERSION:
        raise DeckError(
            f"schema_version 不支援：{data['schema_version']}"
            f"（僅支援 {CARD_SCHEMA_VERSION}）"
        )

    try:
        card = _build_card(data)
    except DeckError:
        raise
    except (TypeError, KeyError, AttributeError, ValueError) as exc:
        raise DeckError(f"card.yaml 欄位形態錯誤：{exc}") from exc

    _validate_expected_within_allowed(card)
    _validate_public_hidden_disjoint(card)
    return card


def _build_card(data: dict) -> IssueCard:
    loc = data["expected_patch_loc"]
    if not (isinstance(loc, list) and len(loc) == 2):
        raise DeckError(f"expected_patch_loc 必須是 [min, max]：{loc!r}")

    reference_cost = data.get("reference_cost")
    if reference_cost is not None:
        reference_cost = Decimal(str(reference_cost))

    return IssueCard(
        schema_version=int(data["schema_version"]),
        issue_id=str(data["issue_id"]),
        archetype=str(data["archetype"]),
        difficulty=str(data["difficulty"]),
        difficulty_scale=float(data["difficulty_scale"]),
        expected_patch_loc=(int(loc[0]), int(loc[1])),
        wall_clock_seconds=int(data["wall_clock_seconds"]),
        max_turns=int(data["max_turns"]),
        allowed_paths=_str_tuple(data["allowed_paths"], "allowed_paths"),
        expected_paths=_str_tuple(data["expected_paths"], "expected_paths"),
        public_requirements=tuple(
            PublicRequirement(
                id=str(item["id"]), text=str(item["text"]), probe=str(item["probe"])
            )
            for item in _dict_list(data["public_requirements"], "public_requirements")
        ),
        critical_requirements=tuple(
            CriticalRequirement(
                id=str(item["id"]), hidden_probe=str(item["hidden_probe"])
            )
            for item in _dict_list(
                data["critical_requirements"], "critical_requirements"
            )
        ),
        regression_probes=tuple(
            _regression_probe(item) for item in _list(data["regression_probes"], "regression_probes")
        ),
        compat_probes=tuple(
            CompatProbe(probe=str(item["probe"]))
            for item in _dict_list(data["compat_probes"], "compat_probes")
        ),
        power_rubric=_power_rubric(data["power_rubric"]),
        reference_cost=reference_cost,
    )


def _power_rubric(data: object) -> PowerRubric:
    if not isinstance(data, dict):
        raise DeckError("power_rubric 必須是 mapping")
    missing = [key for key in _REQUIRED_RUBRIC_SECTIONS if key not in data]
    if missing:
        raise DeckError(f"power_rubric 缺分項：{', '.join(missing)}")

    functional = data["functional"]
    return PowerRubric(
        functional=FunctionalRubric(
            points=int(functional["points"]),
            groups=tuple(
                RubricGroup(
                    id=str(g["id"]), points=int(g["points"]), probe=str(g["probe"])
                )
                for g in _dict_list(functional["groups"], "power_rubric.functional.groups")
            ),
        ),
        robustness=RobustnessRubric(
            points=int(data["robustness"]["points"]),
            probes=_str_tuple(
                data["robustness"]["probes"], "power_rubric.robustness.probes"
            ),
        ),
        compatibility=CompatibilityRubric(
            points=int(data["compatibility"]["points"]),
            probes=_str_tuple(
                data["compatibility"]["probes"], "power_rubric.compatibility.probes"
            ),
        ),
        maintainability=MaintainabilityRubric(
            points=int(data["maintainability"]["points"])
        ),
        runtime_efficiency=RuntimeEfficiencyRubric(
            points=int(data["runtime_efficiency"]["points"]),
            probes=_str_tuple(
                data["runtime_efficiency"]["probes"],
                "power_rubric.runtime_efficiency.probes",
            ),
            timeout_factor=float(data["runtime_efficiency"]["timeout_factor"]),
        ),
    )


def _regression_probe(item: object) -> RegressionProbe:
    if isinstance(item, str):
        return RegressionProbe(path=item)
    if isinstance(item, dict) and set(item) == {"smoke"}:
        return RegressionProbe(smoke=_str_tuple(item["smoke"], "regression smoke"))
    raise DeckError(f"regression_probes 項目必須是路徑或 {{smoke: [...]}}：{item!r}")


def _validate_expected_within_allowed(card: IssueCard) -> None:
    for expected in card.expected_paths:
        covered = any(
            expected == pattern or fnmatch.fnmatch(expected, pattern)
            for pattern in card.allowed_paths
        )
        if not covered:
            raise DeckError(f"expected_paths 超出 allowed_paths：{expected}")


def _validate_public_hidden_disjoint(card: IssueCard) -> None:
    public_paths = {req.probe for req in card.public_requirements}
    public_paths |= {probe.probe for probe in card.compat_probes}
    public_paths |= {
        probe.path for probe in card.regression_probes if probe.path is not None
    }

    for path in sorted(public_paths):
        if path.startswith(_HIDDEN_PREFIX):
            raise DeckError(f"public/hidden 路徑重疊：public probe 落在 hidden/：{path}")

    hidden_paths = {req.hidden_probe for req in card.critical_requirements}
    for path in sorted(hidden_paths):
        if not path.startswith(_HIDDEN_PREFIX):
            raise DeckError(
                f"public/hidden 路徑重疊：hidden probe 不在 hidden/：{path}"
            )

    rubric = card.power_rubric
    hidden_paths |= {
        probe
        for probe in (
            tuple(g.probe for g in rubric.functional.groups)
            + rubric.robustness.probes
            + rubric.compatibility.probes
            + rubric.runtime_efficiency.probes
        )
        if probe.startswith(_HIDDEN_PREFIX)
    }

    overlap = public_paths & hidden_paths
    if overlap:
        raise DeckError(f"public/hidden 路徑重疊：{', '.join(sorted(overlap))}")


def _list(value: object, field: str) -> list:
    if not isinstance(value, list):
        raise DeckError(f"{field} 必須是 list：{value!r}")
    return value


def _dict_list(value: object, field: str) -> list[dict]:
    items = _list(value, field)
    for item in items:
        if not isinstance(item, dict):
            raise DeckError(f"{field} 項目必須是 mapping：{item!r}")
    return items


def _str_tuple(value: object, field: str) -> tuple[str, ...]:
    return tuple(str(item) for item in _list(value, field))
