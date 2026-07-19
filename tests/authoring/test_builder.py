"""Part B Task B2：build_encounter 寫檔 + 品質閘（注入 fake validate seam）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from patchmud.authoring import AuthoringError, build_encounter, load_source
from patchmud.deck.loader import DeckError, load_card

from tests.authoring.test_loader import MINIMAL, _write


def _ok_validate(_encounter_dir: Path) -> None:
    """品質閘通過。"""


def _fail_validate(_encounter_dir: Path) -> None:
    raise DeckError("baseline MAIN probe 不是 failed（bug 未重現）")


def test_builds_valid_deck(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    dest = tmp_path / "deck"
    got = build_encounter(spec, dest, validate=_ok_validate, now="2026-07-19")

    assert got == dest / "demo-v1"
    # 產物過 deck 契約
    card = load_card(got / "card.yaml")
    assert card.issue_id == "demo-v1"
    assert card.briefing == "值裡含 = 會被切爛"
    assert card.public_requirements[0].probe == "tests/public/test_x.py"
    assert card.critical_requirements[0].hidden_probe == "hidden/test_cr1_x.py"
    # 檔案落地
    assert (got / "repo" / "src" / "x.py").is_file()
    assert (got / "repo" / "tests" / "public" / "test_x.py").is_file()
    assert (got / "repo" / "conftest.py").is_file()
    assert (got / "hidden" / "reference.patch").is_file()
    assert (got / "hidden" / "test_cr1_x.py").is_file()
    # provenance：sha + frozen_at + 來源
    prov = yaml.safe_load((got / "provenance.yaml").read_text(encoding="utf-8"))
    assert prov["frozen_at"] == "2026-07-19"
    assert prov["archetype_source"] == "closed bug: example.com/#1"
    assert len(prov["content_sha256"]) == 64


def test_regression_smoke_imports_expected_module(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    got = build_encounter(spec, tmp_path / "d", validate=_ok_validate, now="2026-07-19")
    card = load_card(got / "card.yaml")
    # expected_paths=[src/x.py] → smoke import x
    smoke = card.regression_probes[0].smoke
    assert smoke is not None and "import x" in smoke[-1]


def test_gate_failure_becomes_authoring_error(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    with pytest.raises(AuthoringError):
        build_encounter(spec, tmp_path / "d", validate=_fail_validate, now="2026-07-19")


def test_refuses_to_overwrite_existing(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    (tmp_path / "d" / "demo-v1").mkdir(parents=True)
    with pytest.raises(AuthoringError):
        build_encounter(spec, tmp_path / "d", validate=_ok_validate, now="2026-07-19")


def test_requirements_public_tests_count_mismatch(tmp_path):
    data = dict(MINIMAL)
    data["requirements"] = [
        {"id": "MAIN-1", "text": "a"},
        {"id": "MAIN-2", "text": "b"},
    ]
    # 只有一個 public test → 數量不符
    spec = load_source(_write(tmp_path, data))
    with pytest.raises(AuthoringError):
        build_encounter(spec, tmp_path / "d", validate=_ok_validate, now="2026-07-19")
