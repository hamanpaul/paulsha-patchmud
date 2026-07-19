"""SourceSpec → 凍結 deck 關卡：寫檔 + 品質閘（bug 為真、fix 為真）。

品質閘經注入的 ``validate`` seam 執行——預設佈上真 ``validate_deck``（走
IsolationRunner，比照 pilot 題的同一套 CI）；測試注入 fake。讓出的題與 pilot
題滿足完全相同的不變式：baseline 未套 patch 時 MAIN probe 必須 failed（bug
可重現）、regression 綠；套 reference.patch 後 public 與 hidden 全綠。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

from patchmud.authoring.model import AuthoringError, SourceSpec
from patchmud.deck.loader import DeckError, load_card

__all__ = ["build_encounter"]

_CONFTEST = (
    "import sys\n"
    "from pathlib import Path\n\n"
    'sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))\n'
)


def build_encounter(
    spec: SourceSpec,
    dest_dir: Path,
    *,
    validate: Callable[[Path], None],
    now: str,
) -> Path:
    """在 ``dest_dir/<issue_id>`` 寫出 deck 並經 ``validate`` 品質閘後回傳其路徑。

    ``validate(encounter_dir)`` 於品質閘失敗時 raise（呼叫端捕捉為 AuthoringError）。
    """
    if len(spec.requirements) != len(spec.public_tests):
        raise AuthoringError(
            "requirements 與 public_tests 數量必須一致"
            f"（{len(spec.requirements)} vs {len(spec.public_tests)}）"
        )

    encounter_dir = Path(dest_dir) / spec.issue_id
    if encounter_dir.exists():
        raise AuthoringError(f"目標關卡目錄已存在，拒絕覆寫：{encounter_dir}")

    _write_repo(spec, encounter_dir)
    _write_hidden(spec, encounter_dir)
    card = _write_card(spec, encounter_dir)
    _write_provenance(spec, encounter_dir, now)

    try:
        validate(encounter_dir)
    except (AuthoringError, DeckError) as exc:
        raise AuthoringError(f"品質閘未過：{exc}") from exc
    except Exception as exc:  # noqa: BLE001 — 任何 seam 失敗一律 fail-closed
        raise AuthoringError(f"品質閘執行失敗：{exc}") from exc

    # card 已用於合成，回傳前再自驗一次結構（保險）。
    load_card(encounter_dir / "card.yaml")
    _ = card
    return encounter_dir


def _write_repo(spec: SourceSpec, encounter_dir: Path) -> None:
    repo = encounter_dir / "repo"
    files = dict(spec.buggy_repo)
    files.update(dict(spec.public_tests))
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    conftest = repo / "conftest.py"
    if not conftest.exists():
        conftest.write_text(_CONFTEST, encoding="utf-8")


def _write_hidden(spec: SourceSpec, encounter_dir: Path) -> None:
    hidden = encounter_dir / "hidden"
    hidden.mkdir(parents=True, exist_ok=True)
    (hidden / "reference.patch").write_text(spec.reference_patch, encoding="utf-8")
    for name, content in spec.hidden_tests:
        (hidden / name).write_text(content, encoding="utf-8")


def _module_name(spec: SourceSpec) -> str:
    """由 expected_paths[0] 推 smoke import 的模組名（src/<mod>.py → <mod>）。"""
    first = spec.expected_paths[0] if spec.expected_paths else "src/__init__.py"
    rel = first[len("src/") :] if first.startswith("src/") else first
    if rel.endswith(".py"):
        rel = rel[: -len(".py")]
    return rel.replace("/", ".")


def _write_card(spec: SourceSpec, encounter_dir: Path) -> object:
    hidden_probe = f"hidden/{spec.hidden_tests[0][0]}"
    all_hidden = [f"hidden/{name}" for name, _ in spec.hidden_tests]
    public_reqs = [
        {"id": rid, "text": text, "probe": probe}
        for (rid, text), (probe, _content) in zip(spec.requirements, spec.public_tests)
    ]
    smoke = [
        "python3",
        "-B",
        "-c",
        f"import sys; sys.path.insert(0, 'src'); import {_module_name(spec)}",
    ]
    card_data = {
        "schema_version": 1,
        "issue_id": spec.issue_id,
        "briefing": spec.summary,
        "archetype": spec.archetype,
        "difficulty": spec.difficulty,
        "difficulty_scale": 1.0,
        "expected_patch_loc": [1, 200],
        "wall_clock_seconds": 300,
        "max_turns": 6,
        "allowed_paths": list(spec.allowed_paths),
        "expected_paths": list(spec.expected_paths),
        "public_requirements": public_reqs,
        "critical_requirements": [{"id": "CR-1", "hidden_probe": hidden_probe}],
        "regression_probes": [{"smoke": smoke}],
        "compat_probes": [],
        "power_rubric": {
            "functional": {
                "points": spec.rubric_functional,
                "groups": [
                    {"id": "CR-1", "points": spec.rubric_functional, "probe": hidden_probe}
                ],
            },
            "robustness": {"points": 15, "probes": all_hidden},
            "compatibility": {"points": 10, "probes": []},
            "maintainability": {"points": 10},
            "runtime_efficiency": {
                "points": 5,
                "probes": [hidden_probe],
                "timeout_factor": 3.0,
            },
        },
    }
    path = encounter_dir / "card.yaml"
    path.write_text(
        yaml.safe_dump(card_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return load_card(path)  # 結構自驗（fail-closed）


def _write_provenance(spec: SourceSpec, encounter_dir: Path, now: str) -> None:
    from patchmud.cli import encounter_content_sha256

    data = {
        "schema_version": 1,
        "issue_id": spec.issue_id,
        "archetype_source": spec.origin_source,
        "published_at": None,
        "variant_notes": spec.summary,
        "frozen_at": now,
        "content_sha256": encounter_content_sha256(encounter_dir),
    }
    (encounter_dir / "provenance.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
