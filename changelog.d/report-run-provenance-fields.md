# report-run-provenance-fields

- **report `runs[]` 逐列增列 `encounter`／`end_reason`／`protocol_failed`**（issue #24）——封存既有事實（`run.yaml` 的 `encounter_dir`、`result.yaml` 的終局欄位）的透傳，report 層 fail-closed 驗證值域。動機：下游（paulsha-cortex#452 profile 巷道）需要 deck 全覆蓋的精確驗證（原本只能用 `runs >= encounter_count` 的必要非充分判準），並把「未通關因協定失敗」與「未通關因修不好」分開——#21 顯示 cost-smoke3 的 4 場失敗全是 unified diff 解析失敗，格式噪音不得被誤讀成能力缺陷。`schema_version` 維持 1：純加欄（下游對非 1 版本 fail-closed，bump 反而是破壞性變更）。
