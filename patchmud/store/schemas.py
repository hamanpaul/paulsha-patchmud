"""store 落盤契約：schema versions 與 fail-closed 驗證（spec §3、§12.1）。

所有持久化 artifact 都帶 ``schema_version``；讀取時任何 schema 不符一律
raise ``StoreError``，不做靜默 migration。
"""

from __future__ import annotations

from typing import Any

RUN_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
BUNDLE_SCHEMA_VERSION = 1

REQUIRED_RUN_CONFIG_KEYS: tuple[str, ...] = (
    "run_id",
    "frozen_sha",
    "pricing_hash",
    "harness_prompt_version",
    "schedule_ref",
    "encounter_dir",
)


class StoreError(ValueError):
    """store 契約違反：schema 不符、append-only 破壞、封存輸入缺漏等。"""


def validate_run_config(config: Any) -> None:
    """run_config 必備欄位驗證（create 時 fail-closed）。

    ``schema_version`` 由 store 配發；caller 自帶外來版本會靜默覆寫版本戳，
    使 run.yaml 寫入當下零錯誤、事後永久不可讀不可封存，故比照
    ``validate_new_event`` 在寫入前拒絕。
    """
    if not isinstance(config, dict):
        raise StoreError("run_config 必須是 dict")
    if "schema_version" in config and config["schema_version"] != RUN_SCHEMA_VERSION:
        raise StoreError(
            f"run_config schema_version 不符：{config['schema_version']!r}"
        )
    for key in REQUIRED_RUN_CONFIG_KEYS:
        value = config.get(key)
        if not isinstance(value, str) or not value:
            raise StoreError(f"run_config 缺必填欄位或值非字串：{key}")


def validate_run_record(record: Any) -> None:
    """run.yaml 讀取驗證（open 時 fail-closed）。"""
    if not isinstance(record, dict):
        raise StoreError("run.yaml 內容必須是 mapping")
    if record.get("schema_version") != RUN_SCHEMA_VERSION:
        raise StoreError(
            f"run.yaml schema_version 不符：{record.get('schema_version')!r}"
        )
    validate_run_config(record)


def validate_new_result(result: Any) -> None:
    """write_result 輸入驗證：``schema_version`` 由 store 配發，外來版本拒收。

    result.yaml 一次寫入即不可覆寫；外來版本一旦落盤即毒化無法修復，
    並會被 archive 靜默打包成 replay fail-closed 必拒的封存。
    """
    if not isinstance(result, dict):
        raise StoreError("result 必須是 dict")
    if "schema_version" in result and result["schema_version"] != RESULT_SCHEMA_VERSION:
        raise StoreError(
            f"result schema_version 不符：{result['schema_version']!r}"
        )


def validate_new_event(event: Any) -> None:
    """append_event 輸入驗證：``seq`` 由 store 專屬配發，caller 不得自帶。"""
    if not isinstance(event, dict):
        raise StoreError("event 必須是 dict")
    if "seq" in event:
        raise StoreError("event 不得自帶 seq（由 store 配發）")
    if "schema_version" in event and event["schema_version"] != EVENT_SCHEMA_VERSION:
        raise StoreError(
            f"event schema_version 不符：{event['schema_version']!r}"
        )
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise StoreError("event 缺 type 欄位")


def validate_stored_event(event: Any, expected_seq: int) -> None:
    """events.jsonl 逐行讀取驗證：schema 與 seq 連續性 fail-closed。"""
    if not isinstance(event, dict):
        raise StoreError("event 行必須是 JSON object")
    if event.get("schema_version") != EVENT_SCHEMA_VERSION:
        raise StoreError(
            f"event schema_version 不符：{event.get('schema_version')!r}"
        )
    if event.get("seq") != expected_seq:
        raise StoreError(
            f"event seq 不連續：期望 {expected_seq}、實得 {event.get('seq')!r}"
        )
    event_type = event.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise StoreError("event 缺 type 欄位")
