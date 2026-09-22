# paulsha-patchmud

**PatchMUD**：成本優先的純文字回合制 coding-agent 評測框架——以軟體 issue 為關卡、程式代理模型為玩家、patch 為攻擊動作的 MUD 式評測引擎。

## 定位

- 量測閉環（laboratory），不是生產閉環：frozen fixture、deterministic 評分、可位元重播。
- 對 `paulsha-cortex` / `paulsha-hippo` 零 runtime 依賴；評測輸出以檔案契約供下游（如 model roster/routing）使用。
- 舊 ranked 模式採 deterministic 裁判；`engineering-v1` 另以固定版本 JEV 評估工程工作品質，依 artifact、測試、版本差異與事件作證據。兩種分數有獨立語義。
- 三維角色能力：經濟（Economy 55%）、火力（Power 25%）、控場（Control 20%），另設不重複計分的 token-efficiency 稽核指標。

研究設計、implementation spec 與實作計劃見 `docs/`：

- 研究報告：`docs/PatchMUD_research_report_zh-TW_v0.2.md`
- Spec（v1.1，經 codex gpt-5.6-sol 對抗審查）：`docs/superpowers/specs/2026-07-16-patchmud-mvp-design.md`
- 實作計劃（21 tasks）：`docs/superpowers/plans/2026-07-16-patchmud-mvp.md`
- 跨 provider adapter 設計（codex / agy OAuth headless）：`docs/superpowers/specs/2026-08-11-multi-provider-adapters-design.md`
- 玩家與旁觀者指南：[`docs/user-manual.md`](docs/user-manual.md)

## Install

```bash
python -m pip install -e ".[test]"
```

