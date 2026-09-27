# JEV P1b：JEV 路由＋非程式判決，程式類交給 LLM

P1（`../jev-p1/`）判定 no-go。粒度診斷顯示，P1 一題問到底的設計不符 JEV 官方「拆成 atomic 問題、由程式組合」的原則；但讀程式碼判斷行為（計數、多跳推論）拆了也沒用。

依 Paul 09-27 裁決改成兩階段：
- 程式相關的驗收條件（AC）沿用 P1 的 LLM 整包判決；
- 其他 AC 與「模型選型」交給 JEV：由 JEV 決定每條 AC 交給哪個 judge。

規格由 ChatGPT thread 的 R23 提出（[`chatgpt-r23-prompt.md`](chatgpt-r23-prompt.md)、[`chatgpt-r23-reply.md`](chatgpt-r23-reply.md)），本文件是實作時的事先登錄。時間盒仍是 2026-10-03。

## 系統

| 代號 | 內容 | 角色 |
|---|---|---|
| H | JEV 路由；程式類或不確定 → Codex `gpt-6-luna`（effort max）整包判決；非程式 → JEV 拆子句判決 | 候選 |
| C | Codex `gpt-6-luna`（max）全部題目整包判決 | 主要對照 |
| D | Claude sonnet-5 全部題目整包判決 | 次要對照 |
| Jdiag | JEV 全部題目拆子句判決 | 只作診斷，不參與判定 |

Copilot 額度已用完，不參與 P1b。

- **路由（`router`）：** JEV 只看 AC 和證據清單（id、種類，以及由種類決定的三個旗標），不看證據內文，避免注入文字影響題型分類。問兩題：
  - R1：是否需要推理執行語意；
  - R2：是否有一條不需模擬程式的直接證據路徑。

  只有 R1＝`not_required` 且 R2＝`yes` 才送 JEV，其餘一律送 LLM。
- **非程式判決（`jdiag`）：**
  - 程式先依證據種類濾掉宣稱類證據：PR 說明、review、reviewer note、ticket、release note；
  - 每個原子子句對整組剩餘證據問一次 supports／contradicts／says_nothing；
  - 由程式彙整：任一子句 contradicts → not_satisfied；全部 supports → satisfied；其餘 → insufficient。剩餘證據為空時直接判 insufficient，不呼叫 JEV。
- **混合系統 H 不另外呼叫 provider（事先登錄的協定決定）：** 同一題同一 run 依路由結果，取 `jdiag` 或 `codex` 的同一次判決（common random numbers）。這樣 H 和 C 的差異只來自路由與 JEV 切片，不混入 Codex 兩次呼叫之間的隨機差異。H 的延遲＝路由延遲＋所選分支延遲。路由技術失敗時，依 v3「技術失敗才能 fallback」改走 LLM 分支並計數。

## 題庫（`bank/`）

- **`public.yaml`：** P1 的 36 題全部降為開發集（P1 的 hidden 失敗題已被看過，不得再計入 hidden 資格），逐題補上：
  - `ac_type`（`code_behavior`／`non_code`）
  - `route_label`（`LLM_REQUIRED`／`JEV_ELIGIBLE`；混合一律 `LLM_REQUIRED`）
  - `clauses`（原子子句）
- **`hidden.yaml`：** 全新 32 題，在任何 provider 執行前凍結。
  - 基礎題 24 題：`code_behavior`、`non_code` 各 12 題，三種標註各 4 題；
  - 誤導變體 8 題：兩種類型各 4 題，標註與來源題相同，涵蓋宣稱已修、過時註解、不相干證據、標籤與政策文件蓋過直接觀測等情境。
- **來源：**
  - 程式類改寫自 PR 40 的 engineering-v1／v2 public fixtures；
  - 非程式類為合成情境（`example.invalid` 網域、虛構服務）與 public repo 事實。

### hidden 的選題規則（事先登錄）

1. 每格（AC 類型 × 標註）先出 5 題候選，另出變體。
2. 由不參賽的 Codex `gpt-6-sol`（effort high，唯讀、無工具）逐題審查，範圍包括標註、AC 類型、路由標註、子句忠實度與證據一致性。審查模型刻意不用受測的 `gpt-6-luna`。
3. 標註或驗收條件本身有爭議的題一律排除，不改答案。只有「標註一致、但子句漏了條件」的題，才修正子句文字後複審。
4. 排除後依檔案順序，每格保留前 4 題；多出的備用題捨棄。

