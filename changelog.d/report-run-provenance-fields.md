---
type: feat
scope: report
---
report `runs[]` 逐列增列 `encounter`／`end_reason`／`protocol_failed`（issue #24）——封存既有事實的透傳（`run.yaml` 的 `encounter_dir`、`result.yaml` 的終局欄位），report 層 fail-closed 驗證值域，`schema_version` 維持 1（純加欄）。下游（paulsha-cortex#452 profile 巷道）得以做 deck 全覆蓋精確驗證，並把「未通關因協定失敗」與「未通關因修不好」分開——#21 顯示格式噪音不得被誤讀成能力缺陷。
