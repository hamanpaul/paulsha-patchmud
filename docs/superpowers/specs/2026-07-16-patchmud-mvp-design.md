# PatchMUD MVP implementation spec（2026-07-16）

> 狀態：v1.1 implementation spec。來源為 `docs/PatchMUD_research_report_zh-TW_v0.2.md`（研究設計 v0.2）。
> v1.1 已通過 codex（gpt-5.6-sol）對抗審查並整合全部 21 條 findings（見附錄 D）。
> 範圍鎖定報告 §20 的 Phase 1–4（離線評分核心、回合引擎與四策略、Issue Flooding、Forced Loadout Pilot）。
> Phase 5（Autonomous Draft）、Phase 6（正式賽季）、RPG Exhibition Mode 一律列為 deferred workstream，只保留資料欄位相容性。

## 0. 決策摘要（需使用者確認的項目以 ★ 標記）

| 決策 | 選擇 | 理由 |
| --- | --- | --- |
| ★ 落地位置 | 新獨立 repo `paulsha-patchmud`，Python package `patchmud`，對 `paulsha-cortex` / `paulsha-hippo` 零 runtime 依賴 | 符合生態系拆包哲學；cortex 定位是治理平面，不該吞下 benchmark 引擎。spec / plan 文件留在 cortex `docs/superpowers/`（研究報告所在地），實作計劃 Task 0 負責 scaffold 新 repo |
| MVP 範圍 | 報告 Phase 1–4 | Phase 5/6 依賴 Phase 1–4 的校準產物，先做會空轉 |
| 技術選型 | Python 3.11+、pytest、YAML deck、JSONL event log、git worktree sandbox | 與生態系一致；deck fixture 全為 Python 專案 |
| 裁判原則 | 排名資料流零 LLM 裁判；reviewer finding 不進 ranked Control（附錄 D F10），只留 post-hoc 分析 | 報告 §3「所有關鍵結果均由 artifact、測試、版本差異與可稽核事件日誌決定」 |
| 沙箱隔離 | probe / candidate code 一律在 mount+net+pid namespace（bubblewrap 或等效）內執行，bind allowlist 只含 worktree 與唯讀 toolchain | `unshare -rn` 缺 mount namespace，candidate code 可讀 host 上的 hidden 資產（F1）；ranked run 無 namespace 支援即拒絕啟動 |
| ★ 地端模型經濟性 | MVP 不給地端模型單一數字 CostPerClear 排名；以預註冊成本情境帶（low/mid/high）平行報告 | 地端無 API 價目，填 0 會除零、填 NA 會整排消失（F18）；正式能耗模型 deferred |

## 1. 目標與非目標

### 1.1 目標

實作一套可重現的回合制 coding-agent 評測引擎，能：

1. 以 frozen repo + issue card 執行 encounter，記錄完整 event log 並可離線重播。
2. 強制執行 SOLO / PLAN / TDD / REVIEWER 四策略與其 compliance 規則（報告 §8）。
3. 以 deterministic 規則維護 dynamic issue queue，量測 Issue Flooding（報告 §7）。
4. 以互斥 token ledger 與版本化計價快照計算 run 成本（報告 §5）。
5. 在終局執行 hidden evaluator，產出 Power、hard gates 與 Control / Economy 分數（報告 §6、§7、§11）。
6. 執行 forced loadout pilot（4 母題 × 2 變體 × 8 loadouts × N 模型）並輸出研究指標（報告 §13、§15）。

### 1.2 非目標（MVP 不做）

- Autonomous Draft、selection regret、策略自主切換（Phase 5）。引擎仍記錄 `strategy_switches` 欄位，MVP forced loadout 恆為 0。
- 正式賽季、fixed-reviewer track、cross-model party track（Phase 6）。MVP 只做 self-team reviewer（作者模型、fresh context）。**因此 RQ5 在 MVP 標記 deferred**：矩陣中 reviewer 能力與作者能力完全共變，無法估計補償效果（F19）；欄位照收，claim 不做。
- RPG Exhibition Mode 的任何遊戲機制。
- 地端模型正式能耗 / 硬體攤提計算器（見 §10.5 的情境帶替代）。
- Mutation testing。`mutation_score` 欄位保留為 `NA`。
- 階層式貝氏三維能力估計與 RQ6「預測穩定性」比較、RQ7 的條件控制迴歸（F20）。MVP 產出原始指標、bootstrap CI 與分析就緒欄位；正式統計方法於 pilot 後 pre-register。
- 任何 leaderboard 網站 / TUI 前端。MVP 的 frontend 是 CLI 與純文字 render。

## 2. 系統架構

對應報告 §17.1，落成五個子套件與一個 CLI：

```text
patchmud/
  deck/        # issue cards、fixture repo 物化、變體與 provenance
  engine/      # 回合迴圈、命令解析、strategy enforcer、issue queue、flood tracker
  sandbox/     # worktree 物化、patch 套用、namespace 隔離的 probe 執行
  ledger/      # token ledger、pricing snapshots、cost model
  evaluator/   # hidden tests、power rubric、hard gates（獨立 checkout）
  metrics/     # CostPerClear、TokensPerClear、QATY、EuTB、MTY、FTR、Flood/Control/Economy
  adapters/    # anthropic、openai-compatible（涵蓋地端 vllm/ollama）
  store/       # run 目錄、events.jsonl、transcript、checkpoints、replay
  cli.py       # patchmud run / pilot / replay / report / validate-deck
```

硬性邊界：

- hidden 資產（hidden tests、reference patch、rubric 權重、reference timing）只存在於 deck 的 `hidden/` 目錄，永不物化進 sandbox worktree；且所有 candidate code 的執行（probe、compile、import）都發生在 mount namespace 內，bind allowlist 不含 deck、store、engine 程式碼與使用者 `HOME`（§7）。「拿不到」由**建構排除＋執行期隔離**共同保證，越界嘗試（INSPECT 越界、probe 內部對 allowlist 外路徑的存取失敗）落 event log 供稽核。
- `engine/` 只透過 `sandbox/` 的介面觸碰檔案系統；只透過 `adapters/` 觸碰模型。
- `metrics/` 只讀 `store/`，不參與 run 中決策，保證評分可離線重算。

