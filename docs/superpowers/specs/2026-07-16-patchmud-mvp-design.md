# PatchMUD MVP implementation spec（2026-07-16）

> 狀態：v1 implementation spec 草案。來源為 `docs/PatchMUD_research_report_zh-TW_v0.2.md`（研究設計 v0.2）。
> 本 spec 把研究設計轉成可實作、可驗收的軟體規格；範圍鎖定報告 §20 的 Phase 1–4（離線評分核心、回合引擎與四策略、Issue Flooding、Forced Loadout Pilot）。
> Phase 5（Autonomous Draft）、Phase 6（正式賽季）、RPG Exhibition Mode 一律列為 deferred workstream，只保留資料欄位相容性。

## 0. 決策摘要（需使用者確認的項目以 ★ 標記）

| 決策 | 選擇 | 理由 |
| --- | --- | --- |
| ★ 落地位置 | 新獨立 repo `paulsha-patchmud`，Python package `patchmud`，對 `paulsha-cortex` / `paulsha-hippo` 零 runtime 依賴 | 符合生態系拆包哲學；cortex 定位是治理平面，不該吞下 benchmark 引擎。spec / plan 文件留在 cortex `docs/superpowers/`（研究報告所在地），實作計劃 Task 0 負責 scaffold 新 repo |
| MVP 範圍 | 報告 Phase 1–4 | Phase 5/6 依賴 Phase 1–4 的校準產物，先做會空轉 |
| 技術選型 | Python 3.11+、pytest、YAML deck、JSONL event log、git worktree sandbox | 與生態系一致；deck fixture 全為 Python 專案 |
| 裁判原則 | 排名資料流零 LLM 裁判；reviewer 產物只進 queue 與 debt 計數，不進 Power | 報告 §3「所有關鍵結果均由 artifact、測試、版本差異與可稽核事件日誌決定」 |
| 網路隔離 | 測試與 sandbox 命令以 `unshare -rn` 包裹；ranked run 強制、dev run 可降級並記錄 | WSL2/Linux 可用；無 namespace 時 fail-closed 不產 ranked 資料 |

## 1. 目標與非目標

### 1.1 目標

實作一套可重現的回合制 coding-agent 評測引擎，能：

1. 以 frozen repo + issue card 執行 encounter，記錄完整 event log 並可離線重播。
2. 強制執行 SOLO / PLAN / TDD / REVIEWER 四策略與其 compliance 規則（報告 §8）。
3. 以 deterministic 規則維護 dynamic issue queue，量測 Issue Flooding（報告 §7）。
4. 以互斥 token ledger 與版本化計價快照計算 run 成本（報告 §5）。
5. 在 COMMIT / 截止後執行 hidden evaluator，產出 Power、hard gates 與 Control / Economy 分數（報告 §6、§7、§11）。
6. 執行 forced loadout pilot（4 母題 × 2 變體 × 8 loadouts × N 模型）並輸出研究指標（報告 §13、§15）。

### 1.2 非目標（MVP 不做）

- Autonomous Draft、selection regret、策略自主切換（Phase 5）。引擎仍記錄 `strategy_switches` 欄位，但 MVP 的 forced loadout 不允許切換。
- 正式賽季、fixed-reviewer track、cross-model party track（Phase 6）。MVP 只做 self-team reviewer（作者模型、fresh context）。
- RPG Exhibition Mode 的任何遊戲機制。
- 地端模型能耗 / 硬體攤提計算器。ledger 保留 `gpu_seconds` / `energy_kwh` 欄位，值可為 `NA`。
- Mutation testing 與 curated mutation score。`mutation_score` 欄位保留為 `NA`。
- 階層式貝氏三維能力估計（報告 §15.3）。MVP 產出原始指標與 bootstrap CI，posterior 模型留給分析階段。
- 任何形式的 leaderboard 網站 / TUI 前端。MVP 的 frontend 是 CLI 與純文字 render。

## 2. 系統架構

對應報告 §17.1，落成五個子套件與一個 CLI：

