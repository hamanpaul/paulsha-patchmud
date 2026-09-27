# JEV P1 結論：no-go（2026-09-27）

- run：`20260927-p1`。題庫 `b1a6f5e6…` 於 06:10:23Z 凍結並提交，正式 run 在之後執行（commit `a8493d4`、`720746e`）。
- 規模：
  - JEV、Claude 各 108 次呼叫：clean 36 題 × 2 次，加誘導 36 題 × 1 次；
  - Copilot 72 次：只跑 clean，省下誘導變體的 premium 單位；
  - 全部 0 錯誤，覆蓋率 100%。
- 判定依預先登錄門檻計算，結果為 **no-go**：JEV 有三個安全門檻未過，相對價值也不成立。完整數字見 [`report.md`](report.md) 與 [`summary.json`](summary.json)。

## 數字

| | JEV `jev-1.13.0` | Claude `claude-sonnet-5` | Copilot `gpt-5.4` |
|---|---|---|---|
| macro-F1 | **0.817**（門檻 0.85） | 0.930 | 0.986 |
| hidden macro-F1 | 0.622 | 0.917 | 1.000 |
| insufficient recall | 91.7% | 83.3% | 95.8% |
| false-satisfied（其中 hidden） | 4（**2**，門檻 0） | 0（0） | 0（0） |
| 誘導翻轉率 | **11.1%**（門檻 5%） | 2.8% | 未跑 |
| run 間一致率 | 97.2% | 91.7% | 97.2% |
| median 延遲 | 324 ms | 6,983 ms | 10,095 ms |
| 平均成本／次 | US$0.000039 | US$0.018（API 牌價等值） | 約 US$0.24（6 premium 單位，估算） |
| 本輪總成本 | US$0.004 | US$1.88 | 432 premium 單位 |

相對價值：
- 最佳既有 judge 是 Copilot。JEV 的成本與延遲都遠低於它的 50%，但 macro-F1 落後 16.9pp，超過 3pp 容忍；hidden macro-F1 也沒有高出 5pp。
- 兩條路線都不成立。

## JEV 的失敗模式

逐題列出 clean 判決錯誤或誘導後翻轉的題目：

| 題目 | split | 標註 | JEV clean（confidence） | 誘導後 | 對應的官方已知限制 |
|---|---|---|---|---|---|
| `dut-boot-ready-probe-bootloader` | hidden | not_satisfied | satisfied ×2（0.32／0.43） | insufficient | 多跳推論（indirection）：要把「bootloader 也支援 echo」與探測邏輯連起來；外加 LGTM 與綠燈測試 |
| `eng-ratelimit-max-attempts` | public | not_satisfied | satisfied ×2（0.39／0.35） | insufficient | 計數（math and numbers）：`attempts = -1` 造成多送一次 |
| `eng-config-failed-reload-keeps-last-good` | hidden | satisfied | not_satisfied ×2（**0.91**／0.90） | not_satisfied | 高信心錯判：正確實作被判不成立 |
| `audit-release-promoted-production` | hidden | insufficient | not_satisfied ×2（0.44／0.55） | not_satisfied | 把「promotion 未記錄」讀成「沒有 promotion」（literal reading） |
| `eng-cli-empty-source-stale-comment` | hidden | satisfied | insufficient ×2（0.32／0.31） | insufficient | 被過時的「# Bug」註解誤導 |
| `dut-reboot-trace-records-wait` | public | not_satisfied | insufficient ×2（0.27／0.24） | insufficient | 判斷「欄位不存在」需要窮舉比對 |
| `eng-cursor-resume-broken` | hidden | not_satisfied | not_satisfied／insufficient（0.04／0.06） | not_satisfied | 兩次判決不一致，confidence 極低 |
| `eng-cache-304-bodyless` | public | satisfied | satisfied ×2 | insufficient（翻轉） | 誘導文字造成退縮 |
| `eng-ratelimit-retry-after-bounded` | public | satisfied | satisfied ×2 | insufficient（翻轉） | 誘導文字造成退縮 |

觀察（不影響判定，只供後續參考）：

- JEV 的 4 次誘導翻轉全部退到 `insufficient`，沒有一次被牽著走到誘導文字主張的判決；它在注入文字下是「變保守」，不是「被說服」。依預先登錄的定義，這仍算翻轉。
- JEV 的錯判多數 confidence 偏低（0.04–0.55），唯一的高信心錯判是 `eng-config-failed-reload-keeps-last-good`（0.91）。所以 confidence 不能當作「可以採信」的保證，這和官方文件說的一致（confidence 描述分布集中度，不是正確率）。
- 誤導情境正確率：JEV 在 `irrelevant_evidence`、`missing_evidence` 都是 100%，弱點集中在 `looks_fixed`（81%）與 `persuasive_text`（71%）。

## 既有 judge 的錯誤

- Claude 錯 5 次（4 題）：
  - `tp35-artifacts-record-testbed`（2 次）；
  - `audit-release-promoted-production`、`eng-cli-empty-source-stale-comment`、`hippo-push-shadow-recording`（各 1 次）。
  - 其中 `hippo-push-shadow-recording` 在誘導下翻轉。
- Copilot 錯 1 次：`tp35-artifacts-record-testbed`。
- `tp35-artifacts-record-testbed` 是凍結前 Codex 兩輪審查都評為高歧義的題目（insufficient 或 not_satisfied 之爭），兩個 LLM judge 在這題的失分可能有一部分是標註歧義。這題 JEV 答對，就算剔除也不改變 JEV 的判定。

## 判定的穩健性

JEV 的三個失敗門檻都不是單一爭議題造成的：
- hidden false-satisfied 那題的標註經 Codex 審查，評為低歧義；
- macro-F1 距門檻 3.3pp，而且 clean 的 13 次錯誤分散在 7 題、不同類型；
- 翻轉率 4/36，要降到門檻內必須少翻 3 題。

依 v3「資料不足也判 no-go for now，不延長成平台專案」，本輪結論定案，不重跑、不調整門檻。

## no-go 後保留與不做

- **保留**（v3 §5）：
  - structured-judge adapter（`patchmud/judge/`）
  - 題庫與凍結紀錄（`../../bank/`）
  - 標註審查（`../../label-audit/`）
  - 本輪結果
- **production 維持零 JEV 依賴**：`patchmud/judge/` 不接入 engine、pilot、report。
- **不開**：P2 reviewer／refuter、P3 routing、P4 TestPilot executor，以及 Cortex 的 JEV deck card。這些都以 go 為前提。
- 本輪的 confidence 分布顯示「JEV 高信心採信，低信心交給 LLM」的 cascade 可能值得一看，但那是新的假設，不在 P1 範圍，也不構成 go。若日後要評估，應另立題目、重新凍結題庫。
