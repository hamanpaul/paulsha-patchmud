# PatchMUD schema v1／v2 相容與遷移

本文說明目前 producer 的讀寫能力。版本欄位必須精確比對；未知版本一律拒收，
不得用缺欄預設值、欄位猜測或改寫原封存來「修復」舊資料。

## 相容矩陣

| 資料契約 | v1 讀取 | v2 讀取 | 新輸出 | 舊資料處理 |
|---|---|---|---|---|
| Execution profile | 支援 | 不支援 | v1 | profile 缺席代表 `legacy/unknown`；requested 不推定為 observed |
| Usage provenance | 支援 | 不支援 | v1 | evidence 缺席時以 `legacy/unknown` 於記憶體重建；不補 0、不回寫 |
| RunStore `run.yaml` | 支援 | 不支援 | v1 | 缺 profile 欄位可讀，profile identity 保持 unknown |
| RunStore `result.yaml` | 支援 | 不支援 | v1 | 舊結果照讀；未知版本 fail-closed |
| Event／ledger | 支援 | 不支援 | v1 | 舊紀錄唯讀；schema、序號或欄位不符即拒收 |
| Report | 保留讀取 | 支援 | **只輸出 v2** | v1 只以 opaque payload 讀入並原樣保留，不轉成 v2 或排名資格 |

`patchmud report` 由 run 封存重建時只產 report v2。舊 report v1 沒有完整的
profile／role／usage provenance／coverage cohort，不能單靠舊榜列升格為新版資格；
若要更新，應從原始 run 封存重建。`read_report_document()` 可驗證並原樣回傳 v1
外框，不能把它當成 `validate_report_v2()` 已通過的資料。

`patchmud schema --json` 回報 profile、usage、report 的讀取與輸出版本；這是
檔案契約能力宣告，不是 profile qualification 或 Cortex roster 核可。

## Fail-closed 規則

- schema version 必須是明確支援的整數。未知版本、必要欄位不符、重複或不合法
  evidence 一律拒收；不可把它改標成目前版本後重試。
- execution profile 的 requested／resolved／observed 是獨立事實。舊封存缺 profile
  時使用 `legacy/unknown`，不得從 model 字串、argv、report 時間或成績補出 profile。
- 舊 run 沒有 `usage_evidence.jsonl` 時，report builder 只從 ledger 的 turn／role
  建立 unknown evidence。任何 token 欄都不帶數值；原始 run 目錄或 tar 不會被改寫。
- report v1 透過相容讀取器以舊 payload 原樣保留，不能和 v2 榜列合併，也不能由
  parser 自動改成 v2。report v2 仍須通過固定欄位、coverage、usage 和 fingerprint
  驗證。
- schema version 大於支援值時拒絕；不做向前相容猜測，不忽略未知欄位來產生排名。

## Golden fixtures 與 manifest

`fixtures/golden/` 提供 execution-profile v1、usage-provenance v1、report v2 的
正反例，以及可唯讀載入的 run／result／report v1 範例。`manifest.json` 記錄
`source_revision`、schema versions，以及每個 fixture 的相對路徑、契約、案例類型
和 SHA-256。新增或修改 fixture 後，在 repo 根目錄執行：

```sh
python -m patchmud.fixture_manifest --write
python -m patchmud.fixture_manifest --check
```

`--write` 預設把目前 Git `HEAD` 寫為 fixture 來源 revision；可用
`--source-revision <40 位 SHA>` 指定。manifest 使用固定檔案白名單，漏列、增列或
內容 digest 不同步都會讓驗證失敗。公開資料的安全測試會比對本 repo hidden fixture
原始 bytes，並掃描憑證形狀、個人路徑與使用者名稱。

其他 repo 可直接讀取 `fixtures/golden/manifest.json`，先核對 digest，再用自身的
consumer parser 驗證正反例。PatchMUD 提供 producer fixtures；不宣稱 Cortex consumer
已實作或跨 repo 接線已完成。