```text
patchmud/
  deck/        # issue cards、fixture repo 物化、變體與 provenance
  engine/      # 回合迴圈、命令解析、strategy enforcer、issue queue、flood tracker
  sandbox/     # worktree 物化、patch 套用、public probe 執行、隔離
  ledger/      # token ledger、pricing snapshots、cost model
  evaluator/   # hidden tests、power rubric、hard gates（獨立 checkout，永不進 sandbox）
  metrics/     # CostPerClear、TokensPerClear、QATY、EuTB、MTY、FTR、Flood/Control/Economy
  adapters/    # anthropic、openai-compatible（涵蓋地端 vllm/ollama）
  store/       # run 目錄、events.jsonl、transcript、checkpoints、replay
  cli.py       # patchmud run / pilot / replay / report / validate-deck
```

硬性邊界：

- `evaluator/` 讀取的 hidden 資產（hidden tests、reference notes、rubric 權重）只存在於 deck 的 `hidden/` 目錄，**永不物化進 sandbox worktree**。agent 拿不到，因此「存取 hidden tests → run 無效」由建構保證，不靠事後偵測。
- `engine/` 只透過 `sandbox/` 的介面觸碰檔案系統；只透過 `adapters/` 觸碰模型。
- `metrics/` 只讀 `store/`，不參與 run 中決策，保證評分可離線重算。

## 3. Domain model

| 名詞 | 定義 | 持久位置 |
| --- | --- | --- |
| `Encounter` | 一張 issue card + frozen fixture repo + hidden 資產 | `decks/<deck>/<encounter_id>/` |
| `Run` | 一個 (model, harness, loadout, encounter) 的一場對局 | `runs/<run_id>/run.yaml` |
| `Turn` | 一次 agent 呼叫 + 引擎執行結果；`max_turns` 計 agent 呼叫次數 | `events.jsonl` |
| `Action` | agent 回覆解析出的結構化動作（§5 命令集） | `events.jsonl` |
| `IssueItem` | queue 中一項，type ∈ {MAIN, REOPENED, REGRESSION, SCOPE, REVIEW-DEBT, CHURN, DUPLICATE} | `events.jsonl`（每回合 snapshot） |
| `Probe` | 可執行的公開診斷（public test 群、smoke、compat check、lint）；IssueItem 的 resolved 條件一律綁 probe 或 deterministic 計算 | deck 宣告 |
| `TokenLedgerEntry` | 一次模型呼叫的互斥 token 欄位 + 計價快照 ref | `ledger.jsonl` |
| `PricingSnapshot` | `(provider, model, date)` 的每百萬 token 價目 | `pricing/<provider>/<date>.yaml`，內容 hash 記入 run |
| `PlanArtifact` | PLAY PLAN 提交的 YAML，提交後凍結 | `runs/<run_id>/artifacts/plan.yaml` |
| `ReviewerFinding` | reviewer 回傳的結構化 finding（上限 5 筆/次） | `runs/<run_id>/artifacts/review_<n>.yaml` |
| `Checkpoint` | 每回合結束時 sandbox worktree 的 shadow commit | `runs/<run_id>/checkpoints/`（bare repo） |
| `PowerReport` | hidden evaluator 輸出的分項分數與 hard-gate 判定 | `runs/<run_id>/result.yaml` |
| `RunMetrics` | run 級與跨 run 聚合指標 | `result.yaml` + `report/` 輸出 |

所有持久化 artifact 都帶 `schema_version`。event log 為 append-only；重播時任何 schema 不符即 fail-closed 拒絕重算，不做靜默 migration。

## 4. Issue Deck 契約

### 4.1 目錄佈局

```text
decks/pilot-v1/<encounter_id>/
  card.yaml            # issue card（下）
  repo/                # fixture 專案原始碼（含 public spec、public/starter tests）
  hidden/              # hidden tests、property tests、perf tests、rubric 權重
  provenance.yaml      # 母題來源、公開時間、變體差異描述、封存時間
```

engine 在 run setup 時把 `repo/` 物化成 git repo：固定 author、固定 timestamp（epoch 0）、單一 initial commit，因此 frozen SHA 對同一 deck 版本恆定，寫入 `run.yaml`。

### 4.2 card.yaml（在報告 §12.1 之上補齊引擎需要的欄位）

