# 白話戰報層 + 模組化出題 設計 spec

> 狀態：設計定稿，待實作。承接 pilot MVP（8 關、versus/watch/score 已運作）。

## 動機

使用者實跑 versus 後回饋兩件事：

1. **看不懂誰強在哪**（A）：戰報滿是黑話（`backlog 1`、`MAIN-1`）、分數沒收乾淨（`27.714285714285715`）、沒有一句話講清楚「這關的 bug 是什麼」「這個模型這回合做了什麼」「最後誰贏在哪」。
2. **想要更多、更貼近現實的題**（B）：現在 8 關是手工凍結的 pilot 題組。使用者要一個**結構化、模組化**的出題管道，來源可以是**已修好／closed 的 bug**。

本 spec 拆成兩個獨立可交付的部分：**A（白話戰報層）** 與 **B（出題模組）**。兩者各自成 PR。

---

## 全域約束（copy 自 repo policy / 既有契約）

- Python 3；zh-TW 敘事一律經 `render_zh_tw.text()` 查表，**禁止硬編中文字串**（AST 測試把關）。
- 任何 render pack 文案改動必須 bump `RENDER_PACK_VERSION`（golden 測試 + `harness_prompt_version` 鎖定）。
- deck 契約以 `patchmud/deck/`（`load_card` fail-closed）為唯一真相；出題產物必須能過 `load_card` 與 `validate-deck`。
- probe 執行唯一 seam 是 `IsolationRunner`（plan invariant 3）；出題的品質閘也走同一 seam，測試注入 fake。
- 政策：feature 分支、`changelog.d/*.md` fragment、`python3 -m policy_check --repo .` 零 fail、CI 綠才 merge。

---

## Part A — 白話戰報層

**目標**：不改 benchmark 語意、不動隔離，只在**呈現層**加一層「人話」，讓非開發者也能看懂一局裡誰做了什麼、誰贏在哪。

### 資料來源（皆為既有欄位，無需改引擎）

- turn event：`action`（keyword 或 None）、`outcome` ∈ `{executed, parse_error, illegal, error}`、`detail`、`queue`（`b_t` + `open_items`）。
- baseline event：`queue`。
- `result.yaml`：`clear`（1/0）、`turns`、`main_public_green`（bool）、`gates.critical_pass`（bool）、`power.total`（float）。

### A1 — 分數與用語收乾淨

- 新增純函式 `format_power(value) -> str`：float 四捨五入到小數 1 位（`27.714285… → "27.7"`、`97.0 → "97.0"`），非數值回 render pack 的 `na`。versus 記分板與 `run --live` 終局結算都改用它。
- `backlog` 對人類改稱「待辦」。versus 的 model 行改走新的白話模板（見 A3），不再直接印 `backlog N`。

### A2 — 每關一句話 bug 說明（briefing）

- `IssueCard` 新增**選填**欄位 `briefing: str | None`（`card.yaml` 選填鍵 `briefing`）；`load_card` 讀取，缺省為 `None`。既有 8 關補上 `briefing` 一句話。
- 新增純函式 `encounter_briefing(card) -> str`：有 `briefing` 用它，否則退回 `public_requirements[0].text`（永遠有值）。
- render pack 新增 `versus.briefing`（`【這關的 bug】{text}`）。versus 開場（participants 之後、基線之前）印一行。

### A3 — 每回合白話敘述

versus 的每個模型行從 `{action}｜backlog {n}｜解決 {ids}` 升級為一句人話。推導函式 `narrate_round(round_data) -> str`（純函式），輸入 `{action, outcome, resolved, backlog_before, backlog_after}`：

| 情境（action / outcome / delta） | 白話輸出（render key） |
|---|---|
| baseline | `versus.round.baseline`：`開場：待辦 {after} 件` |
| resolved 非空 | `versus.round.resolved`：`{action}：修好了 {ids}（待辦 {before}→{after}）` |
| PATCH / executed / 無 resolved 且 backlog 未降 | `versus.round.patch_noop`：`PATCH 套用了，但沒解決任何議題（待辦仍 {after}）` |
| PATCH / error | `versus.round.patch_failed`：`PATCH 套用失敗，這刀打空了（待辦仍 {after}）` |
| COMMIT / executed | `versus.round.commit`：`COMMIT 收場（待辦 {after}）` |
| outcome=illegal | `versus.round.illegal`：`{action} 被判不合法，白費一回合（待辦仍 {after}）` |
| outcome=parse_error | `versus.round.parse_error`：`回覆讀不懂，白費一回合（待辦仍 {after}）` |
| 其餘 executed（LOOK/INSPECT/RUN_TEST…） | `versus.round.observed`：`{action}：察看戰場，沒動手（待辦仍 {after}）` |

