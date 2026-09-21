# JEV 工程模型評分

`paulsha-patchmud` 評估指定 harness、model 與原生 effort 在固定工程情境及統一工具下的交付能力。它不代表各 CLI 原生工具、個人 skills 或完整日常 agent workflow 的成績。

## 指令

```bash
paulsha-patchmud --target --harness codex --model gpt-5.6-luna --effort max
paulsha-patchmud --base --harness agy --model gemini-3.8-flash --effort high \
  --target --harness codex --model gpt-5.6-luna --effort max
paulsha-patchmud --pilot --target --harness codex --model gpt-5.6-luna --effort max
paulsha-patchmud --list-cases
```

每個 group 必須獨立提供 harness/model/effort。全域選項：`--suite engineering-v1`、`--repeat N`、`--refresh-base`、`--output-dir DIR`、`--pilot`、可重複的 `--case ID`。不指定 target 的唯讀入口為 `--list-cases`。非法／衝突設定在付費執行前拒絕，無模型 fallback。

JEV 憑證由 `TYPESAFE_API_KEY` 環境變數提供。Codex／agy 使用各自登入態；認證與 model/effort 設定不寫成可公開的秘密資料。缺 JEV 憑證時尚未呼叫受測模型即停止。

統一工具模式要求 harness 能禁止原生工具。已查核的 Codex 0.155.1 與 agy 1.2.7 沒有這項保證，`read-only`、`--sandbox` 或空白工作目錄都不足以成立。這些版本目前會在正式模型呼叫前標示不支援；不能把通過 fake adapter 測試當作已完成真實 CLI 評分。使用額外隔離並使原生工具使用失效，或改評原生 coding agent，會改變評測契約，必須先明確選定。

## 題庫與預算

六類為 repair、diagnosis、scope、testing、recovery、audit。每類三題：多條件推理、跨元件定位、狀態演進；上限分別 8/16/24 回合與 600/1200/1800 秒。18 題單模型最多六小時執行時間，JEV 額外計時。提早完成可提前結束，步數不加分。

原 frozen pilot-v1 保留作舊模式與流程驗證，正式工程評分使用新的工程情境。public requirements 是評分契約；私有參考解／部分解／錯解僅供題庫與裁判驗證，不提供給受測模型或寫入公開報告。固定 staged evidence 對所有受測設定一致。

`--pilot` 選 repair depth1、diagnosis depth2、scope depth3、testing depth1、recovery depth2、audit depth3。pilot 與自選題輸出 partial coverage，不當作完整模型成績或歷史 base。先檢查飽和、不可解、預算與裁判錯誤，再凍結正式版本；不能為了強迫分差改題。

## 計分與不確定性

固定 JEV 版本為 `jev-1.13.0`。每題四項題目專屬規準：達成要求、證據支持、限制與相容性、驗證與交付；每項有五級（0..4）。四項原生 Score 平均乘以 25 即該題 0..100 分。每類三題等權，六類等權；重跑結果先在同題內平均。

測試結果是 JEV 的證據，程式不再另加 deterministic 分數、封頂或完成閘。原生 confidence 與機率分布保留，confidence 不乘進總分，也不是重複測量的信賴區間。一次評測僅是初步結果；不以細小分差宣稱顯著優勝。

送審資料排除受測模型／base-target 身分、原生計費 metadata 與先前分數，保留 transcript 的角色及原文作為證據；模型若自行在回答中提到身分，這些文字仍會保留。報告呈現規準與送審證據，不杜撰 JEV 沒有回傳的文字理由。未知實際模型快照保留 unknown；requested/resolved 不證明服務端採用值。

## 失敗與保存

模型用完回合／時間、協定失敗或產出不完整，仍由 JEV 評估已留下的成果。隔離、認證、provider 傳輸與 JEV 格式／服務失敗則標示 error、分數 null；不足完整 coverage 時不發布完整總分。測試的 passed、failed、error 分開記錄。

模型及 JEV 的原生 usage 各自保存。不同 harness token 語義不得混成同一種成本；缺少計價來源時費用為未知，不是零，也不將 API 等值費用當訂閱帳單。金額運算使用 Decimal。

預設輸出為 `~/.config/paulsha-patchmud/models-score.md`。`runs/<run-id>/` 是不可變、具 digest 的 JSON 原始資料；Markdown 是鎖定後原子更新的歷史檢視。各題包含公開題目、條件、預算、結果、四項分數／原始分布／confidence 與證據。執行中逐題保存 checkpoint；中斷保留已完成題目。

base 只重用完整且 digest 驗證成功、題庫／規準／裁判／工具協定／引擎內容／harness 版本／model+effort／預算／重複次數相符的結果。報告標示原評測日期，提供總分／分類／逐題差值。`--refresh-base` 可避開歷史重用；模型 alias 背後權重不可觀測時不宣稱是同一快照。

符合條件的歷史 target 結果也能成為後續的 base；base／target 表示該次比較的角色，並非不同的評分方法。每次命令的 target 都重新執行。

## 安裝與驗證

題庫隨 Python wheel 封裝，從 repo 外執行也可使用。candidate code 只經 IsolationRunner；正式評分缺 namespace 能力會拒絕執行。單元測試注入 fake model/JEV/runner，live pilot 與正式評分須有獨立原始證據。

執行環境必須提供 `bwrap`，以及可執行 pytest 的 `/usr/bin/python3`。安裝在 pipx／venv 的 pytest 不代表沙箱內的系統 Python 也能使用；缺少任一項會在模型呼叫前停止。

Python toolchain 掛載若包含安裝後的 PatchMUD 套件目錄，隔離器會遮蔽該目錄，避免私有參考答案透過 site-packages 被讀取。封存分開記錄引擎 Python／pytest 與沙箱 `/usr/bin/python3` 的版本；兩者可能不同。

參考 [TypeSafe HTTP API](https://docs.typesafe.ai/api.md)、[Score](https://docs.typesafe.ai/primitives/score.md) 與 [模型版本](https://docs.typesafe.ai/models.md)。

本次實作的離線／真實執行驗證與尚未完成的 live gates 記錄於 [validation record](jev-validation.md)。