## 3. Domain model

| 名詞 | 定義 | 持久位置 |
| --- | --- | --- |
| `Encounter` | 一張 issue card + frozen fixture repo + hidden 資產 | `decks/<deck>/<encounter_id>/` |
| `Run` | 一個 (model, harness, loadout, encounter) 的一場對局 | `runs/<run_id>/run.yaml` |
| `AuthorTurn` | 一次**作者** agent 呼叫 + 引擎執行結果；`max_turns` 只計 author turns | `events.jsonl` |
| `ReviewerSubcall` | `SUMMON REVIEWER` 觸發的 fresh-context 呼叫；嵌在該 author turn 內，消耗 wall-clock 與 token（計入 `C_reviewer`），**不消耗 max_turns**；其產生的 findings 在同一 turn 結束時進入 render（F9） | `events.jsonl` |
| `Action` | agent 回覆解析出的結構化動作（§5 命令集） | `events.jsonl` |
| `IssueItem` | queue 中一項，type ∈ {MAIN, REOPENED, REGRESSION, SCOPE, CHURN, DUPLICATE}（REVIEW-DEBT 見 §6.2） | `events.jsonl`（每回合 snapshot） |
| `Probe` | 可執行的公開診斷（public test 群、smoke、compat check、lint）；IssueItem 的 spawn / resolve 條件一律綁 probe 結果或 diff 幾何 | deck 宣告 |
| `TokenLedgerEntry` | 一次模型呼叫的互斥 token 欄位 + billed totals + 計價快照 ref | `ledger.jsonl` |
| `PricingSnapshot` | `(provider, model, date)` 的完整計價：每百萬 token 各類價、`per_request`、`per_tool_call`、最低消費（F16） | `pricing/<provider>/<date>.yaml`，內容 hash 記入 run |
| `PlanArtifact` | PLAY PLAN 提交的 YAML（schema 見 §6.3），提交後凍結 | `runs/<run_id>/artifacts/plan.yaml` |
| `ReviewerFinding` | reviewer 回傳的結構化 finding（上限 5 筆/次）；advisory，只進 log 與 post-hoc 分析 | `runs/<run_id>/artifacts/review_<n>.yaml` |
| `Checkpoint` | 每 author turn 結束時 sandbox worktree 的 shadow commit | `runs/<run_id>/checkpoints/`（bare repo） |
| `PowerReport` | hidden evaluator 輸出的分項分數、probe 逐項結果與 hard-gate 判定 | `runs/<run_id>/result.yaml` |
| `RunMetrics` | run 級與跨 run 聚合指標 | `result.yaml` + `report/` 輸出 |

所有持久化 artifact 都帶 `schema_version`。event log 為 append-only；重播時任何 schema 不符即 fail-closed 拒絕重算，不做靜默 migration。

## 4. Issue Deck 契約

### 4.1 目錄佈局

```text
decks/pilot-v1/<encounter_id>/
  card.yaml            # issue card（下）
  repo/                # fixture 專案原始碼（含 public spec、public/starter tests）
  hidden/              # hidden tests、property tests、perf tests、reference.patch、reference_timings.yaml
  provenance.yaml      # 母題來源、公開時間、變體差異描述、封存時間
```

engine 在 run setup 時把 `repo/` 物化成 git repo：固定 author、固定 timestamp（epoch 0）、單一 initial commit，因此 frozen SHA 對同一 deck 版本恆定，寫入 `run.yaml`。

### 4.2 card.yaml（在報告 §12.1 之上補齊引擎需要的欄位）

```yaml
schema_version: 1
issue_id: phantom-removal-v1
archetype: state-recovery
difficulty: medium
difficulty_scale: 1.0          # D_issue；pilot 期間全部 1.0，Phase 4 依 §10.4 estimator 校準
expected_patch_loc: [30, 80]
wall_clock_seconds: 600
max_turns: 8
allowed_paths:                 # 硬邊界；之外的 production 變更 → SCOPE(hard)
  - "src/**"
  - "tests/agent/**"
expected_paths:                # 軟邊界；allowed 內但 expected 外 → SCOPE(soft)，LOC 計入 S_scope（F12）
  - "src/snapshot.py"
public_requirements:           # MAIN items；每項綁一組 public probe
  - id: MAIN-1
    text: transient failure must not emit removal
    probe: tests/public/test_transient.py
critical_requirements:         # hard gate；由 hidden evaluator 判定
  - id: CR-1
    hidden_probe: hidden/test_cr1.py
regression_probes:             # 既有行為守門；綠→紅 → REGRESSION item
  - tests/starter/
  - smoke: ["python", "-c", "import example"]
compat_probes:                 # 公開 API 相容檢查
  - probe: tests/public/test_api_surface.py
power_rubric:                  # §9 的分項權重與 probe 對應，全部可執行
  functional:
    points: 60
    groups:                    # group 全綠得該組分數（all-or-nothing）
      - {id: CR-1, points: 40, probe: hidden/test_cr1.py}
      - {id: CR-2, points: 20, probe: hidden/test_cr2.py}
  robustness:
    points: 15                 # 以全部 robustness probes 的 pytest case 數為分母，case 級線性計分（F15）
    probes: [hidden/test_edges.py]
  compatibility:
    points: 10                 # compat probes 全綠 10、任一紅 0
    probes: [tests/public/test_api_surface.py, hidden/test_compat.py]
  maintainability:
    points: 10                 # deterministic proxy，公式見 §9.3
  runtime_efficiency:
    points: 5
    probes: [hidden/test_perf.py]
    timeout_factor: 3.0        # 通過條件：runtime ≤ factor × reference_timings.yaml 對應值（F15）
reference_cost: null           # C_ref；pilot 後依 §10.4 estimator 填入
```

