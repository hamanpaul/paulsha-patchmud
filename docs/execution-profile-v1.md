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

## Cortex dispatch builder lane（`patchmud dispatch-run`）

`patchmud run` 的 profile 描述的是 PatchMUD 自己的量測條件：純補全模式、read-only
sandbox、`P0T0R0` loadout，requirements 一律 unknown。這和 Cortex Manager 派工時
`_bind_workflow_execution_profile` → `make_launcher_profile` 算出的
`binding.resolved_key` 是不同的 key。`sized_dispatch: enforce` 採精確比對，所以那類
qualification 永遠不會被派工查詢用到。

`patchmud dispatch-run <encounter> --target <file>` 改在 **Cortex builder 的實際派工
條件**下量測。這樣產生的 report v2 與 binding，resolved key 會等於 Cortex 為該次目標
派工算出的 key。

### Target 檔

target 檔的格式是 `patchmud.cortex-dispatch-target/v1`，記錄 Manager 在
`_bind_workflow_execution_profile(run, step, identity, launcher)` 收到的那組輸入：

- identity：executor、model、independence domain、capabilities
- persona 與卡片執行契約：action、commit／test policy
- launcher 的 effort
- run 的 sizing band、builder pin、先前的 build steps

目前只接受 builder persona、`phase=build`、`commit_policy=required` 的 codex 派工；
不屬於這個範圍的派工一律 fail-closed 拒收。

範例檔
[`fixtures/cortex-dispatch/small-fix-subagent-build-codex-gpt-6-luna-green.json`](../fixtures/cortex-dispatch/small-fix-subagent-build-codex-gpt-6-luna-green.json)
描述的派工：

- small-fix combo 的 `subagent-build` 卡；
- 以 `--builder-executor codex --builder-model gpt-6-luna` 釘選 builder，與 deployment
  canary 相同；
- sizing band 為 green，前面沒有 build 卡。

檔內記錄的 `cortex_resolved_key` 只用於稽核。PatchMUD 不讀這個欄位產生 key，而是從
發射契約重新計算。

### 每個條件的實際作法

PatchMUD 只在量測條件確實相符時才宣告該值，不是只改 binding 上的字串。

| 條件 | 值 | 實際作法 |
|---|---|---|
| adapter／toolchain | `codex-cli`…`cortex-adapter-v1`／`{codex, cortex-adapter-v1}` | codex argv 移植自 Cortex `build_codex_argv` 的 commit-required builder 那一列：`codex exec --ignore-user-config <prompt> --json --sandbox danger-full-access --model M -c model_reasoning_effort="E" -o <last> -C <worktree>`。CI 會與真實 Cortex 逐 token 比對 |
| model | `{id, revision: "unreported"}` | 採用 Cortex 預設 descriptor 的字面值（provider 不回報 revision）。observed 以 `thread/read` 確認 |
| effort | Cortex 的解析結果 | launcher 有明示值就用它，否則用 catalog 的 `model_defaults`（gpt-6-luna 為 `max`）。observed 以 `thread/read` 確認 |
| loadout | `{builder, "1"}` | prompt 前言逐字等於 Cortex builder persona 契約。`patchmud/cortex_dispatch/data/cortex-personas.yaml` 是逐位元組 vendor 的副本並附 provenance；`render_contract_prompt` 移植後由 CI 與 Cortex 比對 |
| toolset | `[git-commit]` | 候選只取 commit 出來的 HEAD，workspace 的 `.git` 位於可寫邊界內 |
| sandbox／permissions | `workspace-write` | Cortex 對這一列送出 `danger-full-access`，由外層邊界限制；PatchMUD 用相同形狀。外層 bwrap 內，只有 workspace、專用 `CODEX_HOME`、tmpfs `/tmp` 可寫，其餘目錄唯讀；deck、hidden、runs store、使用者 HOME 都不掛入 |

### 執行環境

以下環境條件不進 key：

- **網路**：sandbox 使用 `--unshare-net`。codex 與模型下的指令只能經沙箱內的 bridge
  連到 host 端的 egress allowlist proxy，只放行 `chatgpt.com`、`openai.com` 及其子網域
  的 443。PatchMUD repo 是 public，這條規則確保 hidden 資產在網路面也拿不到。
- **CODEX_HOME**：每場 run 使用全新目錄。`auth.json` 以唯讀方式 bind 進去，codex 不讀
  使用者的 `~/.codex`（AGENTS.md、hooks、memories），也不會改寫使用者的憑證。
- **候選讀取**：HEAD、status、diff 都在沙箱內以 git 讀取，agent 控制的 repo config 不會
  在 host 執行任何指令。
- **完成判定**：比照 builder persona 的 completion obligation。沒有 commit、worktree
  不乾淨、codex 失敗或 session 錯誤，都判為 `failed:protocol`；逾時判為 `wall_clock`。
- **評分**：與 `score-diff` 同構。Workspace 以嚴格模式套用候選，碰到保護區或 harness
  設定檔的候選會被拒收，判為 `failed:protocol`；之後依序執行 public probes、hidden
  evaluator、`compute_clear`。

### requirements 的來源

| requirement | 來源與檢查 |
|---|---|
| role | 固定為 `build`，因為 builder lane 只量測 builder |
| pin | 取自 target 的 `model_chain_override.builder`。必須等於實際量測的 executor／model；未釘選時為 known `null` |
| minimum_quality | 取自 target 的 sizing band，必須是 `green`／`yellow`／`red` 之一，沒有預設值。PatchMUD 不量測 band，只負責把證據綁到該 band；證據是否足夠由 owner 在 approve 時判斷 |
| independence | `selected_domain` 取 identity 的 independence domain。`builder_domains` 從先前的 build steps 推導：`phase=build`、`gate_result=passed`、`commit_policy≠forbidden`、domain 非空 |

### observed plane

observed 只採用兩個受信任來源：

- `codex-app-server:thread/read`：model 與 effort。
- `patchmud:dispatch-launch-v1`：從實際執行過的 codex argv、bwrap argv、prompt 前言與
  `.git` 推導其餘條件。

任何一項無法確認時，該條件維持 unknown，`actual_condition_key` 為 `null`。

### Cortex conformance 測試

`tests/test_cortex_dispatch_conformance.py` 只在設定 `PATCHMUD_CORTEX_SRC` 時執行，
內容如下：

- 對同一份 target，用 Cortex 真實的 `_bind_workflow_execution_profile` 與
  `make_launcher_profile` 算出 key，斷言與 PatchMUD 產出的 key 相等。
- 比對 argv、persona 前言與卡片契約。
- 走完整個 lane（離線假 codex），接著依序執行 Cortex 的 import、operator approve、
  `qualification_policy="enforce"` 放行、revoke 後被擋下。

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
