"""Task 1 RED：IssueCard 契約（spec §4.2）。

鎖定：缺必填欄位 / expected_paths ⊄ allowed_paths / public、hidden 路徑重疊
→ DeckError；合法 card 全欄位 round-trip；frozen dataclass 不可變。
"""

import dataclasses
from pathlib import Path

import pytest
import yaml

from patchmud.deck.loader import DeckError, load_card

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"


def _card_dict() -> dict:
    return yaml.safe_load((FIXTURE / "card.yaml").read_text(encoding="utf-8"))


def _write_card(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "card.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "missing",
    [
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
    ],
)
def test_missing_required_field_raises_deck_error(tmp_path, missing):
    data = _card_dict()
    del data[missing]
    with pytest.raises(DeckError):
        load_card(_write_card(tmp_path, data))


def test_expected_paths_outside_allowed_raises_deck_error(tmp_path):
    data = _card_dict()
    data["expected_paths"] = ["docs/README.md"]
    with pytest.raises(DeckError):
        load_card(_write_card(tmp_path, data))


def test_public_probe_under_hidden_raises_deck_error(tmp_path):
    data = _card_dict()
    data["public_requirements"][0]["probe"] = "hidden/test_cr1_no_negative_stock.py"
    with pytest.raises(DeckError):
        load_card(_write_card(tmp_path, data))


def test_hidden_probe_outside_hidden_raises_deck_error(tmp_path):
    data = _card_dict()
    data["critical_requirements"][0]["hidden_probe"] = "tests/public/test_remove_missing.py"
    with pytest.raises(DeckError):
        load_card(_write_card(tmp_path, data))


def test_unknown_schema_version_fail_closed(tmp_path):
    data = _card_dict()
    data["schema_version"] = 99
    with pytest.raises(DeckError):
        load_card(_write_card(tmp_path, data))


def test_valid_card_full_roundtrip():
    data = _card_dict()
    card = load_card(FIXTURE / "card.yaml")

    assert dataclasses.is_dataclass(card)
    assert card.__dataclass_params__.frozen

    assert card.schema_version == data["schema_version"]
    assert card.issue_id == data["issue_id"]
    assert card.archetype == data["archetype"]
    assert card.difficulty == data["difficulty"]
    assert card.difficulty_scale == pytest.approx(data["difficulty_scale"])
    assert list(card.expected_patch_loc) == data["expected_patch_loc"]
    assert card.wall_clock_seconds == data["wall_clock_seconds"]
    assert card.max_turns == data["max_turns"]
    assert list(card.allowed_paths) == data["allowed_paths"]
    assert list(card.expected_paths) == data["expected_paths"]

    assert [r.id for r in card.public_requirements] == ["MAIN-1"]
    assert card.public_requirements[0].text == data["public_requirements"][0]["text"]
    assert card.public_requirements[0].probe == data["public_requirements"][0]["probe"]

    assert [c.id for c in card.critical_requirements] == ["CR-1"]
    assert (
        card.critical_requirements[0].hidden_probe
        == data["critical_requirements"][0]["hidden_probe"]
    )

    # regression probes：path 與 smoke 兩種形態都要保留
    assert card.regression_probes[0].path == "tests/starter/"
    assert card.regression_probes[0].smoke is None
    assert card.regression_probes[1].path is None
    assert list(card.regression_probes[1].smoke) == [
        "python3",
        "-B",
        "-c",
        "import sys; sys.path.insert(0, 'src'); import inventory",
    ]

    assert [c.probe for c in card.compat_probes] == [
        c["probe"] for c in data["compat_probes"]
    ]

    rubric = card.power_rubric
    assert rubric.functional.points == 60
    assert [(g.id, g.points, g.probe) for g in rubric.functional.groups] == [
        ("CR-1", 60, "hidden/test_cr1_no_negative_stock.py")
    ]
    assert rubric.robustness.points == 15
    assert list(rubric.robustness.probes) == ["hidden/test_cr1_no_negative_stock.py"]
    assert rubric.compatibility.points == 10
    assert list(rubric.compatibility.probes) == ["tests/starter/test_inventory_basics.py"]
    assert rubric.maintainability.points == 10
    assert rubric.runtime_efficiency.points == 5
    assert list(rubric.runtime_efficiency.probes) == [
        "hidden/test_cr1_no_negative_stock.py"
    ]
    assert rubric.runtime_efficiency.timeout_factor == pytest.approx(3.0)

    assert card.reference_cost is None


def test_frozen_card_rejects_mutation():
    card = load_card(FIXTURE / "card.yaml")
    with pytest.raises(dataclasses.FrozenInstanceError):
        card.issue_id = "mutated"