`_timeline` 需擴充為每回合也帶 `outcome` 與 `backlog_before`。model 行模板改為 `versus.model_line_v2`：`  {model}｜{narration}`。

### A4 — 收尾「誰贏在哪」判詞

記分板後加一段比較判詞，純函式 `verdict(entries) -> str`，依各 `result` 推導：

- 恰一人通關：`versus.verdict.one_winner`：`{winner} 通關、{losers} 沒有——差別在：{reason}`。
  - `reason` 由**輸家**的 result 推導：`main_public_green` 為否 → `versus.reason.public_red`（`改動沒通過公開測試`）；否則 `gates.critical_pass` 為否 → `versus.reason.critical_red`（`表面看似修好，卻沒通過隱藏的關鍵測試`）；否則 `versus.reason.other`（`未達通關門檻`）。
- 全部通關：`versus.verdict.all_clear`：`都通關；{fastest} 最省，只花 {turns} 回合` （turns 相同再比 `power.total`）。
- 無人通關：`versus.verdict.none_clear`：`這關無人通關`。

### A 測試

- `tests/engine/test_render_narration.py`：`format_power`、`narrate_round`（覆蓋上表每列）、`verdict`（三分支）、`encounter_briefing`（有/無 briefing）純函式單測。
- 更新 `tests/engine/test_versus.py`：斷言開場有 briefing 行、model 行是白話、記分板後有判詞、分數為 1 位小數。
- `tests/deck/test_loader.py`：`briefing` 選填（有讀到、缺為 None）。
- golden / render pack 版本測試：bump `RENDER_PACK_VERSION` 到 `1.5.0`。

---

## Part B — 出題模組（encounter authoring）

**目標**：一個獨立模組，把「一個已解決的 bug」以**結構化輸入**變成一道**凍結、可重現、驗證通過**的 deck 關卡。強調模組邊界清楚、可單測、與既有 `deck/` 契約對接。

### 核心洞察（closed bug ↔ deck 的天然對應）

| 已解決 bug 的產物 | deck 關卡的產物 |
|---|---|
| 修好前的原始碼 | `repo/`（待修標的） |
| 修補的 diff（PR/commit） | `hidden/reference.patch`（正解） |
| 驗證修補的測試（看得到的） | `tests/public/`（public probe） |
| 更嚴的邊界／回歸測試（藏起來的） | `hidden/test_*.py`（critical / robustness probe） |
| issue 標題／描述 | `card.yaml` 的需求文字 + `briefing` |
| issue 出處（URL/#N） | `provenance.yaml` 的來源記錄 |

### 模組佈局 `patchmud/authoring/`

- `model.py` — `SourceSpec` frozen dataclass + 巢狀契約 + `AuthoringError(ValueError)`。
- `loader.py` — `load_source(path) -> SourceSpec`：讀 `source.yaml`，fail-closed 驗證（缺欄位、路徑邊界比照 deck loader 的 `_require_clean_relpath`）。
- `builder.py` — `build_encounter(spec, dest_dir, *, runner, now) -> Path`：寫出 deck 檔、填 `card.yaml`/`provenance.yaml`、算 `content_sha256`、跑品質閘。
- `__init__.py` — 匯出 `SourceSpec`、`load_source`、`build_encounter`、`AuthoringError`。

### 出題輸入 `source.yaml`（`SourceSpec`）

刻意設計成人可手填、機可生成的結構化清單：

