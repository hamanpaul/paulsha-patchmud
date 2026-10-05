---
type: feat
scope: cortex-dispatch
---
新增 `patchmud dispatch-run`（Cortex dispatch builder lane，paulsha-cortex#842）：依 Cortex 目標派工的 builder 條件（Cortex codex argv、逐字 builder persona 契約、外層 workspace-write 沙箱與 egress allowlist）量測，execution profile 的 resolved key 等於 Cortex Manager 為該派工算出的 `binding.resolved_key`；附與 Cortex 真實程式逐項比對的 conformance 測試。
