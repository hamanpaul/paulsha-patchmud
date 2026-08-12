---
type: change
scope: policy
---
同步 hamanpaul project policy 1.0.15 → 1.0.17：`.project-policy.yml`、`Policy Check` workflow（`uses:` 與 `policy_engine_ref` 雙重釘選至 `9e7fabbf0b5eea9ad933fa6798764b723934a0b7`）、canonical `CLAUDE.md`（symlink `AGENTS.md` / `GEMINI.md` / `.github/copilot-instructions.md` 自動跟隨）與 `README.md`／`CHANGELOG.md` 內的版本引用全數同步至 v1.0.17。1.0.16／1.0.17 對下游 repo 未新增或變更任何規則，僅引擎自身的 distribution identity、runtime bundle 與 release workflow 修正；其中 1.0.16 引入的**引擎版本 gate**（執行中引擎版本與 repo 宣告的 `policy_version` 不符即 fail-loud）正是本次同步的實益——版本對齊後，本機 `policy_check` 預檢才能與 CI 判定一致。
