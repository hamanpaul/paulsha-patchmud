# JEV 工程模型評分

`paulsha-patchmud` 評估指定 harness、model 與 effort 在固定工程情境中的原生 coding-agent 交付能力。Codex 與 agy 由各自 CLI 執行，模型可使用該 CLI 的原生工具；這個契約與舊 ranked／pilot 的 controlled adapter 流程分開保存、分開解讀。

## 指令

```bash
paulsha-patchmud --target --harness codex --model gpt-5.6-luna --effort max
paulsha-patchmud --base --harness agy --model gemini-3.8-flash --effort high \
  --target --harness codex --model gpt-5.6-luna --effort max
paulsha-patchmud --pilot --target --harness codex --model gpt-5.6-luna --effort max
paulsha-patchmud --list-cases
```

每個 group 必須獨立提供 harness/model/effort。全域選項：`--suite engineering-v1`、`--repeat N`、`--refresh-base`、`--output-dir DIR`、`--pilot`、可重複的 `--case ID`。不指定 target 的唯讀入口為 `--list-cases`。非法／衝突設定在付費執行前拒絕，無模型 fallback。

JEV 憑證由 `TYPESAFE_API_KEY` 環境變數提供，僅供評測控制端使用；它不會傳入 provider CLI 或 native workspace。Codex／agy 使用各自登入態，認證與 model/effort 設定不寫成可公開的秘密資料。缺 JEV 憑證或 provider 認證時，尚未呼叫受測模型即停止。

## Native execution contract

整個 provider CLI 經由 `IsolationRunner` 執行。native CLI 可使用自己的工具與 provider network，但只得到乾淨的隔離 HOME 及 narrowly mapped auth-only state：Codex 使用 `.codex`，agy 使用 `.gemini`。不掛載 real HOME、hidden answers、score store，也不把 JEV key 傳給受測 CLI。candidate code 與模型工具的執行唯一 seam 仍是 `IsolationRunner`。

每題唯一共用限制是 600／1200／1800 秒 wall budget，依題目深度而定；沒有 portable turn cap。最後 30 秒保留給控制器獨立執行 public tests、擷取 diff 與保存證據。含 staged requirements 的題目在同一 workspace，使用同一 native conversation（缺少可續跑的 conversation ID 會記為執行錯誤），依序送出 initial request 與後續 phase；總 wall budget 分配給各 phase，後續資訊不會提前揭露。native tool events 與 CLI 內部回合只作觀測證據，不套用舊 controlled action-turn 語義。

原 fixture tests、pytest／harness 設定與其他指定唯讀路徑受保護；`tests/agent/**` 在該題 allowed paths 明確允許時可新增測試；disposable Git metadata 可供模型建立 checkpoint 與 rollback。控制器從一般公開檔案計算最終 diff，不執行受測 Git metadata。native events、transcript、final report、diff、獨立 public-test 結果、usage、exit 與 timing 都保存；timeout 或 interruption 也保留 partial evidence。控制器另建驗證 checkout，以可信 fixture tests／設定搭配候選 source 執行 public tests，排除 `tests/agent`，避免候選 pytest hook 干擾原始測試。新增測試與執行紀錄仍作為 JEV 證據。

## 題庫與預算

六類為 repair、diagnosis、scope、testing、recovery、audit。每類三題：多條件推理、跨元件定位、狀態演進；wall budget 分別為 600／1200／1800 秒，沒有 portable turn cap。18 題單模型最多六小時執行時間，JEV 額外計時；保留的 30 秒 reserve 不算作模型 phase 預算。提早完成可提前結束，native tool calls 與內部回合不加分。

原 frozen pilot-v1 保留作舊模式與流程驗證，正式工程評分使用新的工程情境。public requirements 是評分契約；私有參考解／部分解／錯解僅供題庫與裁判驗證，不提供給受測模型或寫入公開報告。固定 staged evidence 對所有受測設定一致。