`patchmud validate-deck` 驗證：schema、probe 檔案存在、public/hidden 不重疊、`expected_paths ⊆ allowed_paths`、`repo/` 可安裝可跑 starter tests、hidden probes 在 `hidden/reference.patch` 下全綠，並在 pinned 環境量測 `reference_timings.yaml`（deck CI 產物；僅供 evaluator 使用，永不進 run）。

### 4.3 Pilot deck 規模

4 個母題 × 2 個語意變體 = 8 encounters。母題取自報告 §12.2 類型（輸入驗證、parser edge、狀態機 recovery、legacy regression 各一）。變體修改邊界規則 / 錯誤契約 / 常數，不只改名（報告 §12.4）。`provenance.yaml` 記錄母題公開時間與封存時間。

## 5. 回合協定

### 5.1 命令集與語意

沿用報告 §9.2 的命令面，逐一 pin 語意：

| 命令 | 語意 | 失敗語意 |
| --- | --- | --- |
| `LOOK` | 回傳 repo 樹（深度受限）、issue queue render、資源狀態 | — |
| `INSPECT <path>` | 回傳 sandbox 內檔案內容（上限 64KB，超過截斷並標示）；path 必須在 sandbox 內且不在黑名單（`.git`、probe 保護區之外皆可讀） | sandbox 外 / 不存在 → error result，事件記為 `inspect_denied` |
| `PLAY PLAN` | 提交 plan YAML，依 §6.3 schema 驗證後凍結；只能在第一次 `PATCH` 前 | 重複提交 / 已 PATCH 後提交 / schema 不過 → illegal |
| `WRITE_TEST` | 提交 test-only diff；只能新增或修改 `tests/agent/**` | 觸及 production / 既有測試檔 → illegal |
| `PATCH` | 提交 production unified diff；`git apply` 嚴格模式（無 fuzz、無 3way） | apply 失敗 → error result，不改變 worktree |
| `RUN_TEST [target]` | 執行 public probes 或 `tests/agent/**` 內 agent 自建測試（全部或指定） | 非白名單 target → illegal |
| `SUMMON REVIEWER` | 啟動 fresh-context reviewer（§6.2）；作為 ReviewerSubcall 嵌在本 turn | R0 loadout 中 → illegal |
| `TRIAGE` | 關閉 DUPLICATE、標記 wont-fix、重排 queue render 順序 | — |
| `ROLLBACK` | 撤回最近一個成功套用的 patch（引擎維護 patch stack） | 無可撤回 → error result |
| `COMMIT` | 終局；以當下 worktree 對 frozen base 的 cumulative diff 為 final artifact | R1 未滿足（§6）→ illegal |

### 5.2 回合、probe 排程與終局

- **每一次作者呼叫恰好消耗一個 author turn**，不論動作合法與否——token 已真實花費，成本模型必須反映。ReviewerSubcall 不消耗 turn（§3）。
- 回覆無法解析 → 結構化 parse error（附格式提示），消耗一 turn。連續 3 次 invalid / illegal → `failed:protocol` 終局。
- **Turn 0 baseline**：run setup 時引擎先跑**全部** public probes（requirements、regression、compat、starter），封存 baseline 結果。所有「綠→紅」判定都相對於引擎上一次執行結果，因此第一回合就存在可比較基準（F11）。
- **Probe 排程**：每次 `PATCH` / `WRITE_TEST` / `ROLLBACK` 成功後，引擎自動執行**全部 public probe 套件**（它們本來就公開，agent 免費資訊不構成洩漏；sandbox CPU 開銷照記）。`RUN_TEST` 是 agent 主動要求的重跑。queue 依最新結果更新（報告 §9.4 步驟 4）。
- **終局觸發**：`COMMIT`、`max_turns` 用盡、`wall_clock_seconds` 到期、`failed:protocol` 四者之一。終局一律執行：全部 public probes ＋ hidden evaluator 全套。
- **Clear 的唯一定義**（F3）：

  ```text
  Clear = 1  ⟺  終局時 (a) 全部 critical hidden probes 綠
              ∧ (b) 全部 MAIN public probes 綠
              ∧ (c) run 未以 failed:protocol 終局
  ```

  其餘一律 `Clear = 0`。CostPerClear、TokensPerClear、QATY、EuTB 的分母/指示函數只引用此布林。
- agent 回覆格式沿用報告 §9.3（`ACTION:` / `TARGET_ISSUES:` / `FILES:` / `CLAIM:` / `PATCH:` 區塊）；不要求也不記錄私密 chain-of-thought。

### 5.3 harness prompt

system prompt 與狀態 render 模板版本化為 `harness_prompt_version`，寫入 run.yaml。每 turn 傳送：系統規則 + issue card 公開部分 + 累積對話 transcript + 當回合狀態 render。完整 transcript 落盤，token 用量按 adapter 回報計量（含 cache 與 billed totals 欄位）。

## 6. Strategy enforcer

Forced loadout 下，引擎在 run 開始時載入 `(P, T, R)` 三個 bit 並強制執行：

| Factor | =1 時強制 | =0 時禁止 |
| --- | --- | --- |
| P（PLAN） | 第一次 `PATCH` 前必須有通過 §6.3 schema 的 `PLAY PLAN`；違反 → illegal | `PLAY PLAN` → illegal |
| T（TDD） | 第一次 production `PATCH` 前必須達成 valid red（§6.1） | `PLAY TDD` → illegal；`WRITE_TEST` 仍允許（見下） |
| R（REVIEWER） | `COMMIT` 前必須存在一次**合格 review**：schema-valid、其輸入 diff 非空（至少一個 production patch 已套用）、且 findings 已在 COMMIT 之前的某個 turn render 給作者（F8） | `SUMMON REVIEWER` → illegal |

SOLO = P0T0R0，無任何額外限制與 bonus（報告 §8.1）。

