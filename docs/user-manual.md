# PatchMUD 玩家與旁觀者指南 (User Manual)

歡迎來到 **PatchMUD**！本手冊專為 **Junior Engineer（初學者玩家）** 及 **旁觀者（Spectator）** 設計，旨在幫助您迅速理解關卡結構、掌握遊戲玩法、追蹤 AI 模型（Agent）的思考鏈路與對局過程。

---

## 📖 核心概念與遊戲定位

**PatchMUD** 是一款「成本優先、純文字回合制」的程式修補評測引擎（Benchmark Game）：
- **關卡（Encounter）**：一段壞掉的小程式或真實軟體專案中的 Issue。
- **玩家（Player）**：模型代理人（AI Agent）或人類玩家（Human）。
- **回合（Turn）**：像棋盤遊戲般一回合做一件事。
- **目標**：把戰場上的「待辦議題（Backlog / Open Items）」降為 **0** 並完成提交 (`COMMIT`)。

---

## 🔰 Junior Engineer 快速上手指南

當您執行 `patchmud play <關卡>` 親自下場挑戰時，請遵循以下步驟與規範：

### 1. 關卡結構解讀
開場時畫面會印出【任務卡】：
```text
【任務卡】input-validation-v1（archetype input-validation／難度 easy）
回合上限 6、時限 300 秒。
允許修改路徑：src/**, tests/agent/**
預期修改路徑：src/discount.py
公開需求（MAIN）：
- MAIN-1：折扣碼長度必須在 6-10 個字元之間，超出範圍必須判定為無效
```
- **Briefing（問題說明）**：指出目前小程式的毛病所在。
- **MAIN-1（公開需求）**：你必須修好的 Bug，這是公開的驗收標準。
- **待辦 (Backlog)**：開場通常為 1，目標是降到 0。

### 2. 推薦對局四大步驟
初學者建議按照以下順序進行操作：

1. **Step 1: 查看專案與檔案 (`LOOK` / `INSPECT`)**
   - 先打 `LOOK` 看專案結構。
   - 再打 `INSPECT src/discount.py` 仔細查看原始碼。
2. **Step 2: 試跑測試確認問題 (`RUN_TEST`)**
   - 打 `RUN_TEST` 執行公開測試，觀察測試是如何失敗的（紅燈）。
3. **Step 3: 動手修補程式 (`PATCH`)**
   - 撰寫 Unified Diff 提交修補。
4. **Step 4: 宣布完工評分 (`COMMIT`)**
   - 測試全綠且待辦歸 0 後，打 `COMMIT` 送出收場。

### 3. PATCH 語法格式範例
修補時請使用標準 Unified Diff 格式：
```text
ACTION: PATCH
TARGET_ISSUES: MAIN-1
CLAIM: 修正折扣碼長度檢查（6-10字元）
PATCH:
--- a/src/discount.py
+++ b/src/discount.py
@@ -13,4 +13,6 @@
         return False
     if not code[0].isalpha():
         return False
+    if not (6 <= len(code) <= 10):
+        return False
     return code.isalnum()
```

---

## 🍿 旁觀者（Spectator）觀戰與分析指南

如果您不親自寫程式，而是想觀察 AI 模型如何修 Bug，PatchMUD 提供兩種觀戰視角：

### 1. 多模型並排對戰 (`patchmud versus`)
執行：
```bash
patchmud versus input-validation-v1 --models sonnet,haiku
```
- **可用模型別名**（三家共用同一組短名，可任意混搭對戰）：

  | Provider | 別名 | 認證方式 |
  |---|---|---|
  | Anthropic | `sonnet` / `haiku` / `opus` / `fable` | API key 或 OAuth |
  | OpenAI（codex CLI） | `spark` / `luna` / `terra` / `sol` | `codex login` 的登入態，免 API key |
  | Google（agy CLI） | `flash` / `pro` | `agy` CLI 登入態，免 API key |

  跨家旗艦對決：
  ```bash
  patchmud versus input-validation-v1 --models sonnet,sol,flash
  ```
