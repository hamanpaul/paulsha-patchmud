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
profile key。`run.yaml` 在開局寫入且不可變，當時沒有任何 provider 回應，所以
它的 observed plane 是開局快照。run 結束後另寫不可變的 `execution_profile.json`：
descriptor、requested、resolved 與 `profile_id` 必須和開局逐位元組一致，只有
observed plane 改用 adapter 彙總的 provider 觀測。Observed 資料不足以確認所有
條件時，`actual_condition_key` 為 `null`。report v2 逐 run 輸出 `profile_id`，
並以 profile、role、benchmark type、deck content digest 與 evaluator revision
分 cohort；詳見 [`report-contract-v2.md`](report-contract-v2.md)。

### Provider 觀測（observed model／effort）

只有 provider 自己持久化或回應的身分能把 observed model／effort 標成 known；
requested／resolved 的設定永不補洞。目前只有 codex adapter 有觀測來源：

- `codex exec` 不帶 `--ephemeral`，thread 由 codex 持久化在 `CODEX_HOME`；
  每回合取 `thread.started` 的唯一 `thread_id`，以 `codex app-server` 的
  `thread/read`（`includeTurns: false`，不 resume、不產生模型呼叫）讀回
  `model`、`reasoningEffort`、`modelProvider`。
- 一次 run 的每一回合都必須讀到身分、`modelProvider` 為 `openai`、且所有回合
  的 model／effort 一致；任一回合缺漏或不一致，該條件維持 unknown。
- provider 回報的 model 與 descriptor 不同時維持 unknown；回報的 effort 在
  descriptor grammar 內但與 resolved 不同時記成 known（實際條件不符，Cortex 會
  判 `observed-conditions-mismatch`）。
- observed `metadata.evidence_refs` 逐回合記 thread id 的 SHA-256 與回報值，
  不保存 thread id 原文或 provider payload；metadata 不進任何 key。

### Cortex qualification binding

`patchmud profile-binding <run_dir>... [--out FILE]` 讀 post-run
`execution_profile.json`，重新解析 descriptor 與三個 plane 並重算所有 key，
輸出 Cortex `cortex model qualification import --profile-binding` 讀的
PatchMUD execution-profile/v1 payload。多場 run 必須同一 resolved profile，且
observed conditions、requirements、provenance 完全一致才合併（evidence 依 run
順序合併並標 `run_id`）；缺 post-run 記錄的舊 run 一律拒收，不退回開局快照。

Cortex import 以 `adapter_for(executor).descriptor_fields()` 逐欄比對
`descriptor.adapter`。codex 的 adapter 身分（`id`、`protocol_id`、
`protocol_version`、`runtime_version`）與原生 effort 值域取自 vendored 的
`patchmud/adapters/data/cortex-execution-adapters.yaml`——Cortex
`paulsha_cortex/coordinator/data/execution-adapters.yaml` 的逐位元組副本，旁邊的
`*.provenance.json` 釘住來源 revision 與 SHA-256，載入時不符即 fail-closed。
CI 以同一 revision 的 Cortex source 跑 conformance 測試（`PATCHMUD_CORTEX_SRC`）。

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
profile key。例外是採 Cortex adapter 身分的 adapter（目前為 codex）：
`adapter` 四欄必須與 Cortex 逐字相同，PatchMUD harness runtime 字串改記在
`metadata.discovery.harness_runtime`（不進 key）；harness 實作變更由 report
cohort 的 `evaluator_revision`（全部 `patchmud/**/*.py` 的 digest）區分。CLI adapter 另以 executable bytes digest 記錄 runtime toolchain；不啟動
版本查詢程序，也不輸出本機路徑。只輸出 digest，不輸出 source 路徑。
Actual-condition key 只投影 `schema_version` 與 conditions，且只接受 observed
profile；任何必要條件 unknown 時不產 key。

Hash frame 格式為
`b"cortex.execution-profile\\0v1\\0" + domain + b"\\0" + uint64_be(length) + canonical_bytes`。
輸出格式為 `epk:v1:{domain}:{lowercase_sha256_hex}`；requested plane 使用
`request` domain，其餘使用 `resolved`、`observed` 或 `actual`。Adapter
runtime/config 版本需隨影響執行結果的 adapter 預設值一起更新。