**Treatment 語意（F5）**：T0 不禁止 agent 自發 red-first 工作流——禁止自然行為會扭曲 SOLO baseline 的真實性。因此 TDD factor 的 estimand 明確定義為 **enforcement uplift（intention-to-treat）**，不是「有無 TDD 行為」的因果效應。引擎在**所有** cell 記錄 `observed_tdd_workflow`（是否實際發生 valid-red→production→green 序列），供 per-protocol 次要分析與 crossover 率報告。P factor 同理記錄 `observed_planning`（P0 下 agent 無法提交 PlanArtifact，此欄恆 false，僅為欄位對稱）。

### 6.1 TDD valid red 判定（防 gaming 硬化，F6）

1. `WRITE_TEST` 產生的新測試檔在 `tests/agent/**`；引擎記錄每個新增 pytest nodeid 與**測試檔 content hash**。
2. 隨後執行中，至少一個新增 nodeid 以 pytest **`failed`**（assertion / 行為失敗）結束——`error`（collection、import、syntax）不算。達成的 nodeid 集合與其 failure fingerprint（異常類型＋斷言訊息首行）封存為 red evidence。
3. valid red 未達成前，production `PATCH` → illegal。
4. **green 閉環**：T1 的 `tdd_compliant = true` 要求 red evidence 中至少一個 nodeid 在終局（a）測試檔 content hash 與 red 時**完全一致**（期間任何修改即重置該檔全部 red evidence），且（b）pytest 結果為 `passed`。`assert False` 類假 red 永遠無法在不改測試檔的前提下轉綠，因此被結構性排除。
5. red→green 之間只有 production 檔案變更（由 4a 保證測試側不變）。
6. T1 下終局 `tdd_compliant = false`（例如 red 測試被刪除、修改或終局仍紅）→ 該 run 標記 `strategy_violation`，進 event log 與分析欄位；Clear 判定不受影響（違規是流程事實，不偽造品質結果），但該 run 從 TDD uplift 的 per-protocol 分析中列為 non-compliant。

### 6.2 REVIEWER 協定

- reviewer 使用與作者相同 model snapshot、全新 context（self-team track）。
- 輸入嚴格限定：issue card 公開部分、當下 cumulative diff、public probe 最新結果、作者可見 artifacts（plan、claim 歷史）。不含作者 transcript、不含 hidden 資產。
- 輸出 schema：最多 5 筆 finding，每筆 `{category, severity, summary, evidence: [{path, line}]}`。schema 驗證失敗 → 該次 review 記為 `invalid`，成本照計。
- **Findings 是 advisory（F10）**：finding 只 render 給作者並落盤，**不生成 queue item、不進 Flood Index、不進 ranked Control**。理由：finding 是 LLM 產物，幻覺 finding 會讓 Control 受 reviewer 隨機性污染，且 hunk 相交式的「已處理」判定可被一行註解 game 掉。此為對報告 §7.2 REVIEW-DEBT 的**明文偏離**：MVP 的 Flood Index 中 review-debt 權重設 0；REVIEW-DEBT item type 保留於 schema，待 Phase 6 有 executable-probe-backed finding 機制再啟用。
- Reviewer 效度指標改為 **post-hoc**：run 結束後，以 hidden evaluator 逐項結果離線比對 findings（finding 指向的 path 是否確實關聯終局 hidden failure），產出 `reviewer_finding_precision` 與 `author_fix_conversion`（作者在 review 後是否修改 evidence 相交區域且對應 probe 終局轉綠）。hidden 結果只在賽後使用，不回饋同場（報告 §10.2）。
- reviewer 的全部 token 計入 `C_reviewer`。

### 6.3 PLAN schema（F7）

```yaml
requirements: [..]      # 非空；必須涵蓋 card 全部 public_requirements[].id
invariants: [..]        # 非空字串列表，至少 1 項
files_to_inspect: [..]  # 至少 1 項；每項必須存在於 frozen repo
risks: [..]             # 非空字串列表，至少 1 項
test_targets: [..]      # 至少 1 項；每項必須是 tests/ 下的 repo-relative path（允許尚不存在的 tests/agent/** 路徑）
```

任一欄缺失、空列表、空字串、requirements 未涵蓋全部 MAIN id、`files_to_inspect` 引用不存在檔案 → schema 不過，`PLAY PLAN` 為 illegal action（消耗 turn）。通過後凍結，後續修改一律 illegal。

## 7. Sandbox executor（F1 修正後）

- 每 run 一個獨立 worktree，從 frozen commit 物化。
- patch 套用：`git apply --check` 先驗，嚴格模式；路徑必須 repo-relative 且不落在 `.git` / probe 保護區。
- **probe 保護區**：`tests/public/**`、`tests/starter/**`、`benchmark/**` 對 `PATCH` / `WRITE_TEST` 唯讀。回歸與 requirement 判定一律以 deck 原始 probe bytes 執行（每次執行前從 deck overlay 還原），杜絕改測試過關。
- **執行隔離（規範性）**：所有會執行 candidate code 的動作（probe、import smoke、compile、lint 於 changed files、hidden evaluator）必須在同時具備 **mount、network、PID namespace** 的沙箱內執行（實作優先序：bubblewrap；等效的 `unshare -rmnp` + `pivot_root` 可接受）。bind allowlist 只含：worktree（rw）、Python toolchain / venv（ro）、新鮮 tmpfs `/tmp`。**不得**含 deck 目錄、store / runs 目錄、engine 程式碼、使用者 `HOME`、`/proc` 的 host view。環境變數 sanitized（`PATH/LANG/LC_ALL/TMPDIR` 白名單）。
- namespace 能力不足（無 bubblewrap、無 userns）時：run 標記 `sandbox_isolation: degraded`，**ranked / pilot run 拒絕啟動（fail-closed）**；dev run 允許並在一切輸出打上 degraded 浮水印。
- hidden evaluator 使用**獨立 checkout**（frozen base + final diff）與同級 namespace 隔離；即使 candidate code 在 evaluator 階段執行，也讀不到 deck、reference patch 與其他 run 資料。
- 每 author turn 結束把 worktree commit 進 shadow bare repo（checkpoint），供離線 MTY 重播與 replay 驗證。
- 資源記錄：每 probe 的 wall-clock、CPU time（children rusage）；MVP 計價為 0 但欄位落盤（報告 §5.2 的 C_sandbox 保留項）。

