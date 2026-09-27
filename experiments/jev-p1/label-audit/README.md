# 題庫標註審查（凍結前）

標註由 Claude 撰寫，而 Claude 同時是受測 judge。為降低共享盲點，凍結前請不參賽的 Codex（`gpt-5.6-sol`，effort high，唯讀、無工具）逐題獨立重判：先只看 criterion 與 evidence 自行判決，再對照標註，並檢查證據的內部一致性與歧義。

審查者看得到 hidden 題，但不參與 judge 規格設計，也不是受測 judge。審查只改題目的證據與措辭，judge 規格（問題文字、prompt、誘導文字）完全沒有因審查而變動。

## 第 1 輪（36 題）

原始輸出：[`codex-round1.md`](codex-round1.md)。30 題一致；6 題被指出證據不足以支撐標註，處置如下：

| 題目 | 問題 | 處置 |
|---|---|---|
| `tp35-operator-edits-persist` | 改動 marker 行本身會被覆寫，原驗收沒限制編輯位置 | 驗收收窄為「保留第一行 staging 標記、只編輯其下方」 |
| `eng-config-port-range` | 只證明 `parse()` 接受 70000，沒呈現安裝 active 設定的元件 | 補上唯一安裝路徑 `reloader.py`（未改動） |
| `tp35-artifacts-record-testbed` | meta 由 plugin 組裝，core 內的搜尋無法證明「沒有記錄」 | 改標 `insufficient`（trap：`missing_evidence`） |
| `diag-retry-duplicate-delivery` | `delivered=1` 的語意未定義，逾時那次可能已送達 | 補受信端 delivery ledger |
| `tp35-plugin-switch-isolation` | 只證明 staging 函式行為，沒證明 run 會先 staging 再讀檔 | 補 `run_plugin_cases → get_orchestrator → Orchestrator` 呼叫路徑 |
| `eng-schema-replay-test-deleted` | 剩下的測試本體沒附上，可能仍涵蓋 replay | 附上兩個剩餘測試的完整內容 |

為維持三態各 12 題：新增 `eng-fakeclock-caller-log`（not_satisfied），移除與 `eng-cache-invalidate-unseen` 模式重複的 `eng-cli-json-order-unseen`（insufficient）。

## 第 2 輪（修正與新增的 7 題）

原始輸出：[`codex-round2.md`](codex-round2.md)。7 題全部一致。`tp35-artifacts-record-testbed` 仍被評為高歧義（「關鍵欄位」未定義），因此驗收改為明列三個必要欄位，並加入證據範圍說明。這之後才凍結（`../bank/FROZEN.json`）。