```yaml
schema_version: 1
issue_id: phantom-removal-v1
archetype: state-recovery
difficulty: medium
difficulty_scale: 1.0          # D_issue；pilot 期間全部 1.0，Phase 4 校準後更新
expected_patch_loc: [30, 80]
wall_clock_seconds: 600
max_turns: 8
allowed_paths:                 # production patch 允許觸及的 glob；之外 → SCOPE
  - "src/**"
  - "tests/agent/**"
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
  - hidden: false
    probe: tests/public/test_api_surface.py
power_rubric:                  # §9 的分項權重與 probe 對應，全部可執行
  functional: {points: 60, groups: [{id: CR-1, points: 40, probe: hidden/test_cr1.py}, ...]}
  robustness: {points: 15, probes: [hidden/test_edges.py]}
  compatibility: {points: 10, probes: [tests/public/test_api_surface.py, hidden/test_compat.py]}
  maintainability: {points: 10}   # deterministic proxy，見 §9.3
  runtime_efficiency: {points: 5, probes: [hidden/test_perf.py], timeout_factor: 3.0}
reference_cost: null           # C_ref；pilot 校準後填入
```

`patchmud validate-deck` 驗證：schema、probe 檔案存在、public/hidden 不重疊、`repo/` 可安裝可跑 starter tests、hidden probes 在 reference patch 下全綠（deck 作者提供 reference patch 於 `hidden/reference.patch`，僅供 deck CI 驗證，永不進 run）。

### 4.3 Pilot deck 規模

4 個母題 × 2 個語意變體 = 8 encounters。母題取自報告 §12.2 類型（輸入驗證、parser edge、狀態機 recovery、legacy regression 各一）。變體修改邊界規則 / 錯誤契約 / 常數，不只改名（報告 §12.4）。`provenance.yaml` 記錄母題公開時間與封存時間。

## 5. 回合協定

### 5.1 命令集與語意

沿用報告 §9.2 的命令面，逐一 pin 語意：

| 命令 | 語意 | 失敗語意 |
| --- | --- | --- |
| `LOOK` | 回傳 repo 樹（深度受限）、issue queue render、資源狀態 | — |
| `INSPECT <path>` | 回傳 sandbox 內檔案內容（大小上限 64KB，超過截斷並標示）；path 必須在 sandbox 內 | sandbox 外 / 不存在 → error result |
| `PLAY PLAN` | 提交 plan YAML，schema 驗證後凍結；只能在第一次 `PATCH` 前 | 重複提交 / 已 PATCH 後提交 → illegal |
| `PLAY TDD` | 啟動 test-first 約束（forced T1 由引擎在 turn 0 自動啟動） | — |
| `WRITE_TEST` | 提交 test-only diff；只能觸及 `tests/agent/**` 新增或修改 agent 自己的測試 | 觸及 production / 既有測試檔 → illegal |
| `PATCH` | 提交 production unified diff；`git apply` 嚴格模式（無 fuzz、無 3way） | apply 失敗 → error result，不改變 worktree |
| `RUN_TEST [target]` | 執行 public probes 或 `tests/agent/**` 內 agent 自建測試（全部或指定）；輸出摘要與 fail 詳情 | 非白名單 target → illegal |
| `SUMMON REVIEWER` | 啟動 fresh-context reviewer（§8.4） | R0 loadout 中 → illegal |
| `TRIAGE` | 關閉 DUPLICATE、標記 wont-fix、重排 queue render 順序 | — |
| `ROLLBACK` | 撤回最近一個成功套用的 patch（引擎維護 patch stack） | 無可撤回 → error result |
| `COMMIT` | 終局；以當下 worktree 對 frozen base 的 cumulative diff 為 final artifact | — |

### 5.2 回合與失敗規則

- **每一次 agent 呼叫恰好消耗一個 turn**，不論動作合法與否——token 已真實花費，成本模型必須反映（報告 §5.2 的精神）。
- 回覆無法解析成任何命令 → 引擎回傳結構化 parse error（附格式提示），消耗一 turn。
- 連續 3 次 invalid / illegal action → run 以 `failed:protocol` 終局，仍跑 hidden evaluator 記錄 Power，但 `Clear = 0`。
- `max_turns` 用盡或 `wall_clock_seconds` 到期 → 強制終局，等同對當下 worktree 執行 `COMMIT`。
- agent 回覆格式沿用報告 §9.3（`ACTION:` / `TARGET_ISSUES:` / `FILES:` / `CLAIM:` / `PATCH:` 區塊）；不要求也不記錄私密 chain-of-thought。

