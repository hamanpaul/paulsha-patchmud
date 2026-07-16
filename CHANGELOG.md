# Changelog

本專案所有重大變更都會記錄在此檔案。

格式基於 [Keep a Changelog 1.1.0](https://keepachangelog.com/zh-TW/1.1.0/)，
本專案遵循 hamanpaul project policy v1.0.12。

## [Unreleased]

### Added
- **Repo scaffold**：policy 1.0.12 合規骨架（`.paul-project.yml`、agent symlink、policy-check / tests workflows）、`patchmud` 套件骨架與 console entry。
- **PatchMUD 研究資產遷入**（自 `paulsha-cortex` feature/patchmud-spec）：研究報告 v0.2、implementation spec v1.1（含 codex gpt-5.6-sol 對抗審查 21 條 findings 之整合紀錄）、MVP 實作計劃（21 tasks，milestone A–D）。
- **中文 MUD 呈現與可玩性增補**（spec §5.4 / plan Task 22–23）：zh-TW render pack（敘事中文、協定關鍵字英文）、`render_language` 進 treatment、`patchmud play` 人類對局模式（`human: true` 不進 ranked）、`patchmud watch` 逐回合中文戰報 viewer。