- **認證與 API Key 說明**：
  - 若使用 Anthropic 雲端模型（如 `sonnet`, `haiku`, `opus`），請設定 `export ANTHROPIC_API_KEY=...`，或透過 OAuth 登入：`ant auth login` 後執行 `set -a; eval "$(ant auth print-credentials --env)"; set +a`。
  - **codex / agy 別名不需要任何 API Key**：只要該 CLI 本身已登入即可，PatchMUD 直接沿用它的登入態。
  - **💡 免 API Key / 地端 Headless 模式**：
    如果您想在本地 Headless 執行（無須任何雲端登入 / 無須網路），可搭配本地 Ollama / vLLM / LM Studio 伺服器：
    ```bash
    patchmud versus input-validation-v1 --models openai:llama3@http://localhost:11434/v1,openai:qwen2.5@http://localhost:11434/v1
    ```
- **關於 CLI 模型的成本讀數**：`claude` / `codex` / `agy` 這類 CLI 自帶 system prompt 與 skill 目錄，每回合都會多算約 1.7 萬～1.8 萬個 input token。這是該工具的固有成本，不是您的關卡造成的——看成本榜時，同一家內部比較最準確，跨家比較請把這層固定開銷考慮進去。
- **並排看板**：看不同模型在相同的凍結沙盒下，每回合各自做了什麼。
- **白話戰報**：標示哪個模型「修好了 Bug」、「打空了（Patch 失敗）」或「察看戰場未動手」。
- **終局記分板與判詞**：顯示哪個模型贏了、贏在哪（例如：更快通關、或花費更少 Token）。

### 2. 戰報重播與思考鏈路追蹤 (`patchmud watch`)
執行：
```bash
patchmud watch runs/<run_id>
```
這會將 Agent 的完整對局重播為中文戰報。戰報包含以下關鍵資訊：
- **【行動】**：Agent 選擇的動作（如 `PATCH` / `INSPECT`）。
- **【模型意圖／思考鏈】(`CLAIM`)**：顯示 Agent 宣告的修補意圖（例如 `CLAIM: 補充 6-10 字元長度檢查`）。這能讓旁觀者一眼看出模型的邏輯與思考路徑。
- **【議題變化】**：是否成功解決了議題。
- **【洪水壓力 (Flood Backlog)】**：戰場上的 Issue 堆積狀態。

---

## 📊 評分維度解讀

PatchMUD 採用三維能力綜合評分（滿分 100 分）：
1. **經濟 (Economy, 55%)**：花費的 Token 數量與 API 成本。修得越省，分數越高。
2. **火力 (Power, 25%)**：程式修補的完整度與正確性（包含隱藏測資測試）。
3. **控場 (Control, 20%)**：回合數掌控與戰場 Backlog 壓制能力。

> ⚠️ **隱藏測試 (Critical Rubrics)**：
> 看得到的公開測試過了不代表真的修好！系統會在 `COMMIT` 後執行隱藏的判題測資，抓出「表面看似通過、實際上漏洞百出」的修法。

---

## ⚡ 簡化啟動：互動式菜單 (Interactive Mode)

如果不記得命令語法或關卡名稱，直接執行 `patchmud` 即可啟動互動式選單：

```bash
patchmud
```

選單畫面如下：
```text
════════════════════════════════════════
    🎮 歡迎來到 PatchMUD 互動選單 🎮
════════════════════════════════════════
[1] 🎮 人類親自挑戰關卡 (Human Play)
[2] ⚔️ 看 AI 模型並排對戰 (Versus)
[3] 🤖 看單一 AI 模型單挑關卡 (Single Run)
[4] 🍿 離線觀戰重播 (Watch Battle Report)
[5] 🔍 驗證 Deck 關卡品質 (Validate Deck)
[0] 🚪 離開 (Exit)

請選擇操作 (0-5):
```
依照提示輸入數字即可輕鬆選擇關卡與對戰模型！
