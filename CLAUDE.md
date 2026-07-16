policy_version: 1.0.12

# Agent Policy Checklist

本 repo 受 hamanpaul project policy v1.0.12 管轄。
所有 agent 進入 session 時，必須依下列 checklist 行動。

## 本 repo 的 profile
- policy_profile: `flat` （見 `.paul-project.yml`）
- policy_version: `1.0.12`

## 本 repo 定位
- `paulsha-patchmud` 是 PatchMUD 評測引擎：成本優先、純文字回合制的 coding-agent benchmark（量測閉環，不是生產閉環）。
- 對 `paulsha-cortex` / `paulsha-hippo` 零 runtime 依賴；輸出以檔案契約（run 封存、報告）供下游使用。
- 排名資料流零 LLM 裁判；hidden 資產永不進 sandbox；ranked run 的隔離與校準參數 fail-closed。
- Spec：`docs/superpowers/specs/2026-07-16-patchmud-mvp-design.md`；實作計劃：`docs/superpowers/plans/2026-07-16-patchmud-mvp.md`。

## 動工前
- [ ] 確認當前分支不是 `main`
  - 若在 `main`，先開 `feature/<slug>` 分支
  - 若在 `feature/*`，可直接工作，或再開 `wt/<feature>/<subtask>`
- [ ] 若本任務跨多個子項，先建議用 `git worktree` 拆開

## 改 code 時
- [ ] 同一 PR 必須同步更新 `CHANGELOG.md [Unreleased]`
- [ ] 除非可明確標示為 docs-only / test-only / chore，否則不得省略 CHANGELOG
- [ ] code_paths 涵蓋的檔案變動皆視為 code change

## 改版號時（release 觸發時）
- [ ] 嚴格遵循 `<MAJOR>.<MINOR>.<PATCH>[-fix.N]`
- [ ] PATCH bump 對應 profile：
  - `stage-driven`: 一個 stage 落地
  - `flat`: 一個 feature batch 完成
- [ ] MINOR bump 需滿足：feature 群組全 landed + 7 天無 hotfix
- [ ] MAJOR bump 需使用者明確核可

## 完成任務（claim done）前
- [ ] `CHANGELOG.md [Unreleased]` 有對應 entry（或 PR 標 `skip-changelog` + 理由）
- [ ] `VERSION` 內容與意圖一致（release label PR 才可偏離 latest tag）
- [ ] `.github/pull_request_template.md` checklist 全勾
- [ ] `python3 -m policy_check --repo .` 無任何 failure
- [ ] 若本 repo 有額外測試 / lint / build 指令，需一併通過
- [ ] 若跳過任何檢查，PR 必須帶對應豁免 label + 理由

## 禁止
- 直接 commit 到 `main`
- 建立不符合命名規則的分支（必須 `feature/<slug>` 或 `wt/<feature>/<subtask>`）
- 發明新 `policy-exempt:*` label（**只能用 policy 列舉的白名單**）
- 把 agent symlink（AGENTS.md / GEMINI.md / .github/copilot-instructions.md）還原成獨立複本（`agent_files.mode: symlink` 下 R-14 會 FAIL）

## Exemption Labels 白名單
僅允許使用以下 labels 豁免對應規則（其他一律視同未豁免）：
- `policy-exempt:readme-sections` — R-02 README 必備段落
- `policy-exempt:changelog-format` — R-04 CHANGELOG 格式
- `policy-exempt:pr-title` — R-10 PR title conventional-commit 格式
- `policy-exempt:branch-name` — R-12 分支來源規則
- `policy-exempt:agent-files` — R-13 agent convention files 存在
- `policy-exempt:cli-help` — R-16 CLI help 同步
- `policy-exempt:issue-link` — R-17 PR↔issue closing-keyword 形式
- `policy-exempt:docs-sync` — R-18 docs 對齊（WARN）
- `policy-exempt:ci-tests` — R-19 CI 必須跑測試
- `policy-exempt:secret-scan` — R-21 機密掃描
- `policy-exempt:doc-reference` — R-22 doc 懸空引用
- `policy-exempt:engine-pin` — R-23 引擎 pin attestation
- `policy-exempt:moc-alignment` — R-24 MOC 對齊
- `skip-changelog` — R-09 code 變動要求 CHANGELOG entry（特殊用途，需附理由）
- `wip` — R-11 自動通過 PR body checkbox 未全勾（work in progress）

R-14（agent symlink 單一真檔）與 R-20（workflow policy_version 同步）不設豁免。

## 本 repo 額外紀律（spec 派生）
- probe 判定三態 `passed / failed / error`；`error` 永不等同 `failed`。
- 執行 candidate code 的唯一 seam 是 `IsolationRunner`；unit tests 一律注入 fake，不啟真 namespace、不打真 API。
- 金額計算全程 `decimal.Decimal`。
- 校準參數只能由 `analysis/registered/estimators.yaml` 的 estimator 產出；凍結後拒絕覆寫。
- deck `hidden/` 內容不得出現在任何 sandbox 可見路徑、log render 或公開封存。
