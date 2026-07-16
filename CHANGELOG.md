# Changelog

本專案所有重大變更都會記錄在此檔案。

格式基於 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-TW/1.1.0/)，
本專案遵循 hamanpaul project policy v1.0.12。

## [Unreleased]

### Added
- **MVP milestone A（離線評分核心）**：
  - Task 1 deck 契約與 fixture 物化——`IssueCard` frozen dataclass（spec §4.2 全欄位）、`load_card` fail-closed schema 驗證（缺必填欄位／`expected_paths ⊄ allowed_paths`／public-hidden 路徑重疊／未知 `schema_version` → `DeckError`）、`materialize_repo` deterministic 物化（固定 author、epoch 0 timestamp、單一 initial commit、hidden 資產永不進 worktree）、`tests/fixtures/mini_encounter` 最小 encounter fixture（含 hidden probe、`reference.patch`、`reference_timings.yaml`、`provenance.yaml`）。
  - Task 2 namespace 隔離執行器（spec §7）——`IsolationRunner`（唯一執行 candidate code 的 seam；`run` 回 `Execution(exit_code/stdout/stderr/wall_ms/cpu_ms/timed_out)`）、`build_bwrap_argv` bind allowlist（worktree rw、toolchain ro、新鮮 tmpfs `/tmp`；`--unshare-net/--unshare-pid/--die-with-parent`；env 只留 `PATH/LANG/LC_ALL/TMPDIR` 四鍵白名單）、timeout 強制終止整個 process group、`capabilities()` 一次性 `bwrap --unshare-all` 探測快取（失敗回全 False，供 ranked/pilot fail-closed）、真 bwrap 整合測試（allowlist 外路徑讀取失敗、無網路、PID namespace；環境無 bwrap 自動 skip）。
  - Task 3 Workspace（spec §7）——`git apply` 嚴格模式 patch stack（`--check` 先驗、無 fuzz/3way）、production/test patch 路徑規則（保護區 `tests/public|starter`、`benchmark`、`.git` fail-closed 拒收；test patch 限 `tests/agent/**`）、`restore_protected()` 以 deck 原始 bytes 還原保護區、`rollback()` 重放 stack、deterministic shadow checkpoint（相同內容 SHA 恆定、跨 workspace 可重現）、`reverted_loc` 追蹤自我撤銷行數。
- **Repo scaffold**：policy 1.0.12 合規骨架（`.paul-project.yml`、agent symlink、policy-check / tests workflows）、`patchmud` 套件骨架與 console entry。
- **PatchMUD 研究資產遷入**（自 `paulsha-cortex` feature/patchmud-spec）：研究報告 v0.2、implementation spec v1.1（含 codex gpt-5.6-sol 對抗審查 21 條 findings 之整合紀錄）、MVP 實作計劃（21 tasks，milestone A–D）。
- **中文 MUD 呈現與可玩性增補**（spec §5.4 / plan Task 22–23）：zh-TW render pack（敘事中文、協定關鍵字英文）、`render_language` 進 treatment、`patchmud play` 人類對局模式（`human: true` 不進 ranked）、`patchmud watch` 逐回合中文戰報 viewer。
