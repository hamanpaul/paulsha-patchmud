paulsha-cortex#842：

- codex 的 execution-profile descriptor adapter 身分與 effort 值域改取 vendored Cortex `execution-adapters.yaml`（provenance 釘 revision 與 SHA-256），與 Cortex `descriptor_fields()` 逐欄一致；省略 effort 仍為 `high`，其他 adapter 身分不變。
- codex 不再帶 `--ephemeral`，每回合以 `codex app-server` `thread/read` 讀回 provider 持久化的 model／reasoningEffort；全部回合一致才把 observed model／effort 標成 known，run 結束後另寫不可變的 `execution_profile.json`。
- 新增 `patchmud profile-binding`，重算 key 後輸出 Cortex `model qualification import --profile-binding` 可讀的 binding；新增以 Cortex 真實 import／approve blocker 程式驗收的 conformance e2e，CI 以 vendored revision checkout Cortex source 執行。
