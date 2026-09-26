# PatchMUD report v2 契約

`patchmud report --runs <glob> --out <dir>` 從 run 目錄或 RunStore `.tar` 封存
唯讀重建報告。輸出只使用 schema v2；report JSON、YAML、榜單 CSV、`runs.csv`
與 `usage.csv` 都由同一份 report/run evidence 產生。v1 report 不再輸出。

## Report 外框

`report.json` 與 `report.yaml` 具有相同欄位：

- `schema_version`: 固定為 `2`。
- `producer`: `name` 與 PatchMUD `version`。
- `generated_at`: UTC 時間；此欄不進穩定 fingerprint。
- `report_fingerprint`: 對其餘穩定 report 欄位做 canonical JSON／SHA-256；不含
  `generated_at` 與 fingerprint 自身。
- `runs_included`、`runs_skipped`、`runs`: 收錄、略過與逐場證據。
- `leaderboards`: `clear_rate`、`cost_per_clear`、`tokens_per_clear`、`qaty`、
  `eutb`、`power`、`control`、`ftr`。

建置時會驗證固定外框、run/usage 結構與 fingerprint。未知 schema version、未知
欄位集合、非有限 JSON 數值或不合法 fingerprint 會拒絕輸出。

## Run 身分與分組

每個 `runs[]` row 帶 `role`、`benchmark_type`、`profile_id`、`deck_id`、
`deck_digest`、`evaluator_revision`、量測與未量測維度、deck coverage、
`run_digest`、`artifact_digests`、終局原因及失敗來源。builder 只量測
`clear`／`power`／`protocol`；未測 planner 與 reviewer 明列 `unknown` 和原因。

榜列 cohort 的唯一分組鍵是：

```text
role + benchmark_type + profile_id + deck_digest + evaluator_revision
```

任一必要 identity 欄位不完整時，每個 run 保持獨立且標為不可排名，不能和其他
缺欄資料湊成 cohort。coverage 以 cohort 的 encounter set 對照封存的 deck encounter
set；同一 encounter 重跑多次不會增加 coverage。protocol failure 保留原本的
`end_reason`／`protocol_failed`，並明列 `failure_source: protocol`。

## Usage provenance

Run 目錄中的 append-only `usage_evidence.jsonl` 只存正規化 token 數值與 provenance，
不存 provider 原始 payload。每個欄位帶 `state`、`method`、版本化 token `unit_ref`、
`source`（provider/schema/adapter version）、`calculation` 與 subset/total 語意。
`observed`／`estimated` 才有 `value`；`unknown` 只有原因，JSON 與 CSV 都不把它
填成 `0`。欄位來源名稱保留 mapping 可追溯性，不包含原始 provider object。

每個 run 的 `usage_provenance.calls[]` 保留逐次 evidence；`fields` 是可排名的 run
彙總。只有同語意 `usage_delta` 可逐欄相加。`usage_total` 因目前沒有可去重的事件
identity，只保留逐次數值，run 彙總標成 unknown；混合 delta/total 或 subset 語意
不相容時，效率榜為 `unavailable` 並附原因。cache 與 reasoning subset 依 ledger 語意
處理，不重複加到 billed total。失敗發生前已收到的 usage 仍會先 append，再解析
模型協定回覆。

估算仍保留數值但標 `estimated` 與估算方法；成本排名需要 observed billed totals。
舊 run v1 封存若沒有 usage evidence，會以 `legacy`／`unknown` 重建，不推測數值、
不升格為 observed，也不回寫或重新封裝原始 archive。原始 v1 RunStore `.tar` 可以
直接放入 `--runs` glob；讀取器只解開 run 檔案與 evaluator bundle 的 `card.yaml`，
不會解包到使用者資料夾。

## CSV

- `<leaderboard>.csv`：各榜 `rows[]` 的表格輸出；不可排名列帶 `ranked` 或
  `non_ranking` 與理由欄。
- `runs.csv`：逐 run 欄位；巢狀 coverage、dimension、artifact digest 保留為 JSON
  字串。
- `usage.csv`：逐 call、逐 usage 欄位輸出 state/value/unit/method/source/schema/
  adapter version/quantity kind/語意與計算方式。unknown 的 `value` 欄留白。

消費者必須檢查 `schema_version == 2`，並以完整 cohort identity 找榜列；v2 改變了
必要分組語意，不能套用只接受 report v1 或以 `(model, loadout)` 查找的讀取器。
