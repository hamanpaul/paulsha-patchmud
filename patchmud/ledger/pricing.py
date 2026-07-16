"""版本化 pricing snapshot（spec §10.2、F16）。

snapshot 檔為 immutable YAML；``content_hash``（sha256 of raw bytes）供
run.yaml pin 定，價格改動不影響已封存 run。價格欄位必須是字串字面值 →
``decimal.Decimal`` 精確解析；YAML float 一律 fail-closed。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from patchmud.ledger.tokens import LedgerError

PRICING_SCHEMA_VERSION = 1

_PRICE_FIELDS = (
    "input_uncached_per_mtok",
    "input_cached_per_mtok",
    "output_per_mtok",
    "per_request",
    "per_tool_call",
    "minimum_charge_per_call",
)


@dataclass(frozen=True)
class PricingSnapshot:
    """單日、單 treatment 的計價快照（金額全 Decimal）。"""

    snapshot_date: str
    currency: str
    input_uncached_per_mtok: Decimal
    input_cached_per_mtok: Decimal
    output_per_mtok: Decimal
    per_request: Decimal
    per_tool_call: Decimal
    minimum_charge_per_call: Decimal
    content_hash: str

    @classmethod
    def load(cls, path: Path) -> "PricingSnapshot":
        raw = Path(path).read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise LedgerError(f"pricing snapshot 非法 YAML：{exc}") from exc
        if not isinstance(data, dict):
            raise LedgerError("pricing snapshot 內容必須是 mapping")
        if data.get("schema_version") != PRICING_SCHEMA_VERSION:
            raise LedgerError(
                f"pricing snapshot schema_version 不符：{data.get('schema_version')!r}"
            )
        snapshot_date = data.get("snapshot_date")
        currency = data.get("currency")
        if not isinstance(snapshot_date, str) or not snapshot_date:
            raise LedgerError("pricing snapshot 缺 snapshot_date")
        if not isinstance(currency, str) or not currency:
            raise LedgerError("pricing snapshot 缺 currency")
        prices = data.get("prices")
        if not isinstance(prices, dict):
            raise LedgerError("pricing snapshot 缺 prices mapping")
        parsed: dict[str, Decimal] = {}
        for field in _PRICE_FIELDS:
            value = prices.get(field)
            if not isinstance(value, str):
                raise LedgerError(
                    f"價格欄位必須是字串字面值（Decimal 精確解析）：{field}={value!r}"
                )
            try:
                amount = Decimal(value)
            except InvalidOperation as exc:
                raise LedgerError(f"價格欄位無法解析：{field}={value!r}") from exc
            if amount < 0:
                raise LedgerError(f"價格欄位不得為負：{field}={value!r}")
            parsed[field] = amount
        return cls(
            snapshot_date=snapshot_date,
            currency=currency,
            content_hash=content_hash,
            **parsed,
        )