執行 candidate code 的隔離層需要 [bubblewrap](https://github.com/containers/bubblewrap)（`bwrap`）；無 namespace 能力時 ranked / pilot run 會 fail-closed 拒絕啟動，dev run 以 degraded 模式執行。

## Usage

### 工程模型評分（JEV）

先在執行環境設定 `TYPESAFE_API_KEY`，並完成 Codex／agy 各自的登入。不要將憑證寫入題庫、命令紀錄或報告。

工程評分現在採原生 coding-agent 契約：Codex 與 agy 在 `IsolationRunner` 內執行各自 CLI，模型可使用該 CLI 自己的工具。JEV 憑證只留在評測控制端，不會傳入受測 CLI；provider 認證則以隔離的 auth-only home 提供（Codex `.codex`、agy `.gemini`）。每題由 `jev-1.13.0` 按四個維度各自判決，完整封存保留。native CLI、namespace、JEV 校準與正式評分的實際結果見 [驗證紀錄](docs/jev-validation.md)。

```bash
paulsha-patchmud --list-cases
paulsha-patchmud --target --harness codex --model gpt-5.6-luna --effort max
paulsha-patchmud --base --harness agy --model gemini-3.8-flash --effort high \
  --target --harness codex --model gpt-5.6-luna --effort max
```

預設題庫涵蓋修正、診斷、範圍遵守、測試設計、失敗恢復與成果查核，六類各三題；三種深度只有 600／1200／1800 秒 wall budget，沒有 portable turn cap。含 staged requirement 的題目在同一 native conversation 中依序送出各 phase，模型預算保留最後 30 秒給獨立 public tests 與證據擷取。評測單位為 harness × model × effort，品質分數由 JEV 決定，耗時、原生用量與已知費用另列。

結果、公開 case 與每題 public execution 結果存於 `~/.config/paulsha-patchmud/models-score.md`，原始結果保存在同目錄 `runs/`。native protocol 與舊 controlled protocol 使用不同 fingerprint 與 cache，不能互相重用。相符的完整歷史 base 可重用並標示日期；`--refresh-base` 強制重跑。`--repeat N` 重複測量，`--pilot` 取六題試跑，`--case ID` 選題；部分評測不產生正式總分。可用 `--output-dir DIR` 指定儲存位置。

native workspace 只公開 case 的 `repo/`。原 fixture tests、pytest／harness 設定檔與其他唯讀路徑受保護；`tests/agent/**` 與 disposable Git metadata 可供模型建立測試、checkpoint 與 rollback。最後的 public tests 由控制器獨立執行。控制器以一般公開檔案計算 diff，不執行受測 Git metadata，也不把 hidden answers、score store 或 `TYPESAFE_API_KEY` 放進 workspace。

評分規則、歷史重用與失敗語義見 [JEV 評分指南](docs/model-scoring.md)。實測與題庫凍結狀態須依交付證據判斷，程式存在不代表已完成 live 評分。

### 舊版關卡與研究命令

MVP CLI（依實作計劃逐步落地）：

```bash
patchmud play <關卡>                            # 人類親自玩（開場有白話規則；見 docs/how-to-play.md）
patchmud run <關卡> --model sonnet --live        # 看模型即時玩（--delay N 放慢節奏）
patchmud versus <關卡> --models sonnet,haiku     # 多模型並排對戰 + 記分板
patchmud watch <run_dir> [--turn N]            # 離線觀戰：逐回合 zh-TW 戰報
patchmud validate-deck decks/pilot-v1          # deck 契約與 fixture 驗證
patchmud author-encounter <source.yaml> --into <deck_dir>   # 出題：closed bug → 凍結關卡（含品質閘）
patchmud score-diff --encounter <dir> --diff <file>   # 離線評分（milestone A）
patchmud pilot --deck pilot-v1 --models models.yaml --seed 42   # forced loadout 矩陣（跑 benchmark）
patchmud replay <run_dir> [--l2]               # 兩級重播驗證
patchmud report --runs "runs/*"                # 多榜研究報告（模型比較）
```

- 關卡可只打名字（自動找 `decks/pilot-v1/<名字>`），`--loadout` 預設 `P0T0R0`（SOLO）。最短：`patchmud play input-validation-v1`。
- 模型別名（三家共用同一組短名）：

  | Provider | 別名 | 展開後 | 認證 |
  |---|---|---|---|
  | Anthropic | `sonnet` / `haiku` / `opus` / `fable` | `anthropic:claude-*` | API key 或 OAuth bearer；缺憑證且有 `claude` CLI 時自動改走 CLI |
  | OpenAI | `spark` / `luna` / `terra` / `sol` | `codex:gpt-5.3-codex-spark` / `gpt-5.6-luna` / `gpt-5.6-terra` / `gpt-5.6-sol` | `codex login` 的 OAuth 登入態（`~/.codex/auth.json`） |
  | Google | `flash` / `pro` | `agy:gemini-3.6-flash` / `agy:gemini-3.1-pro` | `agy` CLI 登入態（`~/.antigravitycli`） |

  跨家對戰：`patchmud versus input-validation-v1 --models sonnet,sol,flash`。
- Anthropic 認證兩選一：`export ANTHROPIC_API_KEY=…`，**或** 用 Claude 帳號 OAuth——`ant auth login` 後 `set -a; eval "$(ant auth print-credentials --env)"; set +a`（設定 `ANTHROPIC_AUTH_TOKEN`，不必管 API key）。codex 與 agy 別名走各自 CLI 的登入態，不需要任何 API key 環境變數。
- 舊 ranked／pilot 流程與 frozen pilot-v1 仍保留原本的 controlled adapter 契約；工程評分則使用 native Codex／agy CLI，允許各自的原生工具與 provider network。兩者的 transcript、protocol、fingerprint 與 cache 不混用；執行 candidate code 的唯一 seam 仍是 `IsolationRunner`。
- 出題（把已解決的 closed bug 結構化凍結成新關卡、含 bug/fix 品質閘）見 [`docs/authoring/README.md`](docs/authoring/README.md)。

## Version

版本記錄於 `VERSION`，變更紀錄見 [CHANGELOG.md](CHANGELOG.md)；版號規則遵循 hamanpaul project policy v1.0.17（`<MAJOR>.<MINOR>.<PATCH>[-fix.N]`，flat profile）。

## 開發

- 分支：`feature/<slug>` 或 `wt/<feature>/<subtask>`；禁止直接 commit `main`。
- 每個 code PR 同步更新 `CHANGELOG.md [Unreleased]`，並通過 `python3 -m policy_check --repo .` 與 `python3 -m pytest -q`。
- 本 repo 受 hamanpaul project policy v1.0.17 管轄（`tier: shareable`），詳見 `CLAUDE.md`。
