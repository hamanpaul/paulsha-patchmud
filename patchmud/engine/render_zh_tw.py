"""zh-TW render pack：所有 agent-facing 敘事文案的唯一來源（spec §5.4）。

- 敘事文字一律中文；命令關鍵字（``LOOK`` / ``PATCH`` / …）、issue item ID、
  flood 狀態 label 與 artifact 格式維持英文——那是結構化協定。
- 文案表版本化：``RENDER_PACK_VERSION`` 進 ``harness_prompt_version``（spec
  §5.3–5.4）；``RENDER_LANGUAGE`` 是 treatment 欄位 ``render_language`` 的值。
  **任何文案改動都必須 bump 版本**（golden 測試鎖定）。
- 其他模組（render / protocol / prompts / 之後的 loop、watch）只能經
  :func:`text` 查表取文案，不得硬編中文字串；查無 key 或缺 format 參數
  一律 fail-closed :class:`RenderError`。
"""

from __future__ import annotations

__all__ = [
    "MESSAGES",
    "RENDER_LANGUAGE",
    "RENDER_PACK_VERSION",
    "RenderError",
    "text",
]

#: treatment 欄位 ``render_language`` 的值（spec §5.4；MVP 只出 zh-TW）。
RENDER_LANGUAGE = "zh-TW"

#: 文案表版本；文案任何改動必須 bump（golden 測試與 harness_prompt_version 鎖定）。
RENDER_PACK_VERSION = "1.0.0"


class RenderError(Exception):
    """render pack 查表失敗：未知 key 或缺 format 參數（fail-closed）。"""


#: 合法命令一覽（parse 錯誤提示與 system prompt 共用；關鍵字英文）。
_COMMAND_MENU = (
    "LOOK / INSPECT <path> / PLAY PLAN / WRITE_TEST / PATCH / "
    "RUN_TEST [target] / SUMMON REVIEWER / TRIAGE / ROLLBACK / COMMIT"
)

MESSAGES: dict[str, str] = {
    # ---- 通用 ------------------------------------------------------------
    "na": "NA",
    # ---- parse 錯誤提示（spec §5.2：結構化 parse error 附格式提示） ------
    "parse.missing_action": (
        "無法解析回覆：找不到「ACTION: <命令>」行。請以 ACTION: 開頭宣告本回合"
        "唯一動作，合法命令：" + _COMMAND_MENU + "。"
    ),
    "parse.unknown_action": (
        "無法解析回覆：未知動作「{action}」。合法命令：" + _COMMAND_MENU + "。"
    ),
    "parse.missing_diff": (
        "無法解析回覆：{action} 必須以「PATCH:」區塊附上非空的 unified diff"
        "（PATCH: 行之後即為 diff 內容）。"
    ),
    "parse.missing_inspect_path": (
        "無法解析回覆：INSPECT 需要指定 sandbox 內的檔案路徑，"
        "例如「ACTION: INSPECT src/example.py」。"
    ),
    # ---- 狀態 render（spec §5.3–5.4；報告 §9.4） -------------------------
    "state.turn_header": "=== 回合 {turn}／{max_turns} ===",
    "state.battlefield": "【戰場】encounter {encounter_id}",
    "state.queue_header": "【議題佇列】開放 {count} 項",
    "state.queue_item": "- {item_id} [{type}] {summary}",
    "state.queue_empty": "- （目前沒有開放議題，戰場乾淨）",
    "state.resources": (
        "【資源】剩餘回合 {turns_left}｜剩餘時間 {seconds_left} 秒｜"
        "已用 tokens {tokens_spent}"
    ),
    "state.flood": "【洪水壓力】backlog {backlog} → {label}：{desc}",
    # ---- flood 狀態文案（報告 §7.5；只影響 render，不進任何分數） --------
    "flood.stable.label": "Stable",
    "flood.stable.desc": "戰場可控",
    "flood.noisy.label": "Noisy",
    "flood.noisy.desc": "context 與診斷負擔上升",
    "flood.flooded.label": "Flooded",
    "flood.flooded.desc": "queue 顯著擴張，需優先 triage",
    "flood.meltdown.label": "Meltdown",
    "flood.meltdown.desc": "幾乎無法在剩餘時間收斂",
    # ---- system prompt（spec §5.3；報告 §9.3 回覆格式） -------------------
    "prompt.system_rules": (
        "你是 PatchMUD 回合制修補對局中的作者 agent。每回合回覆恰好一個動作，"
        "命令關鍵字與 artifact 格式用英文、敘述用中文。回覆格式：\n"
        "ACTION: <命令>（合法命令：" + _COMMAND_MENU + "）\n"
        "TARGET_ISSUES: <逗號分隔的 issue ID，選填>\n"
        "FILES: <逗號分隔的檔案路徑，選填>\n"
        "CLAIM: <一句話說明本回合意圖，選填>\n"
        "PATCH:\n"
        "<unified diff；PATCH 與 WRITE_TEST 必附非空 diff>\n"
        "PLAY PLAN 則以「PLAN:」區塊附上 plan YAML。\n"
        "規則：每次回覆恰好消耗一個回合，不論動作合法與否；無法解析的回覆"
        "也消耗回合，連續 3 次無效即以 failed:protocol 終局。PATCH 採 git apply"
        " 嚴格模式；WRITE_TEST 只能新增或修改 tests/agent/** 下的測試。"
        "COMMIT 送出終局 artifact，之後執行隱藏評分。"
    ),
    "prompt.card_brief": (
        "【任務卡】{issue_id}（archetype {archetype}／難度 {difficulty}）\n"
        "回合上限 {max_turns}、時限 {wall_clock_seconds} 秒。\n"
        "允許修改路徑：{allowed_paths}\n"
        "預期修改路徑：{expected_paths}\n"
        "公開需求（MAIN）："
    ),
    "prompt.card_requirement": "- {req_id}：{text}",
}


def text(key: str, **kwargs: object) -> str:
    """查表取文案並代入參數；未知 key / 缺參數 fail-closed。"""
    try:
        template = MESSAGES[key]
    except KeyError as exc:
        raise RenderError(f"render pack 查無文案 key：{key!r}") from exc
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError) as exc:
        raise RenderError(f"文案 {key!r} 缺 format 參數：{exc}") from exc