### 5.3 harness prompt

system prompt 與狀態 render 模板版本化為 `harness_prompt_version`，寫入 run.yaml。每 turn 傳送：系統規則 + issue card 公開部分 + 累積對話 transcript + 當回合狀態 render。完整 transcript 落盤，token 用量按 adapter 回報計量（含 cache 欄位）。

## 6. Strategy enforcer

Forced loadout 下，引擎在 run 開始時載入 `(P, T, R)` 三個 bit 並強制執行：

| Factor | =1 時強制 | =0 時禁止 |
| --- | --- | --- |
| P（PLAN） | 第一次 `PATCH` 前必須有通過 schema 驗證的 `PLAY PLAN`；違反 → illegal action | `PLAY PLAN` → illegal |
| T（TDD） | 第一次 production `PATCH` 前必須達成 valid red（§6.1） | `PLAY TDD` → illegal（MVP forced loadout 不允許策略切換）；`WRITE_TEST` 仍允許（寫測試不是 TDD 專利），但不強制 red-first |
| R（REVIEWER） | `COMMIT` 前必須至少一次 `SUMMON REVIEWER`；未滿足時 `COMMIT` → illegal | `SUMMON REVIEWER` → illegal |

SOLO = P0T0R0，無任何額外限制與 bonus（報告 §8.1）。

### 6.1 TDD valid red 判定

1. `WRITE_TEST` 產生的新測試檔在 `tests/agent/**`。
2. 隨後的 `RUN_TEST` 中，至少一個新增測試以 pytest **`failed`**（assertion / 行為失敗）結束——`error`（collection、import、syntax）不算 valid red。
3. valid red 未達成前，production `PATCH` → illegal action。
4. 事後補測試（先 PATCH 後補綠測試）在 T1 下被規則 3 排除；在 T0 下允許但 `tdd_compliant` 記為 false。

### 6.2 REVIEWER 協定

- reviewer 使用與作者相同 model snapshot、全新 context（self-team track）。
- 輸入嚴格限定：issue card 公開部分、當下 cumulative diff、public probe 最新結果、作者可見 artifacts（plan、claim 歷史）。不含作者 transcript、不含 hidden 資產。
- 輸出 schema：最多 5 筆 finding，每筆 `{category, severity, summary, evidence: [{path, line}], suggested_probe?}`。schema 驗證失敗 → 該次 review 記為 `invalid`，成本照計，不產 REVIEW-DEBT item。
- reviewer 的全部 token 計入 `C_reviewer`（報告 §8.4）。

## 7. Sandbox executor

- 每 run 一個獨立 worktree，從 frozen commit 物化；`.git` 對 agent 命令不可見（INSPECT 黑名單）。
- patch 套用：`git apply --check` 先驗，嚴格模式；路徑必須 repo-relative 且不落在 `.git` / probe 保護區。
- **probe 保護區**：`tests/public/**`、`tests/starter/**`、`benchmark/**` 對 `PATCH` / `WRITE_TEST` 唯讀。agent 對 public tests 的任何修改被拒收——回歸與 requirement 判定一律以 deck 原始 probe bytes 執行（每次執行前從 deck overlay 還原），杜絕改測試過關。
- 測試執行：`unshare -rn`（user+net namespace）包裹的 subprocess，sanitized env（只保留 `PATH/HOME/LANG/LC_ALL/TMPDIR/VIRTUAL_ENV`），per-probe timeout。namespace 不可用時：run 標記 `network_isolation: none`，ranked pilot 拒絕啟動（fail-closed），dev run 允許。
- 每回合結束把 worktree commit 進 shadow bare repo（checkpoint），供離線 MTY 重播與 replay 驗證。
- 資源記錄：每 probe 的 wall-clock、CPU time（`resource.getrusage` of children）；MVP 計價為 0 但欄位落盤（報告 §5.2 的 C_sandbox 保留項）。

## 8. Issue queue 與 flooding

### 8.1 Deterministic 觸發規則（報告 §7.2 的可執行化）

