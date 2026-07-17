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
#: 1.1.0：新增 strategy enforcer verdict 與 plan schema 錯誤文案（Task 12）。
#: 1.2.0：新增 turn loop 執行結果與 reviewer subcall 文案（Task 13）。
#: 1.3.0：新增 `patchmud play` 人類對局文案（Task 22；既有文案不變）。
RENDER_PACK_VERSION = "1.3.0"


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
    # ---- strategy enforcer verdict（spec §6；illegal 動作的敘事理由） ------
    "strategy.plan_forbidden": (
        "本場 loadout 未啟用 PLAN（P0）：PLAY PLAN 不可用。"
    ),
    "strategy.plan_duplicate": (
        "計畫已凍結：PLAY PLAN 只能提交一次，通過後的修改一律不合法。"
    ),
    "strategy.plan_after_patch": (
        "已有 production PATCH 套用：PLAY PLAN 只能在第一次 PATCH 前提交。"
    ),
    "strategy.plan_required_before_patch": (
        "P1 規則：第一次 PATCH 前必須先以 PLAY PLAN 提交通過 schema 驗證的計畫。"
    ),
    "strategy.tdd_red_required": (
        "T1 規則：production PATCH 前必須先達成 valid red——tests/agent/** 內"
        "新增測試以 pytest failed（assertion 失敗）收場；error（收集／import "
        "失敗）不算。"
    ),
    "strategy.reviewer_forbidden": (
        "本場 loadout 未啟用 REVIEWER（R0）：SUMMON REVIEWER 不可用。"
    ),
    "strategy.review_required_before_commit": (
        "R1 規則：COMMIT 前必須有一次合格 review——schema 有效、輸入 diff 非空"
        "（至少一個 production patch 已套用）、且 findings 已 render 給作者。"
    ),
    # ---- plan schema 錯誤（spec §6.3；PLAY PLAN illegal 的敘事理由） ------
    "plan.invalid_yaml": "PLAN 區塊不是有效的 YAML：{detail}。",
    "plan.not_mapping": "PLAN 區塊必須是 YAML mapping（key: value 結構）。",
    "plan.field_missing": "plan 缺欄位「{field}」。",
    "plan.field_empty": "plan 欄位「{field}」必須是非空字串列表。",
    "plan.requirements_missing_ids": (
        "plan 的 requirements 未涵蓋全部公開需求：缺 {ids}。"
    ),
    "plan.unknown_inspect_file": (
        "plan 的 files_to_inspect 引用不存在於 frozen repo 的檔案：{path}。"
    ),
    "plan.bad_test_target": (
        "plan 的 test_targets 必須是 tests/ 下的 repo-relative 路徑"
        "（tests/agent/** 允許尚不存在，其餘必須存在）：{path}。"
    ),
    # ---- turn loop 執行結果（spec §5.1–5.2；Task 13） ----------------------
    "loop.illegal": "【裁定】動作不合法：{reason}",
    "loop.patch_applied": "PATCH 已套用，工作區已更新。",
    "loop.patch_rejected": "PATCH 套用失敗：{reason}（工作區未變動）。",
    "loop.write_test_applied": "WRITE_TEST 已套用，tests/agent/** 已更新。",
    "loop.look_header": "【repo 樹】（深度上限 {depth}）",
    "loop.inspect_header": "【檔案】{path}（{size} bytes）",
    "loop.inspect_truncated": "……（內容超過 {limit} bytes，已截斷）",
    "loop.inspect_denied": (
        "INSPECT 遭拒：路徑「{path}」不在 sandbox 內、不存在或位於黑名單。"
    ),
    "loop.plan_accepted": "計畫已通過 schema 驗證並凍結；後續修改一律不合法。",
    "loop.rollback_done": "已撤回最近一個成功套用的 patch。",
    "loop.rollback_empty": "無可撤回的 patch（stack 為空）。",
    "loop.triage_done": "TRIAGE 完成：開放的 DUPLICATE 已全數關閉。",
    "loop.run_test_bad_target": (
        "RUN_TEST 目標不在白名單（public probe id 或 tests/agent/**）：{target}。"
    ),
    "loop.probe_header": "【probe 結果】",
    "loop.probe_line": "- {probe_id}：{status}",
    # ---- human 對局（patchmud play，spec §5.4；Task 22） -------------------
    "play.banner": (
        "【human 對局】你將以同一命令協定親自打完這場 encounter：引擎、"
        "probe、queue、評分與模型對局完全同構。回覆以空行結束；"
        "Ctrl-D（EOF）視同 COMMIT 收尾。本場 run 標記 human，"
        "永不進 ranked 資料與任何聚合指標。"
    ),
    "play.input_prompt": "請輸入你的動作（回覆以空行結束；Ctrl-D 視同 COMMIT）：",
    "play.eof_commit": "偵測到輸入結束（EOF），視同 COMMIT 收尾。",
    # ---- reviewer subcall（spec §6.2；findings advisory，F10） -------------
    "reviewer.system_rules": (
        "你是 PatchMUD 對局中的 fresh-context 審查者：只依據下方提供的任務卡"
        "公開部分、累積 diff、public probe 最新結果與作者可見 artifacts 審查，"
        "你看不到作者對話，也不得臆測隱藏資產。回覆一份 YAML，頂層鍵 "
        "findings，最多 5 筆；每筆欄位：category、severity、summary、"
        "evidence（列表，每項 {{path, line}}）。summary 用中文敘述，"
        "其餘欄位維持英文結構化格式。沒有 finding 時回 findings: []。"
    ),
    "reviewer.diff_header": "【累積 diff】",
    "reviewer.diff_empty": "（目前沒有任何變更）",
    "reviewer.probes_header": "【public probe 最新結果】",
    "reviewer.plan_header": "【作者計畫（PlanArtifact）】",
    "reviewer.claims_header": "【作者 claim 歷史】",
    "reviewer.findings_header": "【審查回報】共 {count} 筆 finding：",
    "reviewer.finding_item": "- [{severity}] {category}：{summary}（證據：{evidence}）",
    "reviewer.finding_no_evidence": "無",
    "reviewer.no_findings": "【審查回報】reviewer 未回報任何 finding。",
    "reviewer.invalid": "本次審查輸出不符 schema，記為 invalid（成本照計）。",
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