`--pilot` 選 repair depth1、diagnosis depth2、scope depth3、testing depth1、recovery depth2、audit depth3。pilot 與自選題輸出 partial coverage，不當作完整模型成績或歷史 base。先檢查飽和、不可解、預算與裁判錯誤，再凍結正式版本；不能為了強迫分差改題。

## 計分與不確定性

固定裁判模型為 `jev-1.13.0`，裁判契約版本為 `dimension-evidence-v1`。每題四項題目專屬規準：達成要求、證據支持、限制與相容性、驗證與交付；每項有五級（0..4）。每個 dimension 各送出一個獨立的 one-Score request；四個原生 Score 平均乘以 25 即該題 0..100 分。單一 dimension 失敗時整題為 `error`、分數為 null，不平均其餘三項。每類三題等權，六類等權；重跑結果先在同題內平均。

## Dimension evidence protocol

四個 request 都保留完整的 public case snapshot，包括公開 source files、requirements、stages 與其餘題目條件；execution evidence 使用同一份原始 public execution whitelist。`evidence` view 是該 whitelist 排除 `final_diff`，供 fulfillment、constraints、verification 的 view 則只排除 `transcript`。因此後三者仍看得到 final diff，evidence 仍保留完整 transcript；四者都保留 `final_report`、全部完整 tool events、test results、status 與 error。送審不依 tool name 或 regex 篩選，也不截斷輸出。

唯一的送審層 wrapper 去重是 AGY `native_tool.output` 中重複的 `conversation_id`、`step_index`、`step_type`、`tool_name`、`duration_seconds`。完整 `tool_info`、`error`、`state` 與未知 provider fields 保留；Codex、未知 event 與其他 event payload 不篩除。raw execution archive 不變，去重只發生在 dimension request view。

每題保存 `per_dimension_results`、各 request 的 HTTP hash、bytes、usage 與 attempts。四個 HTTP body 的 hash 與 composite hash 分開記錄；composite `request_hash` 的 `request_hash_kind` 明確標示為 ordered dimension manifest digest，不把它誤稱為單一 HTTP body hash。原生四分、機率、confidence、evidence refs 與每次嘗試的錯誤也一併保存。

測試結果是 JEV 的證據，程式不再另加 deterministic 分數、封頂或完成閘。原生 confidence 與機率分布保留，confidence 不乘進總分，也不是重複測量的信賴區間。一次評測僅是初步結果；不以細小分差宣稱顯著優勝。

送審資料排除受測模型／base-target 身分、原生計費 metadata 與先前分數，保留 transcript 的角色及原文作為證據；模型若自行在回答中提到身分，這些文字仍會保留。送審 state 內重複的長字串只以可驗證的共用文字引用無損去重，原始事件另行完整封存。JEV context 超限或任一 dimension request 失敗會記錄為裁判錯誤，不截斷證據或填入零分。報告呈現規準與送審證據，不杜撰 JEV 沒有回傳的文字理由。未知實際模型快照保留 unknown；requested/resolved 不證明服務端採用值。

## 失敗與保存

模型耗盡 wall budget 或 provider 正常結束時，仍由 JEV 評估已留下的成果；耗盡預算不要求成果完整。隔離、認證、provider 傳輸、任一獨立 dimension request 或 JEV 格式／服務失敗則整題標示 error、分數 null，不能平均成功的三個 dimension；不足完整 coverage 時不發布完整總分。最後 public tests 的 passed、failed、error 分開記錄，不能代替 JEV 品質判斷。

模型及 JEV 的原生 usage 各自保存。不同 harness token 語義不得混成同一種成本；缺少計價來源時費用為未知，不是零，也不將 API 等值費用當訂閱帳單。金額運算使用 Decimal。