```yaml
schema_version: 1
issue_id: json-escape-v1
archetype: parser-edge
difficulty: easy            # easy | medium | hard
summary: JSON 字串序列化未跳脫反斜線，含 '\' 的值往返後損毀   # → briefing + MAIN 需求文字
origin:                     # 來源：已解決／closed 的 bug
  source: "closed bug: github.com/acme/x#412"
  fixed_at: "2026-05-02"
allowed_paths: ["src/**", "tests/agent/**"]
expected_paths: ["src/jsonenc.py"]
buggy_repo:                 # 修好前的檔案樹（相對 repo/ 根）
  "src/jsonenc.py": |
    def dumps(obj): ...
  "tests/starter/test_basic.py": |
    ...
reference_patch: |          # 把 buggy_repo 修好的 unified diff → hidden/reference.patch
  diff --git a/src/jsonenc.py ...
public_tests:               # 看得到的驗證測試 → tests/public/
  "tests/public/test_escape.py": |
    ...
hidden_tests:               # 藏起來的關鍵測試 → hidden/
  "test_cr1_escape.py": |   # 自動置於 hidden/ 下
    ...
requirements:               # 選填；缺省由 summary 生一條 MAIN-1
  - id: MAIN-1
    text: 含反斜線的字串序列化後往返必須完全一致
rubric_points:              # 選填；缺省套 archetype 預設權重
  functional: 60
```

### builder 行為與**品質閘**（本模組的價值核心）

`build_encounter` 依序：

1. 寫出 `repo/`（`buggy_repo`）、`hidden/reference.patch`、`tests/public/*`、`hidden/test_*`、`repo/conftest.py`（沿用 pilot 樣板：把 `src` 掛上 `sys.path`）。
2. 由 spec 合成 `card.yaml`：`public_requirements` 綁 public 測試、`critical_requirements` 綁 hidden 測試、rubric 套權重（預設值見下），寫 `briefing`。經 `load_card` 驗證（fail-closed）。
3. **品質閘（經注入的 `runner` seam 實跑）**——一道題唯有滿足下列才准凍結，否則 `AuthoringError`：
   - **bug 為真**：hidden probe 在 **buggy_repo（未套 patch）** 上必須 **RED**（至少一條）。
   - **fix 為真**：hidden probe 在 **套上 reference.patch 後** 必須 **全 GREEN**（比照既有 `validate-deck` 的既有不變式）。
   - **public 對齊**：public probe 套 patch 後 GREEN、未套 RED。
   - reference.patch 能以 `git apply` 嚴格模式乾淨套用。
4. 寫 `provenance.yaml`：`archetype_source`（記 `origin.source`）、`variant_notes`（= summary）、`frozen_at`（`now`）、`content_sha256`（deck 內容雜湊，排除 provenance 自身）。
5. 回傳 dest_dir。

> 品質閘讓「表面過、暗地掛」這個 benchmark 靈魂在**出題時就被強制**：沒有能區分真修好與糊弄的 hidden 測試，題就出不出來。

### rubric 預設（archetype 缺省權重）

`rubric_points` 未給時套既有 pilot 慣例：`functional 60 / robustness 15 / compatibility 10 / maintainability 10 / runtime_efficiency 5`；`functional` 單一 group 綁 `CR-1`。

### CLI

`patchmud author-encounter <source.yaml> --into <deck_dir> [--id <issue_id>]`：`load_source` → `build_encounter`（真 `IsolationRunner`）→ 成功印出白話結果（新關路徑 + 品質閘結論），失敗 fail-closed 印 `AuthoringError`。help 文案進 render pack / CLI help 同步（R-16）。

### B 測試

- `tests/authoring/test_loader.py`：`source.yaml` schema（缺欄位、路徑穿越、schema_version）fail-closed。
- `tests/authoring/test_builder.py`：注入 fake runner——
  - happy path：產出目錄能過 `load_card`；provenance 有 sha/frozen_at；card 綁對 probe。
  - 品質閘：hidden 在 buggy 未 RED → `AuthoringError`；套 patch 後未全 GREEN → `AuthoringError`；patch 套不上 → `AuthoringError`。
- `tests/authoring/test_author_e2e.py`：真 bwrap（無能力則 skip，比照既有 e2e），拿一份最小 closed-bug source 產題，再用產出的 deck 實跑一場 scripted run 通關，證明「出題→可玩」閉環。

---

## 不做（YAGNI）

- 不做自動從 git repo 抓 diff／自動分類 public vs hidden——`source.yaml` 由出題者顯式標明（人在迴路，避免污染與誤判）。
- 不做多語系（仍只 zh-TW render pack）。
- 不做 GUI；versus/watch 維持純文字。
- A 不改 `play`/`run` 既有 render（除 `run --live` 終局分數格式）；聚焦 versus 這個「比較」場景。

## 交付順序

1. **PR-A**：白話戰報層（含既有 8 關補 `briefing`）。
2. **PR-B**：出題模組 `patchmud/authoring/` + CLI + 一道用本模組產出的示範新關（來源標為 closed bug）。
