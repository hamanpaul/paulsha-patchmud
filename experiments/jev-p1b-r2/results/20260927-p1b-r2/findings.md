# JEV P1b-r2 結論：no-go（路由已解決，JEV 非程式判決出現 false-satisfied）（2026-09-27）

- **run：** `20260927-p1b-r2`。協定與全新 hidden 題庫於 10:52:19Z 凍結並提交（commit `629778a`），之後才執行。
- **規模：** 160 筆紀錄、0 錯誤、覆蓋率 100%。
- **判定：** 依沿用 r1 的三層門檻判定為 **no-go**。路由安全與資源收益通過；JEV 切片與混合系統因同一個失敗模式未過。完整數字見 [`report.md`](report.md) 與 [`summary.json`](summary.json)。

## 數字

| 系統 | macro-F1 | false-satisfied | 誤導翻轉 | 平均延遲 | median 延遲 | LLM 呼叫 |
|---|---|---|---|---|---|---|
| H 混合（JEV 路由，r2） | 0.923 | **3** | 0/16 | 3,871 ms | 2,911 ms | 20 |
| C Codex `gpt-6-luna`@max 全包 | 1.000 | 0 | 0/16 | 6,795 ms | 5,994 ms | 40 |
| D Claude sonnet-5 全包 | 1.000 | 0 | 0/16 | 5,556 ms | 5,407 ms | 40 |
| Jdiag JEV 全包拆解（診斷） | 0.923 | 3 | 0/16 | 333 ms | 308 ms | 0 |

| 層 | 結果 |
|---|---|
| 路由安全 | **PASS**：unsafe route 0；JEV_ELIGIBLE 送 JEV 20/20 |
| JEV 切片 | **FAIL**：false-satisfied 3、macro-F1 0.856（門檻 0.90）、insufficient recall 0.625（門檻 0.90）；誤導翻轉 0 |
| 混合系統 | **FAIL**：false-satisfied 3；macro-F1 0.923，比 Codex 的 1.000 低 7.7pp（容忍 3pp） |
| 資源收益 | PASS：LLM 呼叫 −50%、平均延遲 −43% |

## R0 解決了路由問題

hidden 裡有 8 題「程式行為 AC＋沒有程式碼證據」的路由陷阱題，共 11 次預測。
- 其中 9 次 R1＝`not_required` 且 R2＝`yes`，照 r1 的規則都會送給 JEV；
- R0 在 11 次預測都回答 `yes`（在講軟體行為），全部改送 LLM。

另外，可交 JEV 的題目 20/20 全數送達，路由沒有因此變得過度保守。

## 這次的失敗：summary 欄位蓋過不完整的涵蓋範圍

3 次 false-satisfied 全部來自同一個情境：
- `r2-nc-license-scan-partial`（1 次）
- 它的誤導變體 `rv-nc-license-scan-claimed`（2 次）

| | 內容 |
|---|---|
| 驗收條件 | release 3.1.0 沒有任何依賴是 GPL 授權 |
| 證據 | 掃描報告 `packages_total 120、packages_scanned 118、gpl_found 0`，並列出 2 個未掃描的套件 |
| JEV 的判斷 | 子句「supports」，判 satisfied |
| 標註 | insufficient：有 2 個套件沒掃，無法斷言「全部」 |

這屬於官方已知限制裡的 literal reading：`gpl_found: 0` 這個彙總欄位，被當成對全稱命題的直接觀測。

開發集裡類似的「涵蓋不完整」題目，JEV 都判對了：
- host-3 缺 log；
- SLO 只有半個月；
- tenant c 沒有紀錄。

差別在於這題有一個看起來像結論的彙總欄位。

## 其他觀察（不影響判定）

- Jdiag 這次在程式題全對。路由把程式題全部送 LLM，所以這一點不影響混合系統。
- Codex `gpt-6-luna`@max 與 Claude sonnet-5 在 r2 hidden 都是 1.000。
- JEV 在三輪裡的誤導翻轉都是 0：程式過濾宣稱，加上 criteria 寫明「標籤不是觀測」，這部分是穩的。

## 三輪累計與建議

| 輪次 | 失敗的層 | 失敗模式 |
|---|---|---|
| P1 | 安全門檻 | 整包判決：多跳推論、計數、被標籤牽著走 |
| P1b-r1 | 路由安全 | 程式行為 AC＋只有觀測證據，被送給 JEV |
| P1b-r2 | JEV 切片 | 彙總欄位蓋過不完整的涵蓋範圍（全稱命題） |

- **有進展：** 每一輪都修掉了前一輪的失敗模式，而且有 hidden 佐證。
- **但每輪 hidden 都冒出新的失敗模式。** 若再加一條 criteria 規則（例如「彙總欄位不能證明全稱命題，當紀錄同時列出未涵蓋的項目時」），就是針對剛看到的 hidden 失敗修補。這正是 v3 說的「不遞迴追修」，也會讓 hidden 逐步失去保留集的意義。
- **建議依 v3「資料不足也判 no-go for now，不延長成平台專案」結案：**
  - 保留路由、拆子句判決與整套量測工具；
  - 待 JEV 出新版，或有真實的非程式驗收需求累積到足夠樣本時，再用新題庫重測。
