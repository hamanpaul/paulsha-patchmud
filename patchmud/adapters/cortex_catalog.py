"""Cortex execution adapter 身分的 vendored 唯讀副本（paulsha-cortex#842 Gap A）。

Cortex `qualification import` 以 ``adapter_for(executor).descriptor_fields()``
逐欄比對 producer binding 的 ``descriptor.adapter``。這份身分資料的 source of
truth 是 Cortex 的 ``paulsha_cortex/coordinator/data/execution-adapters.yaml``；
PatchMUD 維持對 Cortex 零 runtime 依賴，因此逐位元組 vendor 一份到
``data/cortex-execution-adapters.yaml``，旁邊的 ``*.provenance.json`` 釘住來源
repo／路徑／revision 與 SHA-256。

- 載入時先比對 SHA-256：檔案被改動卻沒同步 provenance 一律 fail-closed，避免
  descriptor 宣稱一個沒有被釘住的 Cortex 身分。
- ``descriptor_fields`` 的組法逐字對齊 Cortex ``ExecutionAdapter``：
  ``id = f"{executor}-cli"``，其餘三欄直接取 catalog 條目。
- 只讀資料，不讀 Cortex 的 operator overlay（overlay 是部署端設定，不是契約）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml

__all__ = [
    "CATALOG_FILENAME",
    "PROVENANCE_FILENAME",
    "CortexAdapterIdentity",
    "CortexCatalogError",
    "cortex_adapter_identity",
    "load_catalog_provenance",
]

CATALOG_FILENAME = "cortex-execution-adapters.yaml"
PROVENANCE_FILENAME = "cortex-execution-adapters.provenance.json"
_DATA_DIR = Path(__file__).resolve().parent / "data"
_DESCRIPTOR_KEYS = ("protocol_id", "protocol_version", "runtime_version")
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
        "consumed_executors",
    }
)


class CortexCatalogError(ValueError):
    """vendored Cortex adapter catalog 或其 provenance 不一致（fail-closed）。"""


@dataclass(frozen=True)
class CortexAdapterIdentity:
    """一個 Cortex executor 的 adapter 身分與原生 effort 值域。"""

    executor: str
    descriptor_fields: Mapping[str, str]
    efforts: tuple[str, ...]
    source_ref: str
    sha256: str
    #: catalog ``effort.default``（可為 ``None``）。
    default_effort: str | None = None
    #: catalog ``effort.model_defaults``：model → 原生 effort 預設。
    model_default_efforts: Mapping[str, str] = MappingProxyType({})

    def default_effort_for(self, model_id: str | None) -> str | None:
        """逐字對齊 Cortex ``ExecutionAdapter.default_effort_for()``：model 預設優先。"""
        if model_id is not None and model_id in self.model_default_efforts:
            return self.model_default_efforts[model_id]
        return self.default_effort


def load_catalog_provenance(data_dir: Path | None = None) -> dict[str, object]:
    """讀 provenance 並驗證 vendored catalog bytes 與其 SHA-256 一致。"""
    root = Path(data_dir) if data_dir is not None else _DATA_DIR
    try:
        provenance = json.loads((root / PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CortexCatalogError(f"Cortex adapter catalog provenance 無法讀取：{exc}") from exc
    if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_KEYS:
        raise CortexCatalogError("Cortex adapter catalog provenance 欄位不符")
    if provenance["schema_version"] != 1 or provenance["vendored_file"] != CATALOG_FILENAME:
        raise CortexCatalogError("Cortex adapter catalog provenance 版本或檔名不符")
    try:
        data = (root / CATALOG_FILENAME).read_bytes()
    except OSError as exc:
        raise CortexCatalogError(f"Cortex adapter catalog 無法讀取：{exc}") from exc
    if hashlib.sha256(data).hexdigest() != provenance["sha256"]:
        raise CortexCatalogError(
            "vendored Cortex adapter catalog 與 provenance SHA-256 不符；"
            "請從 Cortex 重新 vendor 並同步 provenance"
        )
    return provenance


def _load_catalog(data_dir: Path | None = None) -> tuple[dict[str, object], dict[str, object]]:
    root = Path(data_dir) if data_dir is not None else _DATA_DIR
    provenance = load_catalog_provenance(root)
    try:
        payload = yaml.safe_load((root / CATALOG_FILENAME).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CortexCatalogError(f"Cortex adapter catalog 無法解析：{exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise CortexCatalogError("Cortex adapter catalog schema_version 不支援")
    adapters = payload.get("adapters")
    if not isinstance(adapters, dict) or not adapters:
        raise CortexCatalogError("Cortex adapter catalog 缺 adapters")
    return adapters, provenance


def _text(value: object, locator: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CortexCatalogError(f"Cortex adapter catalog {locator} 必須是非空字串")
    return value


def cortex_adapter_identity(
    executor: str, *, data_dir: Path | None = None
) -> CortexAdapterIdentity:
    """回傳 ``executor`` 在 Cortex catalog 的 adapter 身分（逐欄同 ``descriptor_fields()``）。"""
    adapters, provenance = _load_catalog(data_dir)
    entry = adapters.get(executor)
    if not isinstance(entry, dict):
        raise CortexCatalogError(f"Cortex adapter catalog 沒有 executor：{executor!r}")
    fields = {"id": f"{executor}-cli"}
    for key in _DESCRIPTOR_KEYS:
        fields[key] = _text(entry.get(key), f"adapters.{executor}.{key}")
    effort = entry.get("effort")
    default_effort: str | None = None
    model_defaults: dict[str, str] = {}
    if effort is None:
        efforts: tuple[str, ...] = ()
    else:
        values = effort.get("values") if isinstance(effort, dict) else None
        if not isinstance(values, list) or not values:
            raise CortexCatalogError(f"Cortex adapter catalog adapters.{executor}.effort.values 不合法")
        efforts = tuple(
            _text(value, f"adapters.{executor}.effort.values[{index}]")
            for index, value in enumerate(values)
        )
        if len(set(efforts)) != len(efforts):
            raise CortexCatalogError(f"Cortex adapter catalog adapters.{executor}.effort 重複")
        raw_default = effort.get("default")
        if raw_default is not None:
            default_effort = _text(raw_default, f"adapters.{executor}.effort.default")
            if default_effort not in efforts:
                raise CortexCatalogError(
                    f"Cortex adapter catalog adapters.{executor}.effort.default 不在值域內"
                )
        raw_model_defaults = effort.get("model_defaults") or {}
        if not isinstance(raw_model_defaults, dict):
            raise CortexCatalogError(
                f"Cortex adapter catalog adapters.{executor}.effort.model_defaults 不合法"
            )
        for model, value in raw_model_defaults.items():
            model_name = _text(model, f"adapters.{executor}.effort.model_defaults key")
            model_effort = _text(
                value, f"adapters.{executor}.effort.model_defaults[{model_name!r}]"
            )
            if model_effort not in efforts:
                raise CortexCatalogError(
                    f"Cortex adapter catalog adapters.{executor}.effort.model_defaults"
                    f"[{model_name!r}] 不在值域內"
                )
            model_defaults[model_name] = model_effort
    source_ref = (
        f"paulsha-cortex:{provenance['source_path']}@{provenance['source_revision']}"
    )
    return CortexAdapterIdentity(
        executor=executor,
        descriptor_fields=MappingProxyType(fields),
        efforts=efforts,
        source_ref=source_ref,
        sha256=str(provenance["sha256"]),
        default_effort=default_effort,
        model_default_efforts=MappingProxyType(model_defaults),
    )
