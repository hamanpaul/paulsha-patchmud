"""Harness prompt 模板與版本（spec §5.3–5.4）。

- ``HARNESS_PROMPT_VERSION`` 版本化 system prompt 與狀態 render 模板，
  **內含 render pack 版本**（spec §5.4）；Task 13 turn loop 寫入 run.yaml。
- prompt 敘事文字一律出自 render pack 查表（本模組不得硬編中文字串）；
  命令關鍵字與回覆格式（報告 §9.3）維持英文。
- 每 turn 訊息組裝（系統規則＋card 公開部分＋transcript＋狀態 render）
  是 turn loop 的事；本模組只提供 deterministic 模板函數。
"""

from __future__ import annotations

from patchmud.deck.model import IssueCard
from patchmud.engine import render_zh_tw as zh

__all__ = ["HARNESS_PROMPT_VERSION", "build_system_prompt"]

#: harness prompt 模板版本；模板或 render pack 任何改動都必須 bump。
_PROMPT_TEMPLATE_VERSION = "1.0.0"

#: 寫入 run.yaml 的完整版本戳：模板版本 + render pack 語言/版本（§5.3–5.4）。
HARNESS_PROMPT_VERSION = (
    f"hp-{_PROMPT_TEMPLATE_VERSION}"
    f"+render-{zh.RENDER_LANGUAGE}-{zh.RENDER_PACK_VERSION}"
)


def build_system_prompt(card: IssueCard) -> str:
    """組 system prompt：系統規則＋issue card 公開部分（deterministic）。"""
    lines = [
        zh.text("prompt.system_rules"),
        "",
        zh.text(
            "prompt.card_brief",
            issue_id=card.issue_id,
            archetype=card.archetype,
            difficulty=card.difficulty,
            max_turns=card.max_turns,
            wall_clock_seconds=card.wall_clock_seconds,
            allowed_paths=", ".join(card.allowed_paths),
            expected_paths=", ".join(card.expected_paths),
        ),
    ]
    lines.extend(
        zh.text("prompt.card_requirement", req_id=req.id, text=req.text)
        for req in card.public_requirements
    )
    return "\n".join(lines)