## 8. Issue queue 與 flooding

### 8.1 Deterministic 觸發規則（報告 §7.2 的可執行化）

| Type | Spawn 規則 | Resolve 規則 |
| --- | --- | --- |
| `MAIN` | run 開始時，每個 `public_requirements[]` 一項 | 綁定 probe 全綠（引擎每 mutating action 後自動判定，不依賴 agent 主動 RUN_TEST） |
| `REOPENED` | 某 MAIN item 曾 resolved，其 probe 後續再紅 → 原 item 關閉、spawn REOPENED | probe 再綠 |
| `REGRESSION` | `regression_probes` / `compat_probes` 任一相對**引擎上次執行結果**（含 turn 0 baseline）綠→紅 | 該 probe 再綠 |
| `SCOPE(hard)` | cumulative diff 觸及 `allowed_paths` 外的 production 檔案；聚合單一 item 附檔案清單 | 越界變更全數撤回 |
| `SCOPE(soft)` | `allowed_paths` 內但 `expected_paths` 外的變更；不生成獨立 item，LOC 計入 `S_scope`（F12） | 對應變更撤回 |
| `CHURN` | 單回合 revert 掉 ≥ 10 行（card 可調）自己先前新增的行（以 patch stack diff 計算 `reverted_loc`） | 事件性 item，下一回合自動關閉 |
| `DUPLICATE` | `PATCH` 的 `TARGET_ISSUES` 引用已 resolved / 已關閉 item | TRIAGE 關閉 |

另外兩個**計數器**（非 queue item）：

- `failed_claim_count`：`PATCH` 的 `TARGET_ISSUES` 宣告某 MAIN/REOPENED/REGRESSION，該 probe 執行後仍紅（報告 §7.2「宣稱修復但仍失敗」的 claim 語意，與 probe 語意的 REOPENED 分開記，F11）。
- `dismissed_findings`：TRIAGE wont-fix 的 reviewer findings 數（post-hoc 分析用）。

規則只依 probe 結果、diff 幾何與宣告欄位運作，**queue 更新零 LLM 參與**。

### 8.2 Flood 計量（F13、F14 修正後）

- `B_t` = author turn t 結束時 open items 數；`M_t` = 其中「從未 resolved 過的原始 MAIN item」數。
- **雙指標**：
  - `FloodArea_total = Σ_t B_t`（報告 §7.3 原式，Δt = 1 turn；wall-clock 加權版平行落盤）。
  - `FloodArea_excess = Σ_t (B_t − M_t)`：只計自生債務（REOPENED、REGRESSION、SCOPE、CHURN、DUPLICATE）的存續面積。
- **Control 使用 excess**：P1/T1 loadout 被強制先花回合走流程，若用 total，完美執行的 PLAN/TDD run 會單因初始 MAIN 存續而被扣 Control（P1 完美 run FloodArea=1、T1=2 vs SOLO=0），Control 因子比較被結構性混淆。原始 MAIN 的存續時間已透過 turn 數與 token 自然反映在 Economy。此為對報告 §7.3 的**明文偏離**，`FloodArea_total` 仍照報告發布。
- Flood Index `F` 依報告 §7.4 公式，以 `FloodArea_excess` 代入、review-debt 項權重 0（§6.2）、其餘係數為 deck 級常數檔（版本化）；`D_issue = difficulty_scale`。
- `Control = 100 × exp(−F/τ)`，τ 由 §10.4 estimator 於 pilot 後凍結。
- **FTR（F14）**：token 歸屬以 **turn 開始時** 的 backlog 狀態判定：

  ```text
  FTR_r = Σ_t ΔT_t · 1(B_{t-1} > B_0) / Σ_t ΔT_t
  ```

  另平行發布 `flood_create_tokens`（造成 B 上升的 turn 之 token）與 `flood_repair_tokens`（B 由高處下降的 turn 之 token），避免單一 ratio 掩蓋 create/repair 差異。
- Flood 狀態門檻（Stable/Noisy/Flooded/Meltdown）只影響 render 文案，不進任何分數（報告 §7.5）。

## 9. Hidden evaluator 與 Power

### 9.1 執行時機與隔離

只在終局後執行：evaluator 在獨立 checkout（frozen base + final diff）以 §7 同級 namespace 隔離套 hidden probes。中間 checkpoint 的 hidden 評分只允許離線重播（MTY 用），嚴禁回饋同場 run（報告 §5.6.5、§10.2）。

### 9.2 Hard gates（報告 §6.1 逐條落地）

| Gate | 判定 | 效果 |
| --- | --- | --- |
| critical requirements 未全過 | 任一 `critical_requirements[].hidden_probe` 紅 | `Clear=0`、`Economy=0`、Utility ≤ 49 |
| 無法 import / compile / 起測試 | evaluator collection 階段失敗 | Power ≤ 15 |
| 公開 API 不相容變更 | `compat_probes` 任一紅 | Power ≤ 50 |
| 存取 hidden 資產 | 建構排除＋namespace 隔離（§7）；偵測到越界嘗試（`inspect_denied`、sandbox 內對 allowlist 外路徑存取）記入稽核 log，人工複核確認蓄意探測 → run 無效 | run invalid |

### 9.3 Power rubric（100 分，公式全 pin，F15）

