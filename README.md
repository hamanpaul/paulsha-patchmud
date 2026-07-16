# paulsha-patchmud

**PatchMUD**：成本優先的純文字回合制 coding-agent 評測框架——以軟體 issue 為關卡、程式代理模型為玩家、patch 為攻擊動作的 MUD 式評測引擎。

## 定位

- 量測閉環（laboratory），不是生產閉環：frozen fixture、deterministic 評分、可位元重播。
- 對 `paulsha-cortex` / `paulsha-hippo` 零 runtime 依賴；評測輸出以檔案契約供下游（如 model roster/routing）使用。
- 排名資料流零 LLM 裁判：所有關鍵結果由 artifact、測試、版本差異與可稽核事件日誌決定。
- 三維角色能力：經濟（Economy 55%）、火力（Power 25%）、控場（Control 20%），另設不重複計分的 token-efficiency 稽核指標。

研究設計、implementation spec 與實作計劃見 `docs/`：

- 研究報告：`docs/PatchMUD_research_report_zh-TW_v0.2.md`
- Spec（v1.1，經 codex gpt-5.6-sol 對抗審查）：`docs/superpowers/specs/2026-07-16-patchmud-mvp-design.md`
- 實作計劃（21 tasks）：`docs/superpowers/plans/2026-07-16-patchmud-mvp.md`

## Install

```bash
python -m pip install -e ".[test]"
```

執行 candidate code 的隔離層需要 [bubblewrap](https://github.com/containers/bubblewrap)（`bwrap`）；無 namespace 能力時 ranked / pilot run 會 fail-closed 拒絕啟動，dev run 以 degraded 模式執行。

## Usage

MVP CLI（依實作計劃逐步落地）：

```bash
patchmud validate-deck decks/pilot-v1          # deck 契約與 fixture 驗證
patchmud score-diff --encounter <dir> --diff <file>   # 離線評分（milestone A）
patchmud run --encounter <dir> --model <cfg> --loadout P0T0R0   # 單場 encounter
patchmud pilot --deck pilot-v1 --models models.yaml --seed 42   # forced loadout 矩陣
patchmud replay <run_dir> [--l2]               # 兩級重播驗證
patchmud report --runs "runs/*"                # 多榜研究報告
```

## 開發

- 分支：`feature/<slug>` 或 `wt/<feature>/<subtask>`；禁止直接 commit `main`。
- 每個 code PR 同步更新 `CHANGELOG.md [Unreleased]`，並通過 `python3 -m policy_check --repo .` 與 `python3 -m pytest -q`。
- 本 repo 受 hamanpaul project policy v1.0.12 管轄（`tier: shareable`），詳見 `CLAUDE.md`。
