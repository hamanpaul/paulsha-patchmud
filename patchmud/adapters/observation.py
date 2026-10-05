"""Provider 確認的實際執行條件（execution profile observed plane 的輸入）。

adapter 只回報 provider **自己持久化或回應**的身分（例如 Codex app-server 讀回的
thread model／reasoningEffort），不得用送出的設定冒充觀測值。彙總規則：

- 一次 run 的每一次 provider 呼叫都必須取得身分；任一次取不到 → 該條件 unknown。
- 所有呼叫回報的值必須一致；不一致 → unknown（不挑其中一個）。
- evidence 只保存 digest 與回報值，不保存 provider payload、thread id 原文或本機路徑。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

__all__ = ["RuntimeObservation"]


@dataclass(frozen=True)
class RuntimeObservation:
    """一次 run 內所有 provider 呼叫的實際 model／effort 彙總。"""

    #: provenance reference（``namespace:body``），標示觀測來源協定。
    source: str
    #: 所有呼叫一致回報的 model id；無法確認時為 ``None``。
    model_id: str | None
    #: 所有呼叫一致回報的原生 effort；無法確認時為 ``None``。
    effort: object | None
    model_reason: str | None = None
    effort_reason: str | None = None
    #: 逐次呼叫的觀測摘要（不含 payload 原文）。
    evidence: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