- **functional 60**：`power_rubric.functional.groups` 各 group 內 probe 全綠得該組分數（all-or-nothing），否則 0。
- **robustness 15**：分母 = 全部 robustness probes 的 pytest case 總數（evaluator collection 計得）；得分 = 15 × passed_cases / total_cases。
- **compatibility 10**：compat probes 全綠 10，任一紅 0（與 hard-gate cap 疊加取低）。
- **maintainability 10**：
  - diff 大小 4 分：`L` = final diff 的 added + deleted 行數（`git diff --numstat` 加總，production 檔案）。`L ≤ hi`（`expected_patch_loc[1]`）→ 4 分；`L > hi` → `4 × max(0, 1 − (L − hi)/hi)`；低於下限不扣分。
  - scope 3 分：無 SCOPE(hard) 殘留 → 2 分；`S_scope`（soft LOC）= 0 → 再 1 分。
  - lint 3 分：`ruff check`（pinned 版本與規則集）對 changed files 相對 frozen base **零新增** diagnostics → 3 分，否則 0。
- **runtime_efficiency 5**：每個 perf probe 在 `timeout_factor × reference_timings.yaml` 內通過各得均分。reference timing 由 deck CI 在 pinned 環境對 reference patch 量測（§4.2）。

### 9.4 重複懲罰隔離（報告 §6.2）

- 終局仍存在的 regression → 扣 Power（compat/functional probes 紅）。
- 中途發生、終局已修復的 regression → 只進 Control（FloodArea_excess、N_regression）與 Economy（真實 token / 成本），不扣 Power。
- 額外呼叫與測試 → 只依 ledger 真實支出進 Economy。

## 10. Token ledger 與成本模型

### 10.1 互斥 ledger（報告 §5.6.1 + F17）

每次 adapter 呼叫落一筆：

```text
{turn, role: author|reviewer,
 input_uncached, input_cached, output_visible, reasoning,      # 互斥欄位；不可得記 NA
 billed_input_total, billed_output_total,                      # provider 帳面總量，永遠照抄
 unallocated,                                                  # billed 總量減去可拆分欄位的殘差
 api_calls: 1, wall_clock_ms, prompt_bytes, generated_bytes,
 pricing_snapshot_ref}
```

- **計費一律以 billed totals 計算**，互斥欄位只用於效率分析——即使 provider 把 reasoning 併進 output total 而無法拆分，帳單也不會錯（F17）。
- `cached ⊆ input` 時先拆 uncached；reasoning 含在 output 時先扣除；拆不動的殘差進 `unallocated`。
- provider 不揭露 reasoning → 記 `NA`，不得記 0；NA 聚合傳染（任何含 NA 的聚合輸出 NA + observable 版本雙欄）。
- **Disclosure cohort（F17）**：token-efficiency 排名（TokensPerClear、QATY、EuTB、MTY、FTR）只在相同 disclosure cohort（全揭露 vs 僅 observable）內進行；跨 cohort 只發布以 `input + output_visible` 一致計算的 common-observable 描述性欄位，明確標註 non-ranking——否則「少揭露 reasoning」的模型在固定 budget 指標上憑空得利。
- mapping 有 per-provider 單元測試釘死（usage fixture → 期望互斥欄位 + billed totals + unallocated）。

### 10.2 成本

- `C_run = C_model + C_reviewer`（MVP）；`C_sandbox`、`C_infra`、`λ_t T_wall` 欄位落盤、計價 0，敏感度分析在 metrics 層以參數重算。
- `C_model` 依報告 §5.2 公式以 run 內 pin 的 PricingSnapshot 計算，**含 `per_request × api_calls`、per_tool_call 與最低消費項**（F16）。snapshot 檔案 hash 寫入 run.yaml，價格改動不影響已封存 run。
- `CostPerClear`、`Economy_i` 轉換依報告 §5.4、§5.5；`reference_cost` 未校準（null）時 Economy 輸出 `NA`，只報原始成本——pilot 前不產 0–100 Economy 分數。`C_run = 0` 是設定錯誤，engine 拒絕（F18 的除零防線）。

### 10.3 效率稽核指標

`TokensPerClear`、`QATY`、`EuTB`、`MTY`、`FTR` 依報告 §5.6.2–5.6.6（FTR 採 §8.2 修正式）實作，全部另附 encounter-level bootstrap CI（B=10,000，seed 記錄）。EuTB 的預算上限與積分網格由 §10.4 estimator 產出並凍結；registered 檔缺失 → EuTB 輸出拒絕（fail-closed，報告 §19.9）。

### 10.4 校準參數 pre-registration（F4）

pilot 開跑**前**，下列 estimator、合法範圍與 outlier 規則以本節文字為準寫入 `analysis/registered/estimators.yaml` 並 commit；pilot 後執行 estimator 產出數值檔，一經凍結不得依正式結果調整：

| 參數 | Estimator（在 pilot 全體 run 上計算） | 合法範圍 / 失敗規則 |
| --- | --- | --- |
| `reference_cost_i`（C_ref） | encounter i 上全部 successful clear run 的 `C_run` **中位數** | 成功數 < 3 → 該 encounter 不產 Economy 分數（只報原始成本） |
| `difficulty_scale_i`（D） | `clamp(median(成功 run 的 final diff LOC) / 40, 0.5, 4.0)` | 成功數 < 3 → 維持 1.0 並標註未校準 |
| `τ` | 全體 run 中 `F > 0` 者的 F 中位數 | 無 F>0 樣本 → τ=1.0；發布 τ×{0.5, 1, 2} 敏感度 |
| `EuTB B` | successful clear 的 `T^work` 之 **P95**，向上取整至 10k tokens | 積分網格 = [0, B] 均勻 256 點；B 與網格寫入 `eutb_budget.yaml` 含 hash |

發布時附全部參數的敏感度分析（報告 §19.5、§19.9）。

### 10.5 地端模型（F18）

MVP 不宣稱地端模型的單一 CostPerClear：

- 地端模型必須在 `models.yaml` 宣告 `cost_scenarios: {low, mid, high}`（$/hour serving rate，換算 `C_model = rate × wall_clock`），數值於 pilot 前註冊。
- Economy / CostPerClear 對地端模型輸出三情境帶，**不進單一數字排行**；token、時間、Power、Control 指標照常參與。
- 正式能耗/攤提模型（報告 §5.3）deferred。

