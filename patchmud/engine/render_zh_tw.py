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
#: 1.4.0：新增 `patchmud watch` 戰報文案（Task 23；既有文案不變）。
#: 1.5.0：新增 versus 白話戰報——briefing／每回合人話敘述／verdict（Part A）。
#: 1.6.0：play.banner 改寫為無程式背景可懂的白話規則（開場即上手）。
#: 1.7.0：新增 Junior Engineer 四步驟對局指引與 unified diff 範例，新增 watch.claim 意圖思考鏈文案。
RENDER_PACK_VERSION = "1.7.0"


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
    "yes": "是",
    "no": "否",
    # ---- run --live 終局結算 --------------------------------------------
    "run.clear_yes": "CLEAR 通關",
    "run.clear_no": "未通關",
    "run.live_settlement": (
        "=== 終局結算 ===\n"
        "結果：{clear_label}（{end_reason}）｜Power {power}／100"
        "（功能 {functional}／60｜critical gate {critical_pass}）"
    ),
    # ---- versus 並排對戰 -------------------------------------------------
    "versus.header": "════ VERSUS：{encounter}（loadout {loadout}）════",
    "versus.participants": "參賽：{names}",
    "versus.round_header": "── 回合 {n} ──",
    "versus.baseline_header": "── 開場（基線）──",
    "versus.model_line": "  {model}｜{action}｜backlog {backlog}{extra}",
    "versus.resolved_extra": "｜解決 {ids}",
    "versus.model_done": "  {model}｜（已收場）",
    "versus.action_baseline": "基線",
    "versus.action_none": "（回覆無法解析）",
    "versus.scoreboard_header": "════ 記分板 ════",
    "versus.score_row": (
        "  {model}｜{result}｜Power {power}／100｜{turns} 回合｜{cost}"
    ),
    "versus.cost_na": "成本 NA",
    "versus.cost": "成本 {cost}",
    # ---- versus 白話戰報（Part A：briefing／每回合人話／verdict） -----------
    "versus.briefing": "【這關的 bug】{text}",
    "versus.model_line_v2": "  {model}｜{narration}",
    "versus.round.baseline": "開場：待辦 {after} 件",
    "versus.round.resolved": "{action}：修好了 {ids}（待辦 {before}→{after}）",
    "versus.round.patch_noop": "PATCH 套用了，但沒解決任何議題（待辦仍 {after}）",
    "versus.round.patch_failed": "PATCH 套用失敗，這刀打空了（待辦仍 {after}）",
    "versus.round.commit": "COMMIT 收場（待辦 {after}）",
    "versus.round.illegal": "{action} 被判不合法，白費一回合（待辦仍 {after}）",
    "versus.round.parse_error": "回覆讀不懂，白費一回合（待辦仍 {after}）",
    "versus.round.observed": "{action}：察看戰場，沒動手（待辦仍 {after}）",
    "versus.verdict_header": "════ 誰贏在哪 ════",
    "versus.verdict.split": "{winners} 通關、{losers} 沒有——差別在：{reason}",
    "versus.verdict.all_clear": "都通關；{fastest} 最省，只花 {turns} 回合",
    "versus.verdict.none_clear": "這關無人通關",
    "versus.reason.public_red": "改動沒通過公開測試",
    "versus.reason.critical_red": "表面看似修好，卻沒通過隱藏的關鍵測試",
    "versus.reason.other": "未達通關門檻",
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
        "════════════════════════════════════════════════════════════\n"
        "             🎮 怎麼玩 PatchMUD 程式修補對局 🎮\n"
        "════════════════════════════════════════════════════════════\n"
        "這是一款「修東西」的回合制遊戲。關卡內會給您一台壞掉的小程式，\n"
        "您的目標是在有限回合內找出毛病、將「待辦議題 (Backlog)」降為 0！\n\n"
        "💡 【Junior Engineer 推薦四步驟指引】：\n"
        "  1️⃣ 檢視專案：打 `LOOK` 或 `INSPECT <檔名>` 觀察原始碼結構與 Bug。\n"
        "  2️⃣ 試跑測試：打 `RUN_TEST` 看公開測試在哪裡出錯（確認紅燈）。\n"
        "  3️⃣ 動手修補：打 `PATCH` 並附上 Unified Diff 來修改程式。\n"
        "  4️⃣ 宣布完工：待辦歸 0 且測試全綠後，打 `COMMIT` 送出收場！\n\n"
        "🛠️ 【PATCH 命令格式範例】（Unified Diff 格式）：\n"
        "   ACTION: PATCH\n"
        "   TARGET_ISSUES: MAIN-1\n"
        "   CLAIM: 修正折扣碼長度限制 (6-10字元)\n"
        "   PATCH:\n"
        "   --- a/src/discount.py\n"
        "   +++ b/src/discount.py\n"
        "   @@ -13,4 +13,6 @@\n"
        "            return False\n"
        "   +    if not (6 <= len(code) <= 10):\n"
        "   +        return False\n"
        "        return code.isalnum()\n\n"
        "🏆 【勝利與評分標準】：\n"
        "   修對了待辦降為 0，`COMMIT` 後系統會執行隱藏測試（Critical Rubrics），\n"
        "   給出「通關／未通關」與綜合能力得分（最高 100 分，越乾淨越省回合越高）。\n\n"
        "（這是您親自玩的場次，不列入任何排名。只想看熱鬧的話，改用 versus 看 AI 對決。）\n"
        "下面是精確的指令格式，照著打即可 ↓"
    ),
    "play.input_prompt": "請輸入你的動作（回覆以空行結束；Ctrl-D 視同 COMMIT）：",
    "play.eof_commit": "偵測到輸入結束（EOF），視同 COMMIT 收尾。",
    # ---- watch 戰報（patchmud watch，spec §5.4；Task 23） -------------------
    "watch.report_header": "【戰報】run {run_id}｜loadout {loadout}",
    "watch.baseline_header": "=== 開場（turn 0 基線） ===",
    "watch.turn_header": "=== 回合 {turn} ===",
    "watch.action": "【行動】{action}——{verdict}",
    "watch.action_unparsed": "【行動】（回覆無法解析，未宣告任何動作）",
    "watch.claim": "【模型意圖／思考鏈】{claim}",
    "watch.outcome.executed": "動作完成",
    "watch.outcome.parse_error": "回覆無法解析（照樣消耗一回合）",
    "watch.outcome.illegal": "動作不合法（照樣消耗一回合）",
    "watch.outcome.error": "動作執行失敗（工作區未變動）",
    "watch.detail": "【說明】{detail}",
    "watch.reviewer_subcall": (
        "【審查】本回合嵌入 reviewer subcall：findings {count} 筆（valid: {valid}）"
    ),
    "watch.queue_delta_header": "【議題變化】",
    "watch.queue_spawned": "- 新增 {item_id} [{type}]",
    "watch.queue_resolved": "- 解決 {item_id} [{type}]",
    "watch.queue_unchanged": "- （議題佇列無變化）",
    "watch.queue_open_header": "【開放議題】{count} 項",
    "watch.queue_open_item": "- {item_id} [{type}]",
    "watch.final_header": "=== 終局結算 ===",
    "watch.final_summary": "終局原因 {end_reason}｜使用回合 {turns}｜Clear = {clear}",
    # ---- watch 操作性錯誤（viewer 對人類的 fail-closed 敘事） --------------
    "watch.error.result_not_mapping": "result 必須是 mapping（result.yaml 內容）",
    "watch.error.missing_field": "event 缺欄位「{field}」（type {type}）",
    "watch.error.no_baseline": "events 缺 turn-0 baseline，無法推導 queue 變化",
    "watch.error.unknown_event": "未知事件型別「{type}」，非回合制 run 無法觀戰",
    "watch.error.turn_not_found": "找不到回合 {turn} 的 turn event",
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
