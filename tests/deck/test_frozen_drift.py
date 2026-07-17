"""F5 回歸：frozen deck drift 偵測（provenance content_sha256）。

codex gpt-5.6-sol finding F5：改動 repo/src、hidden 測資或 card 而未同步 pin
不被判 drift。修正後 provenance pin `content_sha256`，`_check_provenance_file`
重算比對，漂移一律 DeckError。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from patchmud.cli import _check_provenance_file, encounter_content_sha256
from patchmud.deck.model import DeckError

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"


def _pinned_encounter(tmp_path: Path) -> Path:
    enc = tmp_path / "enc"
    shutil.copytree(FIXTURE, enc)
    prov = enc / "provenance.yaml"
    data = yaml.safe_load(prov.read_text(encoding="utf-8")) or {}
    data.setdefault("schema_version", 1)
    data.setdefault("issue_id", "mini")
    data.setdefault("archetype_source", "test")
    data.setdefault("published_at", None)
    data.setdefault("variant_notes", "test")
    data.setdefault("frozen_at", "2026-07-17")
    data["content_sha256"] = encounter_content_sha256(enc)
    prov.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return enc


def test_pinned_encounter_passes(tmp_path: Path) -> None:
    enc = _pinned_encounter(tmp_path)
    _check_provenance_file(enc)  # 不 raise


def test_repo_src_drift_detected(tmp_path: Path) -> None:
    enc = _pinned_encounter(tmp_path)
    src = enc / "repo" / "src" / "inventory.py"
    src.write_text(src.read_text(encoding="utf-8") + "\n# silent edit\n", encoding="utf-8")
    with pytest.raises(DeckError):
        _check_provenance_file(enc)


def test_hidden_probe_drift_detected(tmp_path: Path) -> None:
    enc = _pinned_encounter(tmp_path)
    hidden = enc / "hidden" / "test_cr1_no_negative_stock.py"
    hidden.write_text(hidden.read_text(encoding="utf-8") + "\n# tamper\n", encoding="utf-8")
    with pytest.raises(DeckError):
        _check_provenance_file(enc)


def test_reference_timings_change_does_not_trip(tmp_path: Path) -> None:
    """validate-deck 會覆寫 reference_timings.yaml；不得因此誤判 drift。"""
    enc = _pinned_encounter(tmp_path)
    timings = enc / "hidden" / "reference_timings.yaml"
    timings.write_text("schema_version: 1\nprobes: {}\n", encoding="utf-8")
    _check_provenance_file(enc)  # 不 raise


def test_pilot_deck_pins_are_current() -> None:
    """已封存的 pilot-v1 provenance pin 必須與現況一致（防遷移漂移）。"""
    deck = Path(__file__).resolve().parents[2] / "decks" / "pilot-v1"
    for enc in sorted(deck.iterdir()):
        if not (enc / "card.yaml").is_file():
            continue
        data = yaml.safe_load((enc / "provenance.yaml").read_text(encoding="utf-8"))
        assert data.get("content_sha256") == encounter_content_sha256(enc), enc.name