## 11. Forced loadout 實驗執行器

- `patchmud pilot --deck pilot-v1 --models models.yaml --seed <s>`：展開 encounters × 8 loadouts × models 的 run 矩陣。
- **排程（F21）**：seed 經 PRNG 決定**完整 run 排程**——整個矩陣的全域執行順序單一隨機排列（encounter、loadout、model 三軸都被打散，非只有 block 內模型順序）。生成的 schedule 在任何 run 開跑前落盤封存（`schedule.yaml` + hash），runner 必須依序執行；provider 限流等執行期偏差記入 run log，不得改變順序。
- 所有模型共享同一 frozen SHA、同一 harness_prompt_version、同一 probe 命令。
- treatment 定義 = `(provider, snapshot, reasoning_setting, quantization, serving_stack, harness_prompt_version)`；任一欄不同即不同 treatment（報告 §13.4）。
- run registry（JSONL）支援中斷續跑：已完成 run 以 run_id 冪等跳過；重跑同 run_id 必須顯式 `--force` 並保留舊 run 目錄。
- Pilot 驗收即 Phase 4 校準：依 §10.4 執行 estimator，產出 `reference_cost`、`difficulty_scale`、τ、EuTB budget 並凍結。

## 12. Event log、封存與 replay（F2 修正後）

### 12.1 落盤

- `events.jsonl` 每行一事件：turn、action、probe 結果摘要、queue before/after、cost delta、checkpoint SHA（報告附錄 B schema 的超集）。
- transcript（模型完整輸入輸出）與 ledger 分檔落盤。
- **私有封存（archive-private）**：run 目錄 + content-addressed evaluator bundle（hidden probe bytes、reference timings、rubric、pricing snapshot、`ruff`/pytest lockfile、Python 版本與執行環境描述 digest）。這是 replay 的完整輸入。
- **公開發布（archive-public）**：同上但 hidden probe 內容以 content hash 佔位（報告 §12.4 污染控制）；公開包**不承諾** L2 重執行，只承諾 L1 重算。

### 12.2 兩級 replay

| 級別 | 內容 | 一致性保證 |
| --- | --- | --- |
| **L1 重算** | 從 events + checkpoints + 封存 probe 逐項結果，重算 queue 軌跡、Flood、Power 分數、全部 metrics；**不重新執行任何 probe** | **位元一致**；CI 驗收必跑，公開包可執行 |
| **L2 重執行** | 在 pinned 環境（lockfile + interpreter + evaluator bundle）重新執行全部 probes 後走 L1 | functional / compat / robustness probe 結果**必須相等**；perf probe 比對容忍帶（timing 本質非確定），差異落 report 不算 fail |

- Power 中 runtime_efficiency 的分數判定在**原始 run 當下**以量測值定案並封存 outcome；L1 重算引用封存 outcome，因此位元一致宣稱不被 wall-clock 非確定性破壞（F2）。
- `patchmud replay <run_id> [--l2]`：L1 輸出與封存 `result.yaml` 不一致 → exit non-zero。

## 13. Acceptance matrix

### 協定與策略

- 無法解析的回覆消耗 turn 並回 parse error；連續 3 次 → `failed:protocol`，Clear=0，hidden evaluator 仍執行。
- P1 下先 PATCH 後 PLAN → illegal；空列表 / 未涵蓋 MAIN id 的 plan → illegal（F7）。
- T1 下無 valid red 的 production PATCH → illegal；`assert False` 假 red 無法達成 `tdd_compliant`（測試檔 hash 不變前提下永不轉綠，F6）；red 後改測試檔 → red evidence 重置。
- R1 下：turn 1 空 diff review 不滿足 COMMIT gate；合格 review 後 COMMIT 才合法（F8）。
- reviewer subcall 不消耗 max_turns；turn 7 SUMMON 後作者仍有 turn 8（F9）。
- T0 下自發 red-first 被記錄為 `observed_tdd_workflow=true`，run 照常有效（F5）。

### 隔離

- probe 程式在 namespace 內對 deck `hidden/`、runs store、`HOME` 的讀取失敗（fixture 內嵌探測程式驗證，F1）。
- `sandbox_isolation: degraded` 時 ranked pilot 拒絕啟動。
- 修改 `tests/public/**` 的 PATCH 被拒；判定以 deck 原始 probe bytes 執行。
- reviewer 輸入 render 不含作者 transcript 與 hidden 資產（fixture 測試）。

### 計量

- 各 provider usage fixture → 互斥欄位 + billed totals + unallocated 單元測試；NA 不得變 0；NA 聚合傳染。
- 含 per-request 計價的 snapshot：9 次呼叫的 run，`C_model` 差恰為 `9 × per_request`（F16）。
- 不同日期 snapshot 重算，封存 run 結果不變（snapshot pin 生效）。
- reference_cost null → Economy NA；EuTB registered 檔缺失 → 輸出拒絕；estimators.yaml 未 commit → pilot 拒絕啟動（F4）。
- 地端模型無 cost_scenarios → 拒絕啟動；有 → Economy 輸出三情境帶（F18）。
- 零 clear 模型：CostPerClear / TokensPerClear 輸出 `inf`，Economy=0，不從分析剔除。
- 跨 disclosure cohort 的 EuTB 排名輸出被拒絕；common-observable 欄位帶 non-ranking 標註（F17）。

### Flooding 與 Clear

- turn 0 baseline 存在：第一個 patch 打紅 compat probe 即 spawn REGRESSION（F11）。
- MAIN resolved 後再紅 → REOPENED；宣告修復但仍紅 → `failed_claim_count` 遞增。
- 完美 P1 / T1 run（無自生債務）的 `FloodArea_excess = 0`，Control 不因流程回合被扣（F13）。
- F14 情境（1k token 造 flood、9k token 修 flood）：FTR = 0.9（start-of-turn 語意），且 create/repair 分欄正確。
- agent 從不 RUN_TEST 直接 COMMIT：引擎終局自動跑全套 public + hidden，Clear 依 §5.2 唯一公式判定（F3）。
- replay L1 重算的 FloodArea / Control / Power 與封存值位元一致；perf probe 不參與 L1 重執行（F2）。

