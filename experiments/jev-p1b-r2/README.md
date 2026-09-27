# JEV P1b-r2：路由加上「驗收條件是否在講軟體行為」

P1b-r1（`../jev-p1b/`）判 no-go，只差路由安全一項：
- 一題「程式行為 AC、但證據只有測試輸出」被路由器送給 JEV。JEV 雖然判對，依協定仍算安全事故；
- 其餘門檻全部通過。

Paul 09-27 裁決做 r2。依 r1 協定，改路由就作廢該輪、另起 r2，並重出 hidden。

本文件只列出與 r1 的差異；系統組合、混合系統 H 的推導、門檻、呼叫上限、選題規則與凍結規則，都沿用 [`../jev-p1b/README.md`](../jev-p1b/README.md)。時間盒仍是 2026-10-03。

## 與 r1 的差異

1. **路由新增 R0**（`patchmud/judge/p1b.py` 的 `R0_SOFTWARE_BEHAVIOR`；版本在 `ROUTER_REVISIONS["r2"]`）：
   - 只看 AC、不看證據清單，問「這條 AC 是否在講軟體執行時的行為」（函式、API、CLI、腳本或程式路徑的回傳值、錯誤、exit status、副作用、輸出、重試或狀態變化）。
   - 路由規則改為：**R0＝`no` 且 R1＝`not_required` 且 R2＝`yes`** 才送 JEV，其餘一律送 LLM。
   - r1 的路由問題與凍結紀錄不變，仍可用 `--revision r1` 驗證。
2. **路由標註的定義寫明：** 程式行為 AC 不論證據種類，一律 `LLM_REQUIRED`。
3. **開發集（`bank/public.yaml`）：** r1 的開發集 36 題＋r1 hidden 32 題（已執行、看過結果），共 68 題。
4. **hidden 全新：**
   - r1 的備用題從未被任何 judge 看過，可以回收（標明來源）；
   - 刻意放入「程式行為 AC＋只有觀測類證據」的路由陷阱題，例如 CLI 執行紀錄、假時鐘 log、無關的測試輸出、只記首次重建的 log，S／N／I 三種標註都有。
5. **審查改用解析後的題目：** 審查者看到的內容就是 judge 會收到的內容，避免 r1 發生的 YAML「空格＋#」截斷沒被發現。

## 開發集上的 R0（`dev/rev0-router.jsonl`，只用 JEV）

| 指標 | 結果 |
|---|---|
| 不安全路由 | 0 次（包含 r1 出事的那題） |
| JEV_ELIGIBLE 的預測送到 JEV | 23/28（82%，門檻 75%） |

未送出的 5 次都是保守方向：
- 2 次 R2 回答 `uncertain`；
- Hippo 兩題與 tp46 被 R0 判為「在講軟體行為」，這個判斷本身說得通。

R0 沒有修訂。

## hidden 審查與選題（[`label-audit/`](label-audit/)）

- **第 1 輪（39 題候選，審查的是解析後的內容）：** 排除 7 題、修正 2 題的子句。
  - 排除（標註有爭議）：
    - `rebuild-cursor-after-publish` 與它的變體：「呼叫了 publish」不等於「已發布」；
    - `rebuild-max-off-by-one`：看不到交易產生器；
    - `backup-retention`：設定值不等於實際保留；
    - `node-b-wrong-digest`：驗收條件沒有時間範圍；
    - `region-mismatch` 與它的變體：設定不等於實際執行位置。
  - 修正：`flag-erases-unrelated` 與它的變體，子句補上「未 acknowledge 的 tenant」。
  - 第 1 輪的完整候選檔是 [`hidden-candidates-round1.yaml`](label-audit/hidden-candidates-round1.yaml)。
- **第 2 輪（新增與修正的 6 題＋2 題變體來源）：** 全部一致，沒有排除。
- 依檔案順序每格保留前 4 題，捨棄多出的備用題：`projection-guard-partial`、`dns-two-of-three`、`secondary-unstaffed`、`dispatch-sleep-lgtm`。
- **最終組成：** 24 題基礎題＋8 題誤導變體。「程式行為 AC＋沒有程式碼證據」的路由陷阱題共 8 題（5 題基礎題＋3 題變體），S／N／I 都有。
