# Execution profile v1

PatchMUD 以獨立的 `patchmud.execution_profile` 實作 execution-profile v1，與
Cortex 交換 JSON-compatible descriptor/profile wire 資料；PatchMUD 不匯入 Cortex。
schema 固定為 v1，未知欄位、版本、型別或 descriptor/profile 不相符時拒絕解析。

## Descriptor 與 profile

Descriptor 欄位為 `schema_version`、`id`、`adapter`、`model`、`effort_grammar`、
`provenance`、`metadata`。Adapter 宣告 adapter id、協定 id／版本與 runtime
版本；model 保存解析後 id 與 revision。Effort grammar 保留 adapter 原生型別，
可用 `none`、`string`、`integer`、`number`、`boolean`、`null`、`array` 或
`object` 描述；不把不同 adapter 的同名 effort 當成等價值。

每個 profile 只屬於 `requested`、`resolved`、`observed` 其中一個 plane。條件
固定為 `adapter`、`model`、`effort`、`loadout`、`toolset`、`sandbox`、
`permissions`、`toolchain`。各值以 `known`、`unknown` 或 `not_applicable`
tagged object 表達；只有 effort grammar 為 `none` 時 effort 可用
`not_applicable`。未經 provider 回報的 model revision／effort 保持 unknown，
不以送出的設定冒充實際觀測。

`patchmud run` 將 descriptor、三個 plane、`profile_id` 及每個 plane 的 key
放在 `run.yaml` 的 `execution_profile` 欄位。`profile_id` 等於 resolved
profile key。Observed 資料不足以確認所有條件時，`actual_condition_key` 為
`null`。report v2 逐 run 輸出 `profile_id`，並以 profile、role、benchmark type、
deck content digest 與 evaluator revision 分 cohort；詳見
[`report-contract-v2.md`](report-contract-v2.md)。

可讀／輸出版本由 `patchmud schema --json` 宣告；跨 repo producer fixtures 與
manifest 位於 `fixtures/golden/`，舊資料處理依
[`schema-migration-v1-v2.md`](schema-migration-v1-v2.md)。

## Canonical key

Canonical bytes 使用 typed JSON 表示原生型別，字串以 UTF-8 編碼、物件鍵按
UTF-8 bytes 排序；`toolset` 與 `permissions` 依 item canonical bytes 排序並
去重。整數、浮點數、布林、字串、陣列、物件與 null 有不同型別標記，非有限
浮點數拒收。

Profile key 對下列 canonical projection 計算 SHA-256：
`schema_version`、`plane`、正規化後的 `conditions` 與 `requirements`。Metadata
不進 profile key，因此單獨變更 timestamps 或 pricing provenance 不改能力 key。
Adapter capability descriptor 會在 `metadata.discovery.model_parameters` 記錄
resolved model parameters，將其 canonical JSON digest 與 resolver／adapter source
digest 納入 `adapter.runtime_version`，確保會影響結果的參數或實作變更會改
profile key。CLI adapter 另以 executable bytes digest 記錄 runtime toolchain；不啟動
版本查詢程序，也不輸出本機路徑。只輸出 digest，不輸出 source 路徑。
Actual-condition key 只投影 `schema_version` 與 conditions，且只接受 observed
profile；任何必要條件 unknown 時不產 key。

Hash frame 格式為
`b"cortex.execution-profile\\0v1\\0" + domain + b"\\0" + uint64_be(length) + canonical_bytes`。
輸出格式為 `epk:v1:{domain}:{lowercase_sha256_hex}`；requested plane 使用
`request` domain，其餘使用 `resolved`、`observed` 或 `actual`。Adapter
runtime/config 版本需隨影響執行結果的 adapter 預設值一起更新。