| Type | Spawn 規則 | Resolve 規則 |
| --- | --- | --- |
| `MAIN` | run 開始時，每個 `public_requirements[]` 一項 | 綁定 probe 全綠 |
| `REOPENED` | 某 MAIN item 曾 resolved，其 probe 後續回合再紅 → 原 item 關閉、spawn REOPENED（計數 ×N） | probe 再綠 |
| `REGRESSION` | `regression_probes` / `compat_probes` 中任一從綠轉紅（以上回合結果為基準） | 該 probe 再綠 |
| `SCOPE` | cumulative diff 觸及 `allowed_paths` 外的 production 檔案；聚合為單一 item，附檔案清單 | 越界變更全數撤回 |
| `REVIEW-DEBT` | 每筆 valid reviewer finding 一項 | 後續 patch 的 hunk 範圍與 finding evidence `path:line` 相交，或 TRIAGE 標記 wont-fix（標記後 item 關閉但記入 `dismissed_findings`） |
| `CHURN` | 單回合 revert 掉 ≥ 10 行（card 可調）自己先前新增的行（以 patch stack diff 計算 `reverted_loc`） | 不 resolve；下一回合自動關閉（事件性 item） |
| `DUPLICATE` | `PATCH` 的 `TARGET_ISSUES` 引用已 resolved / 已關閉 item | TRIAGE 關閉 |

規則只依 probe 結果、diff 幾何與宣告欄位運作，**queue 更新零 LLM 參與**。probe 執行時機：每次 `PATCH` / `WRITE_TEST` / `RUN_TEST` / `ROLLBACK` 成功後，引擎自動跑 regression + 已 resolved requirement probes（增量），更新 queue（報告 §9.4 步驟 4）。

### 8.2 Flood 計量

- `B_t` = 回合 t 結束時 open items 數；`FloodArea = Σ B_t`（Δt = 1 turn；wall-clock 加權版平行落盤）。
- Flood Index `F` 依報告 §7.4 公式，係數為 deck 級常數檔（版本化）；`D_issue = difficulty_scale`。
- `Control = 100 × exp(−F/τ)`，τ 初值 1.0，Phase 4 校準後凍結進 `analysis/registered/`。
- Flood 狀態門檻（Stable/Noisy/Flooded/Meltdown）只影響 render 文案，不進任何分數（報告 §7.5）。

## 9. Hidden evaluator 與 Power

### 9.1 執行時機與隔離

只在終局（COMMIT / 截止 / protocol fail）後執行：evaluator 在**獨立 checkout**（frozen base + final diff）套 hidden probes；sandbox worktree 從頭到尾不含 hidden 資產。中間 checkpoint 的 hidden 評分只允許離線重播（MTY 用），嚴禁回饋同場 run（報告 §5.6.5、§10.2）。

### 9.2 Hard gates（報告 §6.1 逐條落地)

| Gate | 判定 | 效果 |
| --- | --- | --- |
| critical requirements 未全過 | 任一 `critical_requirements[].hidden_probe` 紅 | `Clear=0`、`Economy=0`、Utility ≤ 49 |
| 無法 import / compile / 起測試 | evaluator 的 collection 階段失敗 | Power ≤ 15 |
| 公開 API 不相容變更 | `compat_probes` 任一紅 | Power ≤ 50 |
| 存取 hidden 資產 | 由建構排除；INSPECT 越界嘗試落 event log 供稽核 | —（不可能達成） |

### 9.3 Power rubric（100 分，全 deterministic）

- functional 60：`power_rubric.functional.groups` 各 group probe 全綠得該組分數。
- robustness 15：hidden edge/property probes，按通過比例線性計分。
- compatibility 10：compat probes 全綠 10、任一紅 0（與 hard gate cap 疊加取低）。
- maintainability 10：4 分 diff 大小（|final diff LOC| 落在 `expected_patch_loc` 內滿分，超出按比例遞減至 0）、3 分 scope（無 SCOPE item 殘留）、3 分 lint（`ruff check` 對 changed files 零新增 error）。
- runtime_efficiency 5：perf probes 在 `timeout_factor ×` reference 時限內通過。

### 9.4 重複懲罰隔離（報告 §6.2）

- 終局仍存在的 regression → 扣 Power（compat/functional probes 紅）。
- 中途發生、終局已修復的 regression → 只進 Control（FloodArea、N_regression）與 Economy（真實 token / 成本），不扣 Power。
- 額外呼叫與測試 → 只依 ledger 真實支出進 Economy。

