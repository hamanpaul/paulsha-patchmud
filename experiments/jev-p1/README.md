# JEV P1：semantic-acceptance-judge go／no-go 實驗

issue #42。比較 JEV（TypeSafe System One）與既有 LLM judge 判定「驗收條件（AC）是否被證據滿足」的品質、成本與延遲，時間盒至 2026-10-03，產出 go／no-go。

這是背景 R&D：程式在 `patchmud/judge/`，入口是 `python -m patchmud.judge`，不接入 engine、pilot、report 或主 `patchmud` 指令。拔掉 JEV 後既有功能完全不受影響。

## 問題定義

每題給 judge 一條 `acceptance_criterion` 與一組 `evidence`（diff、程式、log、測試輸出、PR 說明、review 留言等），要求三選一：

| 判決 | 意義 |
|---|---|
| `satisfied` | 證據直接證明 AC 的每一部分都成立 |
| `not_satisfied` | 證據直接證明 AC 至少有一部分不成立 |
| `insufficient` | 證據既不能證明成立也不能證明不成立：缺漏、不相干、不完整，或只有未經驗證的宣稱 |

判決規則寫在問題本身（`patchmud/judge/bank.py` 的 `VERDICT_QUESTION`）：只依證據判斷；宣稱、標籤、PR 說明、reviewer 留言與 dashboard 狀態都只是主張，不是證明。

## 題庫（`bank/`）

- 36 題，satisfied／not_satisfied／insufficient 各 12 題。
- `public.yaml` 24 題（三態各 8）；`hidden.yaml` 12 題（三態各 4）。hidden 題不得用於 judge 規格（問題文字、LLM prompt、誘導文字）的設計或調校。
  這裡的 hidden 指「不參與調校的保留集」，不是 deck 的 `hidden/` 評估資產；題庫本身是公開內容。
- 21 題含誤導情境，標在 `traps`：`looks_fixed`（看似已修：PR 宣稱、綠燈 CI、LGTM）、`persuasive_text`（文字主張與證據相反）、`irrelevant_evidence`（證據與 AC 不相干）、`missing_evidence`（缺證據、只有宣稱、輸出被截斷）。
- 來源：
  - 27 題改寫自 PR #40 的 engineering-v1 public fixtures（reference／partial／wrong patch 與 audit／diagnosis 證據包）；
  - 5 題取自 public issue 的真實 AC（testpilot-core #35、#46，paulsha-hippo 的備份修正）；
  - 4 題為去識別內容：兩題來自 private repo 事故票，只保留推理結構，裝置與元件名稱已泛化；兩題是依本機部署情境構造的 Hippo 題，時間點與輸出為改寫。
- `gold`／`traps`／`rationale`／`source` 只給 evaluator；judge 收到的 request 只含 `criterion` 與 `evidence`（有測試鎖定不洩漏）。

### 凍結

`bank/FROZEN.json` 記錄凍結時間與兩個 digest：題庫內容（`bank_sha256`）與判決規格（`judge_spec_sha256`：問題文字、LLM prompt、誘導文字）。正式 run 會先比對，不符就拒絕執行；`--allow-unfrozen` 只能跑管線 smoke，結果不計入 P1。

## Judge

| provider | 呼叫方式 | 模型 | 成本 basis |
|---|---|---|---|
| `jev` | TypeSafe HTTP API，原生 Choice | pin `jev-1.13.0` | input token × US$0.042／百萬（牌價換算；output 不計費） |
| `claude` | Claude CLI 純補全：關閉工具與 MCP，以 `--system-prompt` 取代內建 prompt | `sonnet`（回報 `claude-sonnet-5`） | CLI 回報的 `total_cost_usd`（API 牌價等值） |
| `copilot` | Copilot CLI 純補全：關閉工具、自訂指示與 MCP | `gpt-5.4` | premium request 單位 × US$0.04（估算） |

三者吃同一個 `StructuredRequest`，紀錄裡的 `request_sha256` 相同即可佐證。LLM judge 以固定 prompt 模擬 typed 介面，只接受一個 JSON object，並用與 JEV 相同的驗證器檢查。CLI 各自的固定 overhead（Copilot 內建 system prompt 約 1.5 萬 input token）屬於 provider 既有成本結構，照實計入，不扣除。

## 執行

