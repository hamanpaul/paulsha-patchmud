# 跨 provider adapter 設計：codex / agy OAuth headless

- 狀態：已核可，對應 issue [#14](https://github.com/hamanpaul/paulsha-patchmud/issues/14)
- 日期：2026-08-11
- 影響範圍：`patchmud/adapters/`、`patchmud/ledger/tokens.py`、`patchmud/cli.py`

## 問題

`_MODEL_ALIASES` 只涵蓋 Anthropic 一家，`opus` 還指向上一代的 `claude-opus-4-8`。
要量測跨廠牌的 coding-agent 能力封套，需要把 OpenAI 與 Google 兩條 OAuth headless
路徑納入同一組 deck 與同一套評分流程。

## 決策一：純補全模式，不是 agent 模式

`codex` 與 `agy` 都是會執行 shell、直接改檔案的 coding agent。PatchMUD 的
adapter 契約（spec §2）卻只有一條：`complete(messages) -> AdapterResponse`。
兩種接法：

| | 純補全（採用） | agent 直接進 workspace |
|---|---|---|
| 工具寫入 | 全關，臨時空目錄 | 開放於 encounter workspace |
| candidate code 執行 | 仍只走 `IsolationRunner` | 繞過隔離層 |
| `hidden/` 防洩漏 | 維持既有保證 | 需另建防線 |
| 與 `anthropic:*` run 可比 | 是 | 否，評分基礎不同 |

採用純補全的理由是評測效力，不是實作便利：ranked 排名要成立，所有 run 必須
共用同一個執行語意。若 codex/agy 走 agent 模式而 Anthropic 走純補全，兩者的
分數不再度量同一件事，排名失去意義。既有的 `ClaudeCliAdapter`（`--tools ""`）
已經是這個處理方式，本設計只是把它擴到另外兩家。

具體旗標：

```
codex exec <prompt> -m <slug> -c model_reasoning_effort=high \
  --sandbox read-only --ephemeral --skip-git-repo-check --ignore-user-config \
  --disable plugins --disable memories --disable goals --disable hooks \
  --cd <臨時空目錄> --json

agy --print <prompt> --model <id> --effort high \
  --output-format json --disable-slash-commands --sandbox
```

`--ignore-user-config` 是可重現性的防線：`~/.codex/config.toml` 的 personality、
預設 effort、hooks 都會進 prompt，未阻斷則同一個關卡在不同機器上量到不同結果。

## 決策二：effort 固定 `high`

`CLI_EFFORT = "high"`，codex 走 `-c model_reasoning_effort=high`、agy 走
`--effort high`。ranked run 之間的推理預算必須可比，不能隨使用者的 CLI 設定漂移。

agy 的 effort 也可以烘在 model id 後綴（`gemini-3.6-flash-high`）。改用
base id + 顯式旗標，一是與 codex 對稱、二是別名表不必為每個 effort 檔位各列
一條（`gemini-3.1-pro` 甚至沒有 medium 檔位，後綴法會讓別名表出現不對稱的洞）。

`AnthropicAdapter` 本次不動 effort/thinking。Opus 5 的 thinking 預設開啟，且
`max_tokens` 是 thinking 與回覆的共用上限（目前 4096）——調 effort 必須連同
`max_tokens` 一起評估，屬獨立的一批改動。

## 決策三：usage 差異吸收在 ledger，不在 adapter

三家的 usage 欄位名互不相同：

| | input | cached | output | reasoning |
|---|---|---|---|---|
| openai API | `prompt_tokens` | `prompt_tokens_details.cached_tokens` | `completion_tokens` | `completion_tokens_details.reasoning_tokens` |
| codex CLI | `input_tokens` | `cached_input_tokens` | `output_tokens` | `reasoning_output_tokens` |
| agy CLI | `input_tokens` | `cache_read_tokens` | `output_tokens` | `thinking_tokens` |

spec §10.1 規定 adapter 原樣透傳、不拆不清洗，所以差異吸收在
`patchmud/ledger/tokens.py` 的 per-provider mapper，新增 `codex` 與 `agy` 兩個 key。

**子集語意以真 CLI 實測確認**，不靠欄位名推測：

- codex（gpt-5.6-luna，effort=high）：`output_tokens` 29 −
  `reasoning_output_tokens` 21 = 8，對應可見回覆 `1170` 的量級 → reasoning ⊆ output。
- agy（gemini-3.6-flash-high）：`output_tokens` 459 − `thinking_tokens` 452 = 7，
  同樣對應 `1170\n`；且 `input_tokens` 17874 + `output_tokens` 459 = `total_tokens`
  18333 → thinking ⊆ output，且 total 是重述而非獨立計價量。

因此兩者都採 openai 語意（cached ⊆ input、reasoning ⊆ output）。
`total_tokens` 不進 ledger（可由互斥欄位重算），但若與 `input + output` 對不上
一律 fail-closed——provider 資料不一致不得被靜默採信。codex 的
`cache_write_input_tokens` 有計價但不屬互斥欄位，比照 anthropic 的
`cache_creation_input_tokens` 進 `unallocated` 殘差。

## 決策四：別名共用單一命名空間

```python
"spark": "codex:gpt-5.3-codex-spark",   "flash": "agy:gemini-3.6-flash",
"luna":  "codex:gpt-5.6-luna",          "pro":   "agy:gemini-3.1-pro",
"terra": "codex:gpt-5.6-terra",
"sol":   "codex:gpt-5.6-sol",
```

短別名與既有的 `sonnet` / `haiku` 風格一致，三家共用同一個 dict、彼此不得重複。

claude CLI fallback（缺 Anthropic 憑證時 `anthropic:X` → `claude:X`）抽成
`_fallback_to_claude_cli()`，且**只對 `anthropic:` spec 生效**——否則
`sol` 在沒有 Anthropic 憑證的機器上會被改寫成 `claude:gpt-5.6-sol`，
把 OpenAI 的 run 靜默送去 Claude 執行。這條有回歸測試鎖定。

## 架構

```
CliModelAdapter (cli_base.py)      ← 計時、注入式 runner、prompt 攤平
├── CodexCliAdapter (codex_cli.py) ← argv 組裝 + JSONL 事件解析
└── AgyCliAdapter   (agy_cli.py)   ← argv 組裝 + 單一 JSON 解析
```

`ClaudeCliAdapter` 維持獨立不動：它有 `--system-prompt` 旗標與
`[User]`/`[Assistant]` 攤平格式，行為本來就與這兩者不同，硬併會製造假的共用。

子行程執行抽成注入的 runner callable：unit tests 一律注入 fake，不啟真 CLI、
不打真 API。真 runner 的 `stdin` 導向 `DEVNULL`——CLI 偵測到 stdin 是 pipe
時會等待補充輸入而卡住（實測踩到）。

## fail-closed 清單

| 情境 | 行為 |
|---|---|
| codex 輸出無 `agent_message` 事件 | `AdapterError` |
| codex 輸出無 `turn.completed.usage` | `AdapterError`（ledger 無從計費） |
| agy `status != "SUCCESS"` | `AdapterError`（exit 0 的失敗不得當成合法回覆） |
| agy 缺 `response` / `usage` | `AdapterError` |
| 輸出完全無法解析成 JSON | `AdapterError` |
| CLI 不在 PATH | `RunCliError`，附安裝與登入指引 |
| `codex:` / `agy:` spec 缺 model id | `RunCliError` |
| usage 子集超出母集、agy `total` 對不上 | `LedgerError` |

## 附帶修正：run.yaml 記別名的可重現性缺口

`patchmud run --model opus` 原本在 `run.yaml` 記 `model: opus`——記的是別名，
不是展開後的 spec。本次把 `opus` 從 `claude-opus-4-8` 改指 `claude-opus-5`，
使這個既有缺口具體造成傷害：既有封存寫著 `model: opus`，事後已無從判斷該場
run 跑的是 4.8 還是 5。

修正為記錄 `normalize_model_spec()` 的結果。這與 pilot 路徑（記
`entry.adapter` 完整 spec）以及 scripted run 的既有慣例一致——記別名本來就是
不一致，不是設計。versus 記分板的顯示名走 `VersusEntry.model`，不受影響。

## 已知限制

CLI 自帶 system prompt 與 skill 目錄，每回合有固定的 input overhead（實測
codex ≈17k、agy ≈18k tokens），且每次呼叫都重新計入——CLI 端不保留對話狀態，
每次 `complete()` 都是獨立 session。

嘗試過的降噪手段與結果：`--ignore-user-config`、`--disable plugins|memories|goals|hooks`、
乾淨的 `CODEX_HOME`，三者合計只把 codex 從 18.5k 壓到 16.6k（約 10%）。
剩下的是 CLI 內建 system prompt 與 skill 描述，非設定可關。

**不做扣除**：這層 overhead 屬於該 provider 的既有成本結構，扣掉等於量測一個
使用者拿不到的假設值。既有的 `ClaudeCliAdapter` 同樣具此特性。文件（README、
`docs/user-manual.md`）明確標示，讓看成本榜的人知道同家內部比較最準確。
