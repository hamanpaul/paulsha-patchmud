【R23｜P1 結果 no-go，依 Paul 裁決調整成 P1b】
不要上網搜尋、不要使用任何工具。請用繁體中文回答，要具體、可以直接實作。

## 一、P1 已完成（依 v3 §5）
題庫 36 題（三態各 12、hidden 12、誤導 21），凍結後才跑；288 次呼叫、0 錯誤。

| | JEV jev-1.13.0 | Claude sonnet-5 | Copilot gpt-5.4 |
|---|---|---|---|
| macro-F1 | 0.817 | 0.930 | 0.986 |
| hidden false-satisfied | 2 | 0 | 0 |
| 誘導翻轉率 | 11.1% | 2.8% | 未跑 |
| median 延遲 | 324 ms | 7.0 s | 10.1 s |
| 每次成本 | US$0.00004 | US$0.018 | 約 US$0.24 |

判定 no-go：JEV 三項安全門檻未過，品質落後最佳既有 judge 約 17pp。
- P1 的問法：一題一個 Choice（satisfied／not_satisfied／insufficient），state 放多子句 AC＋1–4 份證據（diff、程式、log、測試輸出、PR 說明、review），instructions 裡寫四條規則（含「宣稱不是證明」）。每次 650–1,360 input token。
- JEV 錯的 7 題：迴圈計數（attempts=-1 多送一次）；多跳推論（要把「bootloader 也能 echo」的文件和探測程式連起來，hidden 的 false-satisfied 就是這題）；封閉世界推論（完整欄位清單裡沒有 → 沒記錄）；被過時的「# Bug」註解誤導；把「promotion 未記錄」讀成「沒有 promotion」；一題對正確實作高信心（0.91）判成 not_satisfied。4 次誘導翻轉全都退到 insufficient，沒有一次被說服到誘導主張的判決。
- JEV 做得好的：insufficient recall 91.7%；「不相干證據」「缺證據」兩類誤導 100% 判對。

## 二、粒度診斷（10 題 public，不計分）
照 TypeSafe 官方指南（拆成最窄的 atomic 問題，用程式組合）改法：AC 拆成單一子句；每個子句用 citation-check 的三選一（supports／contradicts／says_nothing）問整組證據；「宣稱不是證明」改成程式依證據種類（pull_request、review_comment、reviewer_note、ticket、release_note）直接過濾；程式彙整（任一子句 contradicts → not_satisfied；全部 supports → satisfied；其餘 insufficient）。
- 誘導翻轉 3/10 → 0/10（靠程式過濾；不過濾時仍有 1 題被注入的 reviewer 留言當成支持證據）。
- clean 正確 8/10 → 8/10：迴圈計數與封閉世界推論兩題照錯，拆細無效。
- 再把每份證據單獨拆題（子句×證據）反而更差，失去跨證據推理。
我的結論：翻轉門檻失敗主要是設計造成；但「讀程式碼判斷行為」是 System-Two 工作，不是 JEV 的定位。官方「JEV≈Terra」的 67.8% 是已拆成 typed questions 的工作流程一致率，不是整包推理準確率。

## 三、Paul 的裁決（09-27）
1. P1b 分兩個階段：程式相關的 AC 沿用之前的架構（LLM judge，單題整包判）；其他 AC 與「模型選型」交給 JEV。我目前的理解：第一階段由 JEV 做路由（判斷這條 AC 是否需要推理程式怎麼執行），決定交給哪個 judge；第二階段程式類 → LLM judge，非程式類 → JEV（照第二節的拆解版）。
2. PR（P1 的 experiment PR）等 P1b 出來再決定是否 merge；P1b 放同一個 PR。
3. PatchMUD PR 40（用 JEV 的四維 Score 評 coding agent 工程作答、拿分數比較模型；+19,788 行、新增 production 套件 patchmud/scoring 與新 CLI、新增裁判 schema；開於 09-21，早於 v3）已轉 draft。它和 v3 的衝突：§5「P1 是現階段唯一的 JEV 工作」、§6「go 之前不定任何 JEV schema」、原則 7「authority 不屬於 JEV、拔掉 JEV 系統照常運作」（它讓 JEV 分數直接決定模型排名），以及 PatchMUD 自己的「排名資料流零 LLM 裁判」。
4. Copilot 額度已用完，暫停使用。Codex 有額度：P1b 的目標測試模型用 Codex gpt-6-luna（effort max）。Claude sonnet-5 仍可用。
5. 時間盒仍是 10-03。TypeSafe 只收 public／去識別內容。

## 四、請你提出
A. P1b 的具體規格（我會照著實作）：
  1. 路由階段：JEV 要問哪幾個問題、選項與 criteria 文字、路由錯誤怎麼計分（程式類誤送 JEV 是不是等同安全事故）。
  2. 題庫怎麼改：是否每題標 AC 類型（程式行為／非程式）與原子子句；P1 的 hidden 我已經看過失敗題，要不要重出 hidden、要出幾題；三類與兩種 AC 類型的配比。
  3. 受測組合：候選系統（JEV 路由＋JEV 非程式＋Codex luna max 程式）、對照組（Codex luna max 全包、Claude sonnet-5 全包）、要不要保留 JEV 全包拆解版作診斷；每組跑幾次、誘導變體怎麼設。
  4. go 條件怎麼改：哪些門檻套在整個混合系統、哪些只套在 JEV 負責的切片；相對價值怎麼定義（混合系統的成本與延遲大部分來自程式類題目）；「能用」不算 go 的精神怎麼保留。
  5. 事先登錄與凍結的最低要求，以及 Codex luna max 的呼叫量控制。
B. PR 40 與 v3 的衝突怎麼調整：v3 要改哪幾句，還是 PR 40 要改成什麼形狀（例如縮成實驗、拆出可保留的部分、等 go 再談），請給建議與理由。

請把 A 寫成可以直接當規格的條列，B 精簡即可。