### Pilot

- 8 encounters × 8 loadouts × 2 models 的 dry-run 矩陣可跑完並產出 report；schedule.yaml 先於首 run 存在且 hash 相符（F21）；中斷後續跑冪等。

## 14. Deferred workstreams（啟動條件）

| Workstream | 為何不在 MVP | 啟動條件 |
| --- | --- | --- |
| Autonomous Draft + selection regret | 需要 forced loadout 校準的 per-issue 最佳 loadout 當 baseline | Phase 4 pilot 資料齊 |
| Fixed-reviewer / cross-model party track（RQ5） | self-team 矩陣中 reviewer 與作者能力完全共變（F19） | 多模型預算到位，加入正交 reviewer cell |
| Probe-backed REVIEW-DEBT 進 Control | LLM finding 進 ranked 指標污染確定性（F10） | 有 executable-probe-backed finding 機制 |
| RQ6 預測穩定性 / RQ7 條件控制分析 | 需要 pre-registered 統計方法與 out-of-sample 設計（F20） | pilot 後另行 pre-register |
| Mutation / property 擴充、人工 audit | hidden probe 覆蓋先驗證 | 首次 pilot 顯示 hidden tests 區分度不足 |
| 地端能耗成本模型 | 無標準化量測管線；MVP 用情境帶（§10.5） | 地端模型進入正式比較 |
| 容器級 sandbox（gVisor 等） | bubblewrap namespace 對 MVP fixture 足夠 | deck 引入不可信第三方 fixture |
| Capability-normalized track（固定 token budget） | end-to-end track 先行 | EuTB registered budget 凍結後 |
| RPG Exhibition Mode | 與研究效度無關 | 研究版穩定後另案 |

## 15. RQ ↔ 資料 traceability

| RQ | 狀態 | 主要欄位 / 指標 |
| --- | --- | --- |
| RQ1 | MVP 支援（API 模型）；地端為情境帶 | PricingSnapshot、CostPerClear、cost_scenarios |
| RQ2 | MVP 支援（estimand = enforcement uplift, ITT；crossover 率照報） | loadout × archetype × 成本 / clear、observed_tdd_workflow |
| RQ3 | MVP 支援 | FloodArea_excess/total、FloodIndex、FTR ↔ CostPerClear |
| RQ4 | deferred（Phase 5） | 欄位保留：strategy_switches |
| RQ5 | **deferred**（F19；self-team 無法識別補償效應） | 欄位就緒：reviewer_finding_precision、author_fix_conversion、C_reviewer |
| RQ6 | 資料就緒，統計方法 deferred（F20） | Economy / Power / Control 原始分佈 + bootstrap CI |
| RQ7 | 資料就緒，條件控制分析 deferred（F20） | TokensPerClear、QATY、EuTB、MTY、disclosure cohort 欄位 |

## 16. 合規

- 本 spec 與後續 plan 文件落在 `paulsha-cortex` docs（研究資產）；實作程式碼落在新 repo `paulsha-patchmud`（★ 待確認），該 repo 以 `paulsha-conventions` 最新 policy scaffold。
- 兩 repo 均為 `tier: shareable`：fixture、路徑、報告不得含個人絕對路徑或機敏標記。
- deck fixture 的 reference patch 與 hidden 資產不隨 ranked 結果公開；公開發布走 archive-public（§12.1）。
- 分支 `feature/<slug>`；code PR 同步 CHANGELOG；`python3 -m policy_check --repo .` 零 fail。

## 17. 一句話

PatchMUD MVP 不是把 benchmark 套上遊戲皮：它是一台 deterministic 的回合制評測引擎——hidden 資產以建構排除加 namespace 隔離雙重保證拿不到、queue 與 Clear 由唯一布林公式和 probe 結果決定、成本以 billed totals 與版本化價目可稽核、校準參數先註冊 estimator 再看資料、每場 run 以兩級 replay 可驗證——先把 Phase 1–4 的地基打實，才讓 Phase 5/6 的自主策略與賽季有可信的比較基準。

---

## 附錄 D：對抗審查記錄（2026-07-16）

- 審查者：codex CLI（model `gpt-5.6-sol`, reasoning effort xhigh），read-only adversarial review。
- 結果：21 條 findings（4 blocker / 17 major），**全數成立**，v1.1 全數處理。
- 直接採納：F1（mount namespace）、F2（兩級 replay + perf outcome 封存）、F3（Clear 唯一公式 + turn 0 baseline + 終局全套 probe）、F4（estimator pre-registration）、F6（red evidence hash + green 閉環）、F7（PLAN schema）、F8（合格 review 定義）、F9（ReviewerSubcall 分型）、F11（baseline + failed_claim 計數器）、F12（expected_paths 兩級 SCOPE）、F14（start-of-turn FTR + create/repair 分欄）、F15（rubric 公式 pin）、F16（per-request 計價）、F17（billed totals + disclosure cohort）、F21（全域排程封存）。
- 採納但改方案：
  - F5：不禁止 T0 red-first（避免扭曲 SOLO 真實性），改為 ITT estimand「enforcement uplift」+ crossover 記錄。
  - F10：codex 建議 probe-backed finding 才進 queue；因 public probe 已由引擎每回合自動執行，probe-backed debt 必然與 MAIN/REGRESSION 重複，故更進一步——MVP 將 reviewer finding 全面移出 ranked Control（明文偏離報告 §7.2/§7.4），效度指標轉 post-hoc。
  - F13：不改報告的 FloodArea 定義，改為雙指標（total 照報告發布、excess 進 Control）並記錄偏離理由。
  - F18：MVP 不實作能耗模型，改預註冊成本情境帶且退出單一數字排名。
  - F19/F20：對應 RQ 降級為 deferred / 資料就緒，不做 overclaim。