預設輸出為 `~/.config/paulsha-patchmud/models-score.md`。`runs/<run-id>/` 是不可變、具 digest 的 JSON 原始資料；Markdown 是鎖定後原子更新的歷史檢視。`models-score.md` 明確包含公開 cases 及每題的 public execution results；各題另包含條件、預算、四項分數／原始分布／confidence 與證據，不包含 hidden materials。執行中逐題保存 checkpoint；中斷保留已完成題目與 partial evidence。

base 只重用完整且 digest 驗證成功、題庫／規準／裁判模型與 `judge_protocol_version`／工具協定／引擎內容／harness 版本／model+effort／預算／重複次數相符的結果。native `native-engineering-v1` protocol、tool cohort、phase policy 與 `dimension-evidence-v1` judge fingerprint 和舊 controlled 或舊 `full-state-v1` 裁判 protocol／cache 分離，兩者不得互相重用。報告標示原評測日期，提供總分／分類／逐題差值。`--refresh-base` 可避開歷史重用；模型 alias 背後權重不可觀測時不宣稱是同一快照。

符合條件的歷史 target 結果也能成為後續的 base；base／target 表示該次比較的角色，並非不同的評分方法。每次命令的 target 都重新執行。

若已完成模型執行，但需要以修正後的裁判程式重審，可使用不可變封存中的完整公開證據：

```bash
python3 -m patchmud.scoring.rejudge --run BASE_RUN_ID --run TARGET_RUN_ID
```

也可只提供一個 `--run`，或用 `--output-dir DIR` 指向原封存目錄。這個入口先驗證來源 digest，不啟動受測模型、不重建題目；只重新呼叫 `jev-1.13.0` 的 `dimension-evidence-v1` 裁判。新紀錄明確標示原跑次、原執行日期、原 execution engine/protocol、來源 `full-state-v1` 或其他原 judge protocol、目前 judge engine/protocol 及「重用執行證據」，原紀錄與原裁判錯誤不會被覆寫。單獨重判 target 時，只會連結其原指定 base 的相容重判紀錄。

重判結果是 `derived-rejudgment-v1`，不進入一般 base 自動快取，也不與一般跑次直接計算差值；兩份重判紀錄需有相同的來源執行引擎、目前裁判 engine/protocol 及其餘比較條件。裁判仍失敗或 coverage 不完整時，總分仍為 null。正式原生執行若已封存，裁判契約修正後只重用其完整 execution evidence，不重新跑模型。重判 pilot／自選題也不能轉為正式總分。

## 安裝與驗證

題庫隨 Python wheel 封裝，從 repo 外執行也可使用。candidate code 與 provider CLI 只經 IsolationRunner；正式 native 評分缺 namespace 或必要 provider runtime 會拒絕執行。單元測試注入 fake model/JEV/runner，live pilot 與正式評分須有獨立原始證據。

執行環境必須提供 `bwrap`，以及可執行 pytest 的 `/usr/bin/python3`。安裝在 pipx／venv 的 pytest 不代表沙箱內的系統 Python 也能使用；缺少任一項會在模型呼叫前停止。

Python toolchain 掛載若包含安裝後的 PatchMUD 套件目錄，隔離器會遮蔽該目錄，避免私有參考答案透過 site-packages 被讀取。封存分開記錄引擎 Python／pytest 與沙箱 `/usr/bin/python3` 的版本；兩者可能不同。`dimension-evidence-v1` 的完整重新校準已通過 18 cases × 4 variants，共 288 個獨立 Score 請求。正式原生執行與重新判決的實際結果、版本及限制見驗證紀錄；離線測試或 fake adapter 綠燈不能代替實跑證據。

參考 [TypeSafe HTTP API](https://docs.typesafe.ai/api.md)、[Score](https://docs.typesafe.ai/primitives/score.md) 與 [模型版本](https://docs.typesafe.ai/models.md)。

題庫凍結版本、pilot 結果及限制見 [freeze manifest](engineering-v1-freeze.json)。裁判契約的凍結增補見 [judge amendment](engineering-v1-judge-amendment.json)。本次實作的離線／真實執行驗證記錄於 [validation record](jev-validation.md)。
