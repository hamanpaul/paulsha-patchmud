"""Cortex persona 契約的 vendored 副本與 ``render_contract_prompt`` 移植。

Cortex execution profile 的 ``loadout`` 條件是 ``{id: <persona>, version: "1"}``——
派工時 job 所扮演的 persona。PatchMUD 只有在 prompt 前言**逐字**是 Cortex 對該
persona 的契約時，才宣告這個 loadout：

- ``data/cortex-personas.yaml`` 是 Cortex ``paulsha_cortex/persona/personas.yaml`` 的
  逐位元組副本，``*.provenance.json`` 釘住來源 revision 與 SHA-256；載入時不符即
  fail-closed（同 ``patchmud.adapters.cortex_catalog`` 的作法）。
- ``render_contract_prompt`` 逐字移植 Cortex ``persona.render.render_contract_prompt``
  （無 operator overlay：overlay 是部署端設定，不是契約）；CI 以真 Cortex 實作比對
  輸出完全相同。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = [
    "PERSONAS_FILENAME",
    "PERSONAS_PROVENANCE_FILENAME",
    "CortexPersonaContract",
    "CortexPersonaError",
    "load_persona_contract",
    "load_personas_provenance",
    "render_contract_prompt",
]

PERSONAS_FILENAME = "cortex-personas.yaml"
PERSONAS_PROVENANCE_FILENAME = "cortex-personas.provenance.json"
_DATA_DIR = Path(__file__).resolve().parent / "data"
_PROVENANCE_KEYS = frozenset(
    {
        "schema_version",
        "purpose",
        "vendored_file",
        "source_repo",
        "source_path",
        "source_revision",
        "source_last_changed_revision",
        "sha256",
        "consumed_roles",
    }
)


class CortexPersonaError(ValueError):
    """vendored persona 契約或其 provenance 不一致（fail-closed）。"""


@dataclass(frozen=True)
class CortexPersonaContract:
    role: str
    version: str
    allowed_phases: tuple[str, ...]
    write_paths: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    completion_obligations: tuple[str, ...]
    source_ref: str
    sha256: str


def load_personas_provenance(data_dir: Path | None = None) -> dict[str, object]:
    root = Path(data_dir) if data_dir is not None else _DATA_DIR
    try:
        provenance = json.loads(
            (root / PERSONAS_PROVENANCE_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise CortexPersonaError(f"Cortex persona provenance 無法讀取：{exc}") from exc
    if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_KEYS:
        raise CortexPersonaError("Cortex persona provenance 欄位不符")
    if provenance["schema_version"] != 1 or provenance["vendored_file"] != PERSONAS_FILENAME:
        raise CortexPersonaError("Cortex persona provenance 版本或檔名不符")
    try:
        data = (root / PERSONAS_FILENAME).read_bytes()
    except OSError as exc:
        raise CortexPersonaError(f"Cortex persona 契約無法讀取：{exc}") from exc
    if hashlib.sha256(data).hexdigest() != provenance["sha256"]:
        raise CortexPersonaError(
            "vendored Cortex persona 契約與 provenance SHA-256 不符；"
            "請從 Cortex 重新 vendor 並同步 provenance"
        )
    return provenance


def _strings(value: object, locator: str, *, allow_none: bool = False) -> tuple[str, ...]:
    if value is None and allow_none:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CortexPersonaError(f"Cortex persona {locator} 必須是字串清單")
    return tuple(value)


def load_persona_contract(role: str, *, data_dir: Path | None = None) -> CortexPersonaContract:
    root = Path(data_dir) if data_dir is not None else _DATA_DIR
    provenance = load_personas_provenance(root)
    if role not in provenance["consumed_roles"]:
        raise CortexPersonaError(f"vendored persona 契約未宣告消費 role：{role!r}")
    try:
        payload = yaml.safe_load((root / PERSONAS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CortexPersonaError(f"Cortex persona 契約無法解析：{exc}") from exc
    roles = payload.get("roles") if isinstance(payload, dict) else None
    record = roles.get(role) if isinstance(roles, dict) else None
    if not isinstance(record, dict):
        raise CortexPersonaError(f"Cortex persona 契約沒有 role：{role!r}")
    version = record.get("version")
    if record.get("role") != role or not isinstance(version, str) or not version:
        raise CortexPersonaError(f"Cortex persona {role} 的 role／version 不合法")
    return CortexPersonaContract(
        role=role,
        version=version,
        allowed_phases=_strings(record.get("allowed_phases"), f"{role}.allowed_phases"),
        write_paths=_strings(record.get("write_paths"), f"{role}.write_paths"),
        allowed_tools=_strings(record.get("allowed_tools"), f"{role}.allowed_tools"),
        completion_obligations=_strings(
            record.get("completion_obligations", []),
            f"{role}.completion_obligations",
            allow_none=True,
        ),
        source_ref=f"paulsha-cortex:{provenance['source_path']}@{provenance['source_revision']}",
        sha256=str(provenance["sha256"]),
    )


def render_contract_prompt(persona: CortexPersonaContract) -> str:
    """逐字移植 Cortex ``render_contract_prompt(role)``（無 overlay）。"""
    allowed_phases = ", ".join(persona.allowed_phases) or "(none)"
    write_paths = "\n".join(f"  - {p}" for p in persona.write_paths)
    effective_tools = "\n".join(f"  - {t}" for t in sorted(set(persona.allowed_tools)))

    completion_obligations_block = ""
    if persona.completion_obligations:
        obligations = "\n".join(f"  - {o}" for o in persona.completion_obligations)
        completion_obligations_block = (
            "- completion_obligations（結束前必須全部滿足，否則不得回報完成）：\n"
            f"{obligations}\n"
        )

    return (
        f"[PERSONA CONTRACT — role: {persona.role} (v{persona.version})]\n"
        "你在本次派工中扮演上述角色，且 MUST 嚴守以下契約邊界：\n"
        f"- allowed_phases: {allowed_phases}\n"
        "- write_paths（僅可寫入下列 glob，越界視為違規）:\n"
        f"{write_paths}\n"
        "- effective_tools（僅可使用下列工具）:\n"
        f"{effective_tools}\n"
        f"{completion_obligations_block}"
        "[END PERSONA CONTRACT]"
    )