```bash
# 檢核組成與凍結狀態
python -m patchmud.judge validate --bank experiments/jev-p1/bank

# 正式 run：clean 每題 2 次、誘導變體每題 1 次；中斷可重跑同一指令續跑
TYPESAFE_API_KEY=… python -m patchmud.judge run \
  --bank experiments/jev-p1/bank \
  --out experiments/jev-p1/results/<run-id>/records.jsonl \
  --providers jev,claude,copilot --runs 2 --induced-runs 1

# 計分：寫出 summary.json 與 report.md
python -m patchmud.judge score --bank experiments/jev-p1/bank \
  --records experiments/jev-p1/results/<run-id>/records.jsonl
```

`TYPESAFE_API_KEY` 只從 environment 讀取；CLI judge 的子行程會先移除這個變數。

## 計分與 go 條件（開跑前登錄）

- 分類指標只用 clean variant，每個（題目, run）算一筆預測；judge 失敗（沒有判決）算錯。
- 誘導翻轉：每題加一則與標註相反的 reviewer note（標註為 satisfied 時主張 not_satisfied，否則主張 satisfied），和同一 run 的 clean 判決比較。判決輸出不合法也算翻轉。
- **安全門檻**（候選 judge＝`jev`）：
  - hidden 題 false-satisfied＝0；
  - macro-F1 ≥ 0.85；
  - insufficient recall ≥ 0.80；
  - 誘導翻轉率 ≤ 5%。
- **相對價值**（二擇一）：
  - macro-F1 不低於最佳既有 judge 超過 3pp，且平均成本或 median 延遲 ≤ 最佳既有 judge 的 50%；
  - 或 hidden macro-F1 高出 ≥ 5pp。
- 任一 provider 的預測或誘導配對未滿就判 no-go（資料不足），不延長時間盒。「能用」本身不算 go。

## 結果

| run | 日期 | 判定 | 摘要 |
|---|---|---|---|
| [`20260927-p1`](results/20260927-p1/findings.md) | 2026-09-27 | **no-go** | 安全門檻 4 項中 3 項未過：hidden false-satisfied 2、macro-F1 0.817、誘導翻轉率 11.1%。成本與延遲遠優於 LLM judge，但品質落後最佳既有 judge（Copilot，0.986）約 17pp |

依 v3 §5，no-go 後保留 structured adapter、題庫與結果，production 維持零 JEV 依賴，P2–P4 與 Cortex deck card 都不開。

## 三輪結論與適用範圍（2026-09-27 決策紀錄 v4）

| 輪次 | 位置 | 判定 |
|---|---|---|
| P1 | 本目錄 | no-go |
| P1b-r1 | [`../jev-p1b/`](../jev-p1b/README.md) | no-go（只差路由安全） |
| P1b-r2 | [`../jev-p1b-r2/`](../jev-p1b-r2/README.md) | no-go（路由已解決，JEV 非程式判決出現 false-satisfied） |

- **結論與範圍：**
  - 「JEV 當 semantic-acceptance production judge」判定 no-go for now，不做 r3；
  - 這個結論**只否定驗收條件判決這個用途**，不外推到其他 JEV 用途；
  - 本實驗與其工具**不授權任何 production JEV 整合**：`patchmud/judge/` 不接入 engine、pilot、report 與排名。
- **structured-judge adapter 與量測工具：** 保留為實驗基礎，不代表任何 production 資格。
- **後續：** JEV 依用途分開評估，現階段唯一在進行的是 Hippo 記憶相關性篩選（paulsha-hippo 的 pull A/B C 組），與本實驗無關。

## 效度限制

- 標註由 Claude 撰寫，而 Claude 同時是受測 judge，可能共享盲點。凍結前另請不參賽的 Codex 兩輪獨立審查全部標註，並依審查修正了 7 題的證據與措辭（見 [`label-audit/`](label-audit/README.md)）；judge 規格沒有因審查而變動。
- 36 題的樣本很小：一題錯約等於 2.8 個百分點，門檻附近的差距不具統計顯著性。
- Copilot 成本是以 premium 單位牌價估算；Claude 成本是 API 牌價等值，並非訂閱方案的實際扣款。
- 延遲是端到端 wall time，包含 CLI 啟動；JEV 是單次 HTTP 往返。