審查紀錄在 [`label-audit/`](label-audit/)：
- **第 1 輪（38 題候選）：** 排除 4 題、修正 5 題的子句；
  - 排除的是 `json-key-order`（「有 source」對 null 定義不清）、`tp35-not-auto-closed`（無法排除關閉後又重開）、`release-rejected-promotion` 與它的變體（log 只涵蓋一天）；
  - 第 1 輪的完整候選檔是 [`hidden-candidates-round1.yaml`](label-audit/hidden-candidates-round1.yaml)。
- **第 2 輪（新增與修正的 8 題＋1 題變體來源）：** 新增的 `changelog-known-issue` 被指出驗收條件被截斷。原因是 YAML 會把純文字值裡「空格＋#」之後當成註解；同樣受影響的 `issue-closed-by-pr` 改用引號（第 1 輪看的是 YAML 原文，判斷不受影響），`changelog` 題依規則當備用題捨棄。已加回歸測試，P1 題庫經檢查不受影響。

## 開發集上的調整（`dev/`，只用 JEV）

- **rev0：**
  - 路由：不安全路由 0 次，可交 JEV 的 8 題送出 6 題（75%）；
  - 拆子句判決：可交 JEV 的 8 題只對 5 題，把記錄裡的標籤（`patched`、`production-complete`）當成支持、把「未記錄」當成反證。
- **rev1（唯一一次修訂，只改拆子句判決的 criteria）：**
  - 在 supports／contradicts 的定義寫明「標籤、狀態只是主張，不是觀測」與「缺紀錄不是反證」；
  - 結果：可交 JEV 的 8 題全對，false-satisfied 從 6 降到 3（剩下 3 題都是程式題，本來就會送 LLM）。
- **路由器沒有修訂：** 放寬 R2 可能讓 `tp35-artifacts` 這類程式證據題被誤送 JEV。
- **已知限制：** 開發集的非程式題 7/8 是 insufficient，rev1 會不會對 satisfied 題過度保守，只能由 hidden 回答。

## 執行次數與上限

- 基礎題每個 arm 跑 1 次，誤導變體跑 2 次（量穩定性）：每個 arm 共 40 次判決。
- 上限：Codex ≤ 60 次、Claude ≤ 40 次、JEV ≤ 300 個 typed judgment；到上限就停，不因接近門檻而追加樣本。
- 本協定計畫：Codex 40 次、Claude 40 次；JEV 路由 80 個＋拆子句約 70 個。

## go 條件（三層全部通過才 go，門檻見 `patchmud/judge/p1b_scoring.py`）

1. **路由安全（hidden）：**
   - 程式類誤送 JEV（unsafe route）＝0；
   - `JEV_ELIGIBLE` 的預測至少 75% 送到 JEV。
2. **JEV 切片（hidden 中 `non_code` 且實際送 JEV 的預測）：**
   - false-satisfied＝0；
   - macro-F1 ≥ 0.90；
   - insufficient recall ≥ 0.90；
   - 誤導變體翻轉＝0。
3. **混合系統（全部 hidden 預測）：**
   - false-satisfied＝0；
   - macro-F1 與最佳全 LLM 對照組（C、D 取較高者）差距 ≤ 3pp；
   - 且至少一項資源收益：LLM 呼叫比 Codex 全包少 ≥ 35%、平均延遲少 ≥ 30%，或可比較的成本少 ≥ 30%。Codex 走訂閱、沒有金額，成本這一項不適用。

任一 arm 的紀錄不齊就判 no-go（資料不足）。

凍結後只允許修傳輸或序列化的錯誤；要改 criteria、標註、彙整或門檻，就作廢本輪、另起 P1b-r2。

## 結果

| run | 日期 | 判定 | 摘要 |
|---|---|---|---|
| [`20260927-p1b`](results/20260927-p1b/findings.md) | 2026-09-27 | **no-go** | 只有路由安全未過（程式類 AC 誤送 JEV 1 次，該題 JEV 仍判對）。其餘全過：JEV 切片 19/19、混合系統 macro-F1 1.000（與 Codex 全包相同）、LLM 呼叫 −50%、平均延遲 −38% |

## 執行

```bash
python -m patchmud.judge p1b-validate --bank experiments/jev-p1b/bank
python -m patchmud.judge p1b-freeze --bank experiments/jev-p1b/bank
TYPESAFE_API_KEY=… python -m patchmud.judge p1b-run --bank experiments/jev-p1b/bank \
  --out experiments/jev-p1b/results/<run-id>/records.jsonl --arms router,jdiag,codex,claude --split hidden
python -m patchmud.judge p1b-score --bank experiments/jev-p1b/bank \
  --records experiments/jev-p1b/results/<run-id>/records.jsonl
```
