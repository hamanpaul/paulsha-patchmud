# JEV P1b 結論：no-go（只差一項路由安全門檻）（2026-09-27）

- **run：** `20260927-p1b`。協定與 hidden 題庫於 09:55:09Z 凍結並提交（commit `4153541`），之後才執行。
- **規模：** 160 筆紀錄、0 錯誤、覆蓋率 100%。
  - hidden 32 題：基礎題 24 題各跑 1 次，誤導變體 8 題各跑 2 次，每個 arm 共 40 次預測；
  - arm：router、jdiag（JEV）、codex（`gpt-6-luna`@max）、claude（sonnet-5）。
- **判定：** 依事先登錄的三層門檻判定為 **no-go**。唯一未過的是路由安全：程式類 AC 被誤送 JEV 1 次，門檻是 0 次。其餘門檻全部通過。完整數字見 [`report.md`](report.md) 與 [`summary.json`](summary.json)。

## 數字

| 系統 | macro-F1 | false-satisfied | 誤導翻轉 | 平均延遲 | median 延遲 | LLM 呼叫 |
|---|---|---|---|---|---|---|
| H 混合（JEV 路由） | **1.000** | 0 | 0/16 | 5,059 ms | 3,751 ms | **20** |
| C Codex `gpt-6-luna`@max 全包 | 1.000 | 0 | 0/16 | 8,217 ms | 7,485 ms | 40 |
| D Claude sonnet-5 全包 | 0.974 | 0 | 0/16 | 10,613 ms | 6,462 ms | 40 |
| Jdiag JEV 全包拆解（診斷） | 0.948 | 1 | 0/16 | 475 ms | 358 ms | 0 |

| 層 | 門檻 | 結果 |
|---|---|---|
| 路由安全 | unsafe route＝0 | **FAIL（1）** |
| 路由安全 | JEV_ELIGIBLE 送 JEV ≥ 75% | PASS（19/20＝95%） |
| JEV 切片 | false-satisfied＝0、macro-F1 ≥ 0.90、insufficient recall ≥ 0.90、翻轉＝0 | 全部 PASS（19 次預測全對，涵蓋三種標註） |
| 混合系統 | false-satisfied＝0；macro-F1 與最佳對照差距 ≤ 3pp | PASS（1.000 對 Codex 1.000） |
| 資源收益 | LLM 呼叫 −35% 或延遲 −30% 或成本 −30% | PASS（呼叫 −50%、平均延遲 −38%；Codex 無金額，成本不適用） |

本輪用量：
- JEV：路由 40 次＋拆子句 40 次，US$0.003；
- Codex：40 次，約 75.6 萬 input token，走訂閱、無金額；
- Claude：40 次，約 US$0.50（API 牌價等值）。

## 那一次 unsafe route

`h-code-snapshot-cursor-unseen` 的內容如下：
- **驗收條件：** 「一個 snapshot 發出的 cursor，用在另一個 snapshot 時會 raise ValueError」，屬於程式行為，預先標為 `LLM_REQUIRED`；
- **證據：** 只有一份與此無關的測試輸出，標註是 insufficient。

路由器只看得到「驗收條件＋一個 `test_output`（直接觀測類）」，R1 判 `not_required`、R2 判 `yes`，於是送給 JEV。JEV 在這題判對了（insufficient）。

依協定，路由錯誤不能用後段碰巧答對來抵銷，所以仍算安全事故。這題暴露的是 R1 的弱點：**AC 在問程式行為、但證據裡沒有程式碼時，路由器會依證據種類而不是 AC 本身來判斷。** 同樣的情況下，如果證據換成一行看起來相關的 `PASSED`，JEV 就可能直接判 satisfied。這正是要擋在路由層的風險。

## 其他觀察（不影響判定）

- **JEV 切片完全正確：** 包括：
  - 誤導變體：宣稱類證據被程式濾掉、舊 CI 失敗紀錄、「flaky 已靜音」的 dashboard 標籤、寫著 30 天的政策文件與寫著 14 天的設定並陳；
  - 缺觀測類題目：host-3 缺 log、備份還原從未觀測、SLO 只有半個月資料、tenant c 沒有紀錄。

  P1 的整包判法在這類題目上會被標籤牽著走；拆子句、程式過濾宣稱，再在 criteria 寫明「標籤不是觀測、缺紀錄不是反證」之後，這類錯誤沒有再出現。
- **Jdiag 在程式題錯 2 題：**
  - 把「新增了但沒執行的測試」當成行為成立的證據（false-satisfied）；
  - 把「在任何寫入前就 raise」判成 insufficient。

  程式行為確實不該交給 JEV，和 P1 的結論一致。
- **Codex `gpt-6-luna`@max 在這批 hidden 題全對；Claude 錯 1 次**（`h-nc-migration-tenant-c-missing`：log 沒有 tenant c 的紀錄，被判成 not_satisfied，屬於封閉世界推論）。程式類題目交給 luna max 是可靠的。

## 效度限制

- 題目少：非程式切片只有 19 次預測。全對不代表錯誤率為 0，只能說這批題目沒有出錯。
- hidden 題由 Claude 撰寫，經 Codex `gpt-6-sol` 兩輪審查；有爭議的題已排除，但仍可能偏向「容易判定」的題目。
- 混合系統重用 C 的同一次判決（事先登錄），H 與 C 在程式題的品質因此必然相同。
- 延遲是端到端 wall time，包含 CLI 啟動；Codex 的成本無法換算成金額。

## 若要做 P1b-r2（需要 Paul 裁決）

依協定，改路由就作廢本輪、另起 r2。本輪的 hidden 題已被看過，r2 必須重出 hidden。最小的修法方向（尚未實作）：
1. 在路由加上一條不看證據的判斷：「AC 是否在陳述軟體行為（函式、API、CLI、程式路徑）」，是就送 LLM；或者改由程式依 AC 的來源或欄位決定，不讓 JEV 決定。
2. 路由標註的定義寫明「程式行為的 AC 不論證據種類，一律 `LLM_REQUIRED`」，並在 hidden 裡刻意放入「程式行為 AC＋只有觀測類證據」的題目。
3. 其餘規格（JEV 切片、門檻、呼叫上限）不變。