## 10. Token ledger 與成本模型

### 10.1 互斥 ledger（報告 §5.6.1 逐條落地）

每次 adapter 呼叫落一筆：`{turn, role: author|reviewer, input_uncached, input_cached, output_visible, reasoning, api_calls: 1, wall_clock_ms, prompt_bytes, generated_bytes, pricing_snapshot_ref}`。

- adapter 負責把 provider usage 映射為互斥欄位；`cached ⊆ input` 時先拆 uncached；reasoning 含在 output 時先扣除。
- provider 不揭露 reasoning → 記 `NA`，不得記 0；該 run 只參與 observable-token 指標，`T^work` 聚合時 NA 傳染（任何含 NA 的聚合輸出 NA + observable 版本雙欄）。
- mapping 有 per-provider 單元測試釘死（給定 usage fixture → 期望互斥欄位）。

### 10.2 成本

- `C_run = C_model + C_reviewer`（MVP）；`C_sandbox`、`C_infra`、`λ_t T_wall` 欄位落盤、計價 0，敏感度分析在 metrics 層以參數重算（報告 §5.2 完整式保留）。
- `C_model` 依報告 §5.2 公式以 run 內 pin 的 PricingSnapshot 計算；snapshot 檔案 hash 寫入 run.yaml，價格改動不影響已封存 run。
- `CostPerClear`、`Economy_i` 轉換依報告 §5.4、§5.5；`reference_cost` 未校準（null）時 Economy 分數輸出 `NA`，只報原始成本——**pilot 前不產 0–100 Economy 分數**。

### 10.3 效率稽核指標

`TokensPerClear`、`QATY`、`EuTB`、`MTY`、`FTR` 依報告 §5.6.2–5.6.6 公式在 `metrics/` 實作，全部另附 encounter-level bootstrap CI（B=10,000, seed 記錄）。EuTB 的預算上限 B 與積分網格由 pilot 產出後寫入 `analysis/registered/eutb_budget.yaml`（含 hash）凍結；正式 run 前該檔不存在 → EuTB 輸出拒絕（fail-closed，報告 §19.9）。

## 11. Forced loadout 實驗執行器

- `patchmud pilot --deck pilot-v1 --models models.yaml --seed <s>`：展開 encounters × 8 loadouts × models 的 run 矩陣。
- Block randomization：以 (encounter, loadout) 為 block，模型執行順序隨機、seed 落盤（報告 §13.4）。
- 所有模型共享同一 frozen SHA、同一 harness_prompt_version、同一 probe 命令。
- treatment 定義 = `(provider, snapshot, reasoning_setting, quantization, serving_stack, harness_prompt_version)`；任一欄不同即不同 treatment（報告 §13.4）。
- run registry（JSONL）支援中斷續跑：已完成 run 以 run_id 冪等跳過；重跑同 run_id 必須顯式 `--force` 並保留舊 run 目錄。
- Pilot 驗收即 Phase 4 校準：產出 `reference_cost`、`difficulty_scale`、τ、EuTB budget，寫入 deck / registered 檔案後凍結。

## 12. Event log 與 replay

- `events.jsonl` 每行一事件：turn、action、probe 結果摘要、queue before/after、cost delta、checkpoint SHA（報告附錄 B schema 的超集）。
- `patchmud replay <run_id>`：從 events + checkpoints 重算 queue 軌跡、Flood、Power（重跑 evaluator）、全部 metrics；輸出必須與封存 `result.yaml` 位元一致，否則 exit non-zero。replay 是 CI 驗收的一部分。
- transcript（模型完整輸入輸出）與 ledger 分檔落盤；run 目錄整體可 tar 發布（報告 §21「可重播 logs」）。

## 13. Acceptance matrix

### 協定與策略

- 無法解析的回覆消耗 turn 並回 parse error；連續 3 次 → `failed:protocol`，Clear=0，hidden evaluator 仍執行。
- P1 下先 PATCH 後 PLAN → illegal；T1 下無 valid red 的 production PATCH → illegal；R1 下未 review 就 COMMIT → illegal。
- import error 的測試不構成 valid red；T0 下事後補測試 `tdd_compliant=false`。

### 隔離

