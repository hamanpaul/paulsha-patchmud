---
type: fix
scope: sandbox
---
相對 --runs-root 時 checkpoint 因相對 GIT_DIR 找不到 shadow repo 而失敗——Workspace 絕對化 shadow_dir/encounter_dir。