- sandbox worktree 內任何路徑 find 不到 hidden 資產（結構測試）。
- 修改 `tests/public/**` 的 PATCH 被拒；requirement / regression 判定以 deck 原始 probe bytes 執行。
- `network_isolation: none` 時 ranked pilot 拒絕啟動。
- reviewer 輸入 render 不含作者 transcript 與 hidden 資產（fixture 測試）。

### 計量

- 各 provider usage fixture → 互斥 ledger 欄位單元測試；NA 不得變 0；NA 聚合傳染。
- 同一 run 以不同日期 PricingSnapshot 重算成本，封存 run 結果不變（snapshot pin 生效）。
- reference_cost 為 null 時 Economy 輸出 NA；EuTB registered 檔缺失時輸出拒絕。
- 全部失敗（零 clear）的模型：CostPerClear / TokensPerClear 輸出 `inf`，Economy=0，不從分析剔除。

### Flooding

- probe 綠→紅→綠序列產生 REGRESSION item 並 resolve；MAIN resolved 後再紅產生 REOPENED。
- 越界檔案觸發 SCOPE；撤回後 resolve。
- 中途修復的 regression 不扣 Power，但 FloodArea > 0。
- replay 重算的 FloodArea / Control 與封存值位元一致。

### Pilot

- 8 encounters × 8 loadouts × 2 models 的 dry-run 矩陣可跑完並產出 report；中斷後續跑冪等。

## 14. Deferred workstreams（啟動條件）

| Workstream | 為何不在 MVP | 啟動條件 |
| --- | --- | --- |
| Autonomous Draft + selection regret | 需要 forced loadout 校準的 per-issue 最佳 loadout 當 baseline | Phase 4 pilot 資料齊 |
| Fixed-reviewer / cross-model party track | 需要 self-team baseline 與多模型預算 | RQ5 進入分析階段 |
| Mutation / property 擴充、人工 audit | hidden probe 覆蓋先驗證 | 首次 pilot 顯示 hidden tests 區分度不足 |
| 地端能耗成本模型 | 無標準化量測管線 | 地端模型進入正式比較 |
| 容器級 sandbox（gVisor 等） | unshare 對 MVP fixture 足夠 | deck 引入不可信第三方 fixture |
| Capability-normalized track（固定 token budget） | end-to-end track 先行 | EuTB registered budget 凍結後 |
| RPG Exhibition Mode | 與研究效度無關 | 研究版穩定後另案 |

## 15. RQ ↔ 資料 traceability（摘要）

| RQ | 主要欄位 / 指標 |
| --- | --- |
| RQ1 | PricingSnapshot、CostPerClear |
| RQ2 | loadout × archetype × 成本 / clear（forced 矩陣） |
| RQ3 | FloodArea、FloodIndex、FTR ↔ CostPerClear 中介分析 |
| RQ4 | （deferred，欄位保留：strategy_switches、selection regret） |
| RQ5 | reviewer finding precision、author fix conversion、C_reviewer（MVP 只有 self-team） |
| RQ6 | Economy / Power / Control 原始分佈 + bootstrap CI |
| RQ7 | TokensPerClear、QATY、EuTB、MTY（控制 Power 與 clear 後） |

## 16. 合規

- 本 spec 與後續 plan 文件落在 `paulsha-cortex` docs（研究資產）；實作程式碼落在新 repo `paulsha-patchmud`（★ 待確認），該 repo 以 `paulsha-conventions` 最新 policy scaffold。
- 兩 repo 均為 `tier: shareable`：fixture、路徑、報告不得含個人絕對路徑或機敏標記。
- deck fixture 的 reference patch 與 hidden 資產不隨 ranked 結果公開；發布 run log 時 hidden probe 內容以 hash 佔位。
- 分支 `feature/<slug>`；code PR 同步 CHANGELOG；`python3 -m policy_check --repo .` 零 fail。

## 17. 一句話

PatchMUD MVP 不是把 benchmark 套上遊戲皮：它是一台 deterministic 的回合制評測引擎——hidden 資產從建構上就拿不到、queue 更新零 LLM 裁判、成本以互斥 ledger 與版本化價目可稽核、每場 run 可位元一致地重播——先把 Phase 1–4 的地基打實，才讓 Phase 5/6 的自主策略與賽季有可信的比較基準。
