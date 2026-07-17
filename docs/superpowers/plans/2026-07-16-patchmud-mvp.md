# PatchMUD MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> 依 task 順序執行；每個 task 先取得指定 RED，再做最小實作、跑局部測試並 commit。不得順手實作 spec §14 的 deferred workstreams。

**Goal:** 在新 repo `paulsha-patchmud` 落地 spec v1.1 的 Phase 1–4：deterministic 回合制 coding-agent 評測引擎（deck、sandbox、evaluator、ledger、turn engine、strategy enforcer、issue queue、metrics、pilot runner），全程零 LLM 裁判、fail-closed、可兩級重播。

**Architecture:** 分層單向依賴：`deck → sandbox → evaluator/ledger → engine → metrics → cli`。engine 只經 `sandbox` 介面碰檔案系統、只經 `adapters` 碰模型；`metrics` 只讀 `store` 落盤資料。所有跨層資料結構帶 `schema_version`，schema 不符一律 fail-closed。

**Tech Stack:** Python 3.11+、stdlib（`subprocess/json/pathlib/hashlib/dataclasses`）、PyYAML、pytest、git CLI、bubblewrap（`bwrap`）、ruff（pinned）。

**Source spec:** 本 repo `docs/superpowers/specs/2026-07-16-patchmud-mvp-design.md`（以下引用 §N 均指該 spec）。

## Global Constraints

- 落地 repo：本 repo `paulsha-patchmud`（已於 2026-07-16 確認並 scaffold）；對 `paulsha-cortex` / `paulsha-hippo` 零 runtime 依賴。
- 排名資料流零 LLM 裁判；reviewer finding 不進 queue / Control（§6.2）。
- 所有會執行 candidate code 的動作必須經 mount+net+pid namespace 隔離；namespace 不可用時 ranked/pilot run 拒絕啟動（§7）。
- hidden 資產永不進 sandbox worktree、永不進 bind allowlist（§2、§7）。
- `Clear` 只有 §5.2 的唯一布林公式一種算法。
- 計費一律以 billed totals；互斥 token 欄位不可得記 `NA` 不記 0；NA 聚合傳染（§10.1）。
- 校準參數（C_ref、D、τ、EuTB B）只能由 `analysis/registered/estimators.yaml` 的 estimator 產出（§10.4）。
- 敘事文字一律出自 zh-TW render pack；命令關鍵字與 artifact 格式維持英文（§5.4）；`render_language` 進 treatment。
- 每個 code task 同步更新新 repo 的 `CHANGELOG.md [Unreleased]`；Task 1 建 umbrella entry，之後只在行為實質改變時補充。
- 每個 task 結束跑 `python3 -m pytest -q` 全綠 + `python3 -m policy_check --repo .` 零 fail 才可 commit claim done。

## Cross-task invariants

1. probe 判定結果只有 `passed / failed / error` 三態；`error` 永不等同 `failed`（TDD valid red 依賴此區分，§6.1）。
2. 任何 probe 執行都以 deck 原始 bytes overlay 還原保護區後進行（§7）。
3. `IsolationRunner` 是唯一執行 candidate code 的 seam；unit tests 一律注入 fake runner，不啟真 namespace、不打真 API。
4. store 落盤為 append-only JSONL + immutable YAML；重算路徑（metrics、replay）禁止寫回 run 目錄的原始事件。
5. 每個 author turn 恰記一筆 turn event、一個 checkpoint commit；ReviewerSubcall 嵌在所屬 turn event 內。
6. 金額計算全程 `decimal.Decimal`，禁止 float 累加。

## Milestone 對應

| Milestone | Tasks | Spec 章節 | 交付 |
| --- | --- | --- | --- |
| A 離線評分核心 | 0–8 | §2–4、§7、§9、§10.1–10.2、§12.1 | 給定手工 diff 可完整評分＋封存 |
| B 回合引擎與策略 | 9–13 | §5、§6 | 真模型可打完一場 encounter |
| C Flooding 與 metrics | 14–17 | §8、§10.3、§12.2 | Control/效率指標＋兩級 replay |
| D Pilot | 18–21 | §10.4–10.5、§11、§13 | 8×8×N 矩陣可跑、校準凍結 |
| E 中文可玩性 | 22–23（Task 10 已含 zh-TW render pack） | §5.4 | 人可親自玩（play）、可觀戰（watch） |

---

### Task 0: Scaffold `paulsha-patchmud` repo

**Files:**
- Create: `~/prj_pri/paulsha-patchmud/`（`git init`）：`pyproject.toml`、`.paul-project.yml`（`policy_profile: flat`、`tier: shareable`、`agent_files.mode: symlink`、`conventions_engine.repo: hamanpaul/paulsha-conventions`）、`README.md`、`CHANGELOG.md`、`VERSION`（`0.1.0`）、`CLAUDE.md` + 三個 symlink、`.github/workflows/`（policy-check、tests 骨架，pin 最新 conventions 版本）
- Create: `patchmud/__init__.py` 與 `deck/ engine/ sandbox/ ledger/ evaluator/ metrics/ adapters/ store/` 空子套件、`tests/__init__.py`

**Interfaces:**
- Produces: 可安裝套件 `patchmud`（`pip install -e .`）、`patchmud` console entry（暫回 exit 0）。

> **狀態：已完成（2026-07-16，隨文件遷移一併 scaffold）。**

- [x] **Step 1:** 依 `paulsha-conventions` 模板建立上述檔案；`pyproject.toml` 宣告 `requires-python = ">=3.11"`、deps `pyyaml`、dev deps `pytest ruff`。
- [x] **Step 2:** Run: `python3 -m pip install -e . && python3 -c "import patchmud"`；Expected: 成功。
- [x] **Step 3:** Run: `python3 -m policy_check --repo .`；Expected: 零 fail。
- [x] **Step 4:** Commit: `chore: scaffold paulsha-patchmud package skeleton`（首個 commit 在 `main`，其後全部工作在 `feature/mvp-<task>` 分支）。

---

### Task 1: Deck 契約與 fixture 物化

**Files:**
- Create: `patchmud/deck/model.py`、`patchmud/deck/loader.py`、`patchmud/deck/materialize.py`
- Test: `tests/deck/test_card_schema.py`、`tests/deck/test_materialize.py`
- Create: `tests/fixtures/mini_encounter/`（最小可用 encounter：`card.yaml`、`repo/`（一個 20 行模組＋starter test＋public test）、`hidden/`（1 個 hidden probe、`reference.patch`、`reference_timings.yaml`）、`provenance.yaml`）

**Interfaces:**
- Produces: `IssueCard`（frozen dataclass，§4.2 全欄位）、`load_card(path: Path) -> IssueCard`（schema 錯誤 raise `DeckError`）、`materialize_repo(encounter_dir: Path, dest: Path) -> FrozenRepo`，`FrozenRepo.sha: str`。

- [x] **Step 1: RED** — 測試鎖定：缺必填欄位 / `expected_paths ⊄ allowed_paths` / public、hidden 路徑重疊 → `DeckError`；合法 card 全欄位 round-trip；`materialize_repo` 兩次呼叫產生**相同** commit SHA（固定 author、epoch 0），且 dest 內不存在 `hidden/` 任何檔案。
  Run: `python3 -m pytest -q tests/deck/`；Expected: FAIL（模組不存在）。
- [x] **Step 2:** 實作 `model/loader/materialize` 最小版本。
- [x] **Step 3:** Run 同上；Expected: PASS。
- [x] **Step 4:** Commit: `feat(deck): issue card contract and deterministic fixture materialization`。

---

### Task 2: Namespace 隔離執行器

**Files:**
- Create: `patchmud/sandbox/isolate.py`
- Test: `tests/sandbox/test_isolate.py`（unit，fake `bwrap`）、`tests/sandbox/test_isolate_integration.py`（真 `bwrap`，無則 `pytest.skip`）

**Interfaces:**
- Produces: `IsolationRunner.run(argv: list[str], cwd: Path, timeout_s: float) -> Execution`（`Execution`: `exit_code/stdout/stderr/wall_ms/cpu_ms/timed_out`）、`IsolationRunner.capabilities() -> Capabilities(mount_ns, net_ns, pid_ns: bool)`、`build_bwrap_argv(worktree, toolchain_ro, argv) -> list[str]`。

- [x] **Step 1: RED** — 測試鎖定：`build_bwrap_argv` 產出的 bind 參數**只含** worktree（rw）、toolchain（ro）、tmpfs `/tmp`，且 `--unshare-net --unshare-pid --die-with-parent` 存在；env 只剩白名單四鍵（§7）；timeout 觸發 `timed_out=True` 且 child 被終止；capabilities 探測失敗回全 False。
  Run: `python3 -m pytest -q tests/sandbox/test_isolate.py`；Expected: FAIL。
- [x] **Step 2:** 實作；capabilities 以一次性 `bwrap --unshare-all -- true` 探測並快取。
- [x] **Step 3: 整合 RED→PASS** — integration 測試在真 bwrap 內執行探測程式：讀取 allowlist 外路徑（模擬 deck `hidden/`、`$HOME`）必須失敗、無網路（connect 立即失敗）。Run: `python3 -m pytest -q tests/sandbox/`；Expected: PASS（或環境無 bwrap 時 unit PASS + integration skip）。
- [x] **Step 4:** Commit: `feat(sandbox): bubblewrap isolation runner with bind allowlist`（acceptance「隔離」第 1 條的基礎，§13）。

---

### Task 3: Workspace：patch stack、保護區、checkpoints

**Files:**
- Create: `patchmud/sandbox/workspace.py`
- Test: `tests/sandbox/test_workspace.py`

**Interfaces:**
- Consumes: `FrozenRepo`（Task 1）。
- Produces: `Workspace.apply_patch(diff: str, kind: Literal["production","test"]) -> ApplyResult`、`.rollback() -> bool`、`.checkpoint() -> str`（shadow bare repo commit SHA）、`.cumulative_diff() -> str`、`.diff_stats() -> DiffStats(added, deleted, files, reverted_loc)`、`.restore_protected()`。

- [x] **Step 1: RED** — 鎖定：`git apply` 嚴格模式（fuzz patch 被拒、worktree 不變）；`kind="test"` 只允許 `tests/agent/**`，`kind="production"` 觸及 `tests/public|starter/**`、`benchmark/**`、`.git` → `ApplyResult.rejected`；`restore_protected()` 以 deck 原始 bytes 還原保護區（先手動污染再驗還原）；`rollback` 恢復上一 patch 前狀態；`checkpoint` 對相同內容回穩定 SHA；`reverted_loc` 正確計算「revert 自己先前新增行」案例。
  Run: `python3 -m pytest -q tests/sandbox/test_workspace.py`；Expected: FAIL。
- [x] **Step 2:** 實作（patch stack 為 list[applied diff]，rollback = 重放到 n-1）。
- [x] **Step 3:** Run 同上；Expected: PASS。
- [x] **Step 4:** Commit: `feat(sandbox): workspace patch stack, protected paths, shadow checkpoints`。

---

### Task 4: Probe runner 與 turn-0 baseline

**Files:**
- Create: `patchmud/sandbox/probes.py`
- Test: `tests/sandbox/test_probes.py`

**Interfaces:**
- Consumes: `IsolationRunner`（注入）、`Workspace`、`IssueCard`。
- Produces: `ProbeSuite.from_card(card) -> ProbeSuite`、`ProbeSuite.run(workspace, subset=None) -> ProbeResults`；`ProbeResults[probe_id] -> ProbeOutcome(status: passed|failed|error, cases_total, cases_passed, failure_fingerprints: list[str], wall_ms, cpu_ms)`；`ProbeResults.transitions(prev) -> list[Transition(probe_id, green_to_red | red_to_green)]`。

- [x] **Step 1: RED** — 以 fake runner 餵 pytest 輸出 fixture 鎖定：assertion fail → `failed`＋fingerprint（異常類型＋斷言訊息首行）；collection/import error → `error`；case 計數正確；`transitions` 相對前次結果產生正確 green_to_red；執行前必呼叫 `restore_protected()`（以 spy 驗證，§7）。
  Run: `python3 -m pytest -q tests/sandbox/test_probes.py`；Expected: FAIL。
- [x] **Step 2:** 實作：pytest 以 `--json-report`（或 `junitxml`）在隔離內執行、解析三態與 case 級結果。
- [x] **Step 3:** Run 同上＋`mini_encounter` 整合（turn-0 baseline：materialize 後 run 全套，MAIN probe 紅、starter 綠）；Expected: PASS。
- [x] **Step 4:** Commit: `feat(sandbox): three-state probe runner with baseline and transitions`。

---

### Task 5: Hidden evaluator、hard gates、Power rubric

**Files:**
- Create: `patchmud/evaluator/evaluate.py`、`patchmud/evaluator/power.py`、`patchmud/evaluator/gates.py`
- Test: `tests/evaluator/test_power.py`、`tests/evaluator/test_gates.py`

**Interfaces:**
- Consumes: `ProbeSuite`/`ProbeOutcome`、`IssueCard`、`DiffStats`。
- Produces: `evaluate_final(card, frozen, final_diff, runner) -> FinalEvaluation(probe_outcomes, power: PowerReport, gates: GateResult)`；`PowerReport(functional, robustness, compatibility, maintainability, runtime_efficiency, total)`；`GateResult(critical_pass: bool, power_cap: int|None, run_invalid: bool)`。evaluator 使用**獨立 checkout**（frozen + final diff），非 agent worktree。

- [x] **Step 1: RED（rubric 公式逐條）** — 以合成 `ProbeOutcome` 鎖定 §9.3 每一分：functional group all-or-nothing；robustness case 級 `15 × passed/total`；compat 全綠 10 / 任一紅 0；maintainability 三小項（`L=100, hi=80 → diff 分 = 4×(1−20/80)=3.0`、SCOPE(hard) 殘留扣 2、`S_scope>0` 扣 1、lint 新增 diagnostics 扣 3）；perf `runtime ≤ 3.0 × reference` 判定；hard gates 三個 cap（§9.2）與 `Clear=0` 連動；runtime_efficiency 分數以**量測當下 outcome 封存值**輸出（供 L1 重算引用，§12.2）。
  Run: `python3 -m pytest -q tests/evaluator/`；Expected: FAIL。
- [x] **Step 2:** 實作 `power.py`（純函數，輸入 outcomes/stats/card）與 `gates.py`。
- [x] **Step 3:** Run 同上；Expected: PASS。
- [x] **Step 4:** `mini_encounter` 整合：手工正確 diff → critical 綠、Power ≥ 60；手工破壞 API diff → Power ≤ 50。Run: `python3 -m pytest -q tests/evaluator/ tests/deck/`；Expected: PASS。
- [x] **Step 5:** Commit: `feat(evaluator): hidden evaluation, hard gates, fully pinned power rubric`。

---

### Task 6: Run store、event log、封存

**Files:**
- Create: `patchmud/store/run_store.py`、`patchmud/store/schemas.py`
- Test: `tests/store/test_run_store.py`

**Interfaces:**
- Produces: `RunStore.create(run_config) -> RunStore`（建 `runs/<run_id>/`、寫 `run.yaml` 含 frozen SHA、pricing hash、harness_prompt_version、schedule ref）、`.append_event(event: dict)`（自動 `seq`、schema 驗證）、`.write_result(result: dict)`、`.archive_private(dest.tar)`、`.archive_public(dest.tar)`（hidden bytes → content hash 佔位，§12.1）、`.load_events() -> list[dict]`（schema 不符 raise `StoreError`）。

- [x] **Step 1: RED** — 鎖定：event append-only（重開 store 續寫 seq）；unknown `schema_version` 讀取 fail-closed；`archive_public` 內 grep 不到任何 hidden probe bytes、但含其 sha256；`archive_private` 含 evaluator bundle（hidden bytes、reference timings、lockfile 描述）。
  Run: `python3 -m pytest -q tests/store/`；Expected: FAIL。
- [x] **Step 2:** 實作。
- [x] **Step 3:** Run 同上；Expected: PASS。
- [x] **Step 4:** Commit: `feat(store): append-only event log and two-tier archives`。

---

### Task 7: Token ledger、pricing、成本

**Files:**
- Create: `patchmud/ledger/tokens.py`、`patchmud/ledger/pricing.py`、`patchmud/ledger/cost.py`
- Test: `tests/ledger/test_usage_mapping.py`、`tests/ledger/test_cost.py`
- Create: `pricing/example/2026-07-16.yaml`（示例快照：token 各類價 + `per_request` + 最低消費）

**Interfaces:**
- Produces: `LedgerEntry`（§10.1 全欄位，`NA` 以 `None` 表示）、`map_usage(provider: str, usage: dict) -> LedgerEntry`、`PricingSnapshot.load(path) -> PricingSnapshot`（含 `content_hash`）、`compute_run_cost(entries, snapshot) -> RunCost(c_model: Decimal, c_reviewer: Decimal)`、`aggregate_work_tokens(entries) -> int | None`（NA 傳染）。

- [x] **Step 1: RED（per-provider fixture）** — anthropic 式（`input_tokens/output_tokens/cache_read_input_tokens`）與 openai 式（`prompt_tokens/completion_tokens/completion_tokens_details.reasoning_tokens`、cached 子集）usage fixture → 期望互斥欄位＋billed totals＋`unallocated`；reasoning 缺席 → `None` 非 0；`aggregate_work_tokens` 任一 entry 含 `None` → 整體 `None`；成本測試：9 次呼叫、`per_request=0.01` → 總價差恰 `Decimal("0.09")`（F16）；同 entries 換 snapshot 日期 → 價格不同但 entries 不變。
  Run: `python3 -m pytest -q tests/ledger/`；Expected: FAIL。
- [x] **Step 2:** 實作；金額全 `Decimal`。
- [x] **Step 3:** Run 同上；Expected: PASS。
- [x] **Step 4:** Commit: `feat(ledger): exclusive token ledger, billed totals, versioned pricing`。

---

### Task 8: Milestone A 收口——離線評分 walking skeleton

**Files:**
- Create: `patchmud/cli.py` 的 `patchmud score-diff --encounter <dir> --diff <file>` 子命令
- Test: `tests/test_score_diff_e2e.py`

**Interfaces:**
- Consumes: Task 1–7 全部。
- Produces: 給定 encounter + 手工 diff → `result.yaml`（PowerReport、gates、probe outcomes、封存）；這是 spec Phase 1「離線評分核心」的驗收入口。

- [x] **Step 1: RED** — e2e：對 `mini_encounter` 跑 reference patch → `Clear` 前置條件（critical 綠）成立、result.yaml 落盤、`archive_private` 可產出；跑空 diff → critical 紅、Economy 欄位 `NA`（無 reference_cost）。
  Run: `python3 -m pytest -q tests/test_score_diff_e2e.py`；Expected: FAIL。
- [x] **Step 2:** 串線實作。
- [x] **Step 3:** Run: `python3 -m pytest -q && python3 -m policy_check --repo .`；Expected: 全綠。
- [x] **Step 4:** Commit: `feat(cli): offline scoring entrypoint (milestone A)`。

---

### Task 9: Model adapters

**Files:**
- Create: `patchmud/adapters/base.py`、`patchmud/adapters/anthropic.py`、`patchmud/adapters/openai_compat.py`、`patchmud/adapters/scripted.py`（測試/矩陣 dry-run 用腳本化 adapter）
- Test: `tests/adapters/test_adapters.py`

**Interfaces:**
- Produces: `ModelAdapter.complete(messages: list[dict]) -> AdapterResponse(text: str, usage_raw: dict, wall_ms: int)`；`ScriptedAdapter(replies: list[str])` 依序回放；HTTP adapters 以注入的 transport callable 測試（不打真網路）。

- [x] **Step 1: RED** — 鎖定：兩個 HTTP adapter 對 fake transport 的 request 組裝（model、messages、max_tokens）與 usage_raw 原樣透傳（mapping 是 ledger 的事，adapter 不拆）；`ScriptedAdapter` 耗盡 replies → raise。
  Run: `python3 -m pytest -q tests/adapters/`；Expected: FAIL。
- [x] **Step 2–3:** 實作 → PASS。
- [x] **Step 4:** Commit: `feat(adapters): anthropic/openai-compatible/scripted adapters`。

---

### Task 10: 回合協定 parser 與 renderer

**Files:**
- Create: `patchmud/engine/protocol.py`、`patchmud/engine/render.py`、`patchmud/engine/render_zh_tw.py`（zh-TW 文案表）、`patchmud/engine/prompts.py`（`HARNESS_PROMPT_VERSION` 常數與模板，含 render pack 版本）
- Test: `tests/engine/test_protocol.py`、`tests/engine/test_render.py`

**Interfaces:**
- Produces: `parse_reply(text) -> Action`（dataclass 家族：`Look/Inspect/PlayPlan/WriteTest/Patch/RunTest/SummonReviewer/Triage/Rollback/Commit`，帶 `target_issues/files/claim/payload`）或 `ParseFailure(hint)`；`render_state(run_state) -> str`（§5.3、§5.4；敘事 zh-TW、命令關鍵字英文；含 queue、資源、flood 文案門檻——文案不進分數）。

- [x] **Step 1: RED** — 鎖定：報告 §9.3 格式的合法回覆逐命令解析；缺 `ACTION:` / 未知動作 / PATCH 無 diff 區塊 → `ParseFailure`（錯誤提示為 zh-TW）；render 對固定 run_state fixture 輸出穩定 zh-TW golden 字串（版本化，含「回合」「戰場」「洪水壓力」等敘事詞）；文案全部經 `render_zh_tw.py` 查表，`render.py` 內不得出現硬編中文字串。
  Run: `python3 -m pytest -q tests/engine/test_protocol.py tests/engine/test_render.py`；Expected: FAIL。
- [x] **Step 2–3:** 實作 → PASS。
- [x] **Step 4:** Commit: `feat(engine): reply parser and versioned state renderer`。

---

### Task 11: Issue queue 引擎

**Files:**
- Create: `patchmud/engine/queue.py`
- Test: `tests/engine/test_queue.py`

**Interfaces:**
- Consumes: `ProbeResults.transitions`、`DiffStats`、`IssueCard`。
- Produces: `IssueQueue.from_card(card, baseline: ProbeResults)`、`.update(probe_results, diff_geometry, action) -> QueueDelta(resolved, spawned, closed)`、`.open_items() -> list[IssueItem]`、`.counters -> QueueCounters(reopen, regression, duplicate, churn_events, failed_claims, scope_hard_open, s_scope_loc)`、`.snapshot() -> dict`（B_t、M_t）。

- [x] **Step 1: RED（§8.1 逐條）** —
  ```python
  def test_reopen_after_resolved():
      q = queue_with_main_red()
      q.update(probes(MAIN_GREEN), no_diff, patch_action)   # resolved
      d = q.update(probes(MAIN_RED), no_diff, patch_action) # 再紅
      assert [i.type for i in d.spawned] == ["REOPENED"]

  def test_failed_claim_counts_without_reopen():
      q = queue_with_main_red()
      q.update(probes(MAIN_RED), no_diff, patch_claiming("MAIN-1"))
      assert q.counters.failed_claims == 1 and q.counters.reopen == 0
  ```
  另鎖定：turn-0 baseline 紅的 compat probe 第一次「更紅」不 spawn（無綠→紅轉換）而首綠後再紅 spawn REGRESSION（F11）；SCOPE(hard) 聚合單 item、撤回 resolve；`expected_paths` 外 LOC 進 `s_scope_loc` 不生 item；CHURN 事件性關閉；DUPLICATE 引用已 resolved item。
  Run: `python3 -m pytest -q tests/engine/test_queue.py`；Expected: FAIL。
- [x] **Step 2–3:** 實作 → PASS。
- [x] **Step 4:** Commit: `feat(engine): deterministic issue queue with reopen/regression/scope/churn rules`。

---

### Task 12: Strategy enforcer

**Files:**
- Create: `patchmud/engine/strategy.py`、`patchmud/engine/plan_schema.py`
- Test: `tests/engine/test_strategy.py`、`tests/engine/test_plan_schema.py`

**Interfaces:**
- Consumes: `Action` 家族、`ProbeResults`、workspace 檔案 hash。
- Produces: `StrategyEnforcer(loadout: Loadout)`、`.check(action, state) -> Verdict(legal: bool, reason)`、`.on_probe_results(results)`（追蹤 valid red）、`.tdd_state -> TddState(red_nodeids, red_file_hashes, compliant)`、`.reviewer_gate_satisfied -> bool`、`.observed_tdd_workflow -> bool`；`validate_plan(yaml_text, card) -> PlanArtifact | PlanError`（§6.3 逐條）。

- [x] **Step 1: RED（gaming 向量全覆蓋）** —
  ```python
  def test_assert_false_never_compliant():
      e = enforcer(T=1)
      e.record_write_test({"tests/agent/test_x.py": HASH_A}, nodeids=["test_red"])
      e.on_probe_results(agent_test(FAILED, "test_red"))       # valid red
      assert e.check(patch_action, state).legal
      # 改測試檔讓它變綠 → red evidence 重置
      e.record_write_test({"tests/agent/test_x.py": HASH_B}, nodeids=["test_red"])
      e.on_final(agent_test(PASSED, "test_red"), file_hashes={"tests/agent/test_x.py": HASH_B})
      assert e.tdd_state.compliant is False
  ```
  另鎖定：P1 先 PATCH → illegal、plan 空列表/漏 MAIN id → `PlanError`（F7）；T1 下 `error` 態不算 red（import error 案例）；R1 空 diff review 不滿足 gate、合格 review（非空 diff + schema-valid + 已 render）後 COMMIT 合法（F8）；T0 自發 red-first → `observed_tdd_workflow=True` 且一切合法（F5）；P0/T0/R0 對應 PLAY/SUMMON illegal。
  Run: `python3 -m pytest -q tests/engine/test_strategy.py tests/engine/test_plan_schema.py`；Expected: FAIL。
- [x] **Step 2–3:** 實作 → PASS。
- [x] **Step 4:** Commit: `feat(engine): loadout enforcement with hardened tdd/plan/review gates`。

---

### Task 13: Turn loop 與 reviewer subcall（Milestone B 收口）

**Files:**
- Create: `patchmud/engine/loop.py`、`patchmud/engine/reviewer.py`
- Modify: `patchmud/cli.py`（`patchmud run --encounter --model --loadout`）
- Test: `tests/engine/test_loop.py`、`tests/engine/test_reviewer.py`、`tests/test_run_e2e.py`

**Interfaces:**
- Consumes: Task 1–12 全部。
- Produces: `run_encounter(card, adapter, loadout, config, store) -> RunResult`；每 author turn：render → adapter.complete → parse → enforcer.check → 執行 → probe 排程（§5.2）→ queue update → checkpoint → event；終局四觸發 → 全套 public + hidden evaluator → `Clear` 唯一公式 → result.yaml。`reviewer.py`：組 reviewer 輸入（§6.2 白名單）、驗 findings schema、findings 只落盤與 render。

- [x] **Step 1: RED（loop 語意）** — 以 `ScriptedAdapter` 鎖定：連續 3 次亂文 → `failed:protocol` 且 evaluator 仍執行、Clear=0；`max_turns=2` 用盡 → 強制終局；agent 從不 RUN_TEST 直接 COMMIT → 引擎自動全套判定 Clear（F3）；turn 7 SUMMON → reviewer subcall 不耗 turn、turn 8 仍屬作者（F9）；reviewer 輸入 render 字串不含作者 transcript 與 hidden 路徑（F－隔離）；每 turn 恰一 checkpoint 與一筆 turn event。
  Run: `python3 -m pytest -q tests/engine/test_loop.py tests/engine/test_reviewer.py`；Expected: FAIL。
- [x] **Step 2–3:** 實作 → PASS。
- [x] **Step 4: e2e** — `mini_encounter` + scripted「兩回合修好」劇本：Clear=1、ledger 有 entries、result.yaml 完整。Run: `python3 -m pytest -q && python3 -m policy_check --repo .`；Expected: 全綠。
- [x] **Step 5:** Commit: `feat(engine): full turn loop with reviewer subcall (milestone B)`。

---

### Task 14: Flood 計量

**Files:**
- Create: `patchmud/metrics/flood.py`
- Test: `tests/metrics/test_flood.py`

**Interfaces:**
- Consumes: store 的 events（queue snapshots：B_t、M_t、per-turn ΔT）。
- Produces: `flood_metrics(events, card, coeffs) -> FloodMetrics(area_total, area_excess, flood_index, control, ftr, flood_create_tokens, flood_repair_tokens)`；係數檔 `patchmud/metrics/flood_coeffs.yaml`（版本化，review-debt 權重 0）。

- [x] **Step 1: RED（F13/F14 情境直接入測）** —
  ```python
  def test_perfect_tdd_run_has_zero_excess_area():
      ev = events(B=[1,1,1,0], M=[1,1,1,0])   # WRITE_TEST→red→PATCH→綠
      m = flood_metrics(ev, card, COEFFS)
      assert m.area_excess == 0 and m.area_total == 3

  def test_ftr_uses_start_of_turn_backlog():
      ev = events(B0=1, turns=[(1000, 2), (9000, 1)])  # (ΔT, B_t)
      m = flood_metrics(ev, card, COEFFS)
      assert m.ftr == pytest.approx(0.9)
      assert m.flood_create_tokens == 1000 and m.flood_repair_tokens == 9000
  ```
  另鎖定：Control = `100·exp(−F/τ)`；τ 未校準時使用 1.0 並標記 `uncalibrated`。
  Run: `python3 -m pytest -q tests/metrics/test_flood.py`；Expected: FAIL。
- [x] **Step 2–3:** 實作 → PASS。
- [x] **Step 4:** Commit: `feat(metrics): dual flood area, start-of-turn FTR, control score`。

---

### Task 15: 效率與經濟指標

**Files:**
- Create: `patchmud/metrics/economy.py`、`patchmud/metrics/efficiency.py`、`patchmud/metrics/bootstrap.py`
- Test: `tests/metrics/test_economy.py`、`tests/metrics/test_efficiency.py`

**Interfaces:**
- Consumes: 多 run 的 result.yaml + ledger 聚合。
- Produces: `cost_per_clear(runs) -> Decimal | inf`、`economy_score(run, card) -> int | None`（reference_cost null → None）、`tokens_per_clear / qaty / eutb(runs, registered_budget) / mty(checkpoint_scores)`；`eutb` 在 registered 檔缺失時 raise `NotRegisteredError`（fail-closed）；`bootstrap_ci(values, b=10000, seed)`；所有效率排名輸出帶 `disclosure_cohort` 欄位，跨 cohort 排名請求 → raise（F17）。

- [x] **Step 1: RED** — 鎖定：零 clear → `inf` 且不剔除；NA 傳染到 TokensPerClear（含 None entry 的 run 集 → observable 雙欄）；EuTB 對手算小例（2 run、B=1000）值正確且無 registered 檔即拒絕；QATY 手算例；跨 cohort 排名拒絕；bootstrap 固定 seed 重現。
  Run: `python3 -m pytest -q tests/metrics/`；Expected: FAIL。
- [x] **Step 2–3:** 實作 → PASS。
- [x] **Step 4:** Commit: `feat(metrics): economy and token-efficiency suite with fail-closed registration`。

---

### Task 16: 兩級 replay

**Files:**
- Create: `patchmud/store/replay.py`
- Modify: `patchmud/cli.py`（`patchmud replay <run_dir> [--l2]`）
- Test: `tests/store/test_replay.py`

**Interfaces:**
- Consumes: run 目錄（events、checkpoints、封存 probe outcomes、result.yaml）。
- Produces: `replay_l1(run_dir) -> ReplayReport(identical: bool, diffs)`（重算 queue/flood/power/metrics，**不執行 probe**，runtime_efficiency 引用封存 outcome，§12.2）；`replay_l2(run_dir, runner)`（pinned 環境重執行 probes；functional/compat/robustness 必須相等，perf 容忍帶）。

- [ ] **Step 1: RED** — e2e run 一場（scripted）→ `replay_l1` identical=True；手動竄改 result.yaml 一個分數 → identical=False、exit non-zero；L2 對 perf 差異不 fail、對 functional 差異 fail。
  Run: `python3 -m pytest -q tests/store/test_replay.py`；Expected: FAIL。
- [ ] **Step 2–3:** 實作 → PASS。
- [ ] **Step 4:** Commit: `feat(store): bit-exact L1 replay and pinned L2 re-execution`。

---

### Task 17: Milestone C 收口——metrics report

**Files:**
- Modify: `patchmud/cli.py`（`patchmud report --runs <glob>`：輸出 §11.2 對應的多榜 YAML/CSV）
- Test: `tests/test_report_e2e.py`

- [ ] **Step 1: RED** — 兩場 scripted run（1 clear、1 fail）→ report 含 clear rate、cost/tokens per clear（inf 案例）、Power、Control、FTR、（EuTB 缺 registered → 標記 skipped 而非假值）。
- [ ] **Step 2–3:** 實作 → PASS。Run: `python3 -m pytest -q && python3 -m policy_check --repo .`。
- [ ] **Step 4:** Commit: `feat(cli): multi-leaderboard research report (milestone C)`。

---

### Task 18: Pilot runner：排程、registry、fail-closed gates

**Files:**
- Create: `patchmud/engine/pilot.py`、`patchmud/engine/schedule.py`
- Modify: `patchmud/cli.py`（`patchmud pilot --deck --models --seed`）
- Test: `tests/engine/test_pilot.py`

**Interfaces:**
- Produces: `build_schedule(matrix, seed) -> Schedule`（全域單一隨機排列，序列化含 hash，F21）；`PilotRunner.run(schedule, ...)`：逐項執行、run registry JSONL 冪等續跑、`--force` 語意；啟動前 gates：`estimators.yaml` 已 commit（F4）、全部模型 sandbox capabilities 齊備（§7）、地端模型有 `cost_scenarios`（F18）——任一不滿足即拒絕啟動。

- [ ] **Step 1: RED** — 同 seed 兩次 build → 相同 schedule hash；不同 seed → 不同排列且三軸皆被打散（統計性檢查：任一 loadout 的 runs 不連續成塊）；schedule 檔不存在或 hash 不符 → runner 拒跑；中斷後重啟跳過已完成 run；三個 fail-closed gate 各一測試。
  Run: `python3 -m pytest -q tests/engine/test_pilot.py`；Expected: FAIL。
- [ ] **Step 2–3:** 實作 → PASS。
- [ ] **Step 4:** Commit: `feat(pilot): sealed global schedule, idempotent registry, preflight gates`。

---

### Task 19: 校準 estimators

**Files:**
- Create: `patchmud/metrics/calibration.py`、`analysis/registered/estimators.yaml`（§10.4 表格逐字落檔）
- Modify: `patchmud/cli.py`（`patchmud calibrate --runs <glob> --out <dir>`）
- Test: `tests/metrics/test_calibration.py`

**Interfaces:**
- Produces: `calibrate(runs) -> CalibrationResult(reference_costs, difficulty_scales, tau, eutb_budget, grid)`；輸出寫入 deck 與 `analysis/registered/`（含 hash），已存在即拒絕覆寫（凍結語意）。

- [ ] **Step 1: RED** — 合成 pilot runs 鎖定：C_ref = 成功 run 成本中位數、成功數 <3 → 該 encounter 標記不產 Economy；D 公式與 clamp；τ = F>0 中位數、無樣本 → 1.0；EuTB B = P95 向上取整 10k、網格 256 點；重複 calibrate → 拒絕覆寫。
  Run: `python3 -m pytest -q tests/metrics/test_calibration.py`；Expected: FAIL。
- [ ] **Step 2–3:** 實作 → PASS。
- [ ] **Step 4:** Commit: `feat(metrics): pre-registered calibration estimators with freeze semantics`。

---

### Task 20: Pilot deck 內容（4 母題 × 2 變體）

**Files:**
- Create: `decks/pilot-v1/<8 個 encounter>/`（§4.3：輸入驗證、parser edge、狀態機 recovery、legacy regression 各一母題，各兩個語意變體）
- Test: deck CI（`patchmud validate-deck decks/pilot-v1`，含 reference patch 全綠與 reference timings 量測）

> 內容創作任務：每個 encounter 是獨立小 Python 專案。建議一個 encounter 一個 commit，先做母題再做變體（變體改邊界規則/錯誤契約/常數，§4.3）。`provenance.yaml` 記錄來源與封存時間。此 task 可與 Task 9–19 並行。

- [ ] **Step 1:** 母題 1（輸入驗證）+ 變體，`validate-deck` 綠 → commit。
- [ ] **Step 2:** 母題 2（parser edge）+ 變體 → commit。
- [ ] **Step 3:** 母題 3（狀態機 recovery；可改編自報告 phantom-removal 範例）+ 變體 → commit。
- [ ] **Step 4:** 母題 4（legacy regression）+ 變體 → commit。
- [ ] **Step 5:** Run: `patchmud validate-deck decks/pilot-v1`；Expected: 8/8 PASS。Commit: `feat(deck): pilot-v1 deck (4 archetypes x 2 variants)`。

---

### Task 21: Milestone D 收口——矩陣 dry-run 驗收

**Files:**
- Test: `tests/test_pilot_dryrun_e2e.py`

- [ ] **Step 1: RED** — `pilot-v1`（8 encounters）× 8 loadouts × 2 個 `ScriptedAdapter` 假模型（一個「會修」、一個「會 flood」）跑完 128 runs：schedule 先存在且 hash 符；全部 run 有 result.yaml；report 產出；抽 3 個 run `replay_l1` identical；中斷（kill 中途）重啟後總 run 數不變。
  Run: `python3 -m pytest -q tests/test_pilot_dryrun_e2e.py`；Expected: FAIL → 修串線問題 → PASS。
- [ ] **Step 2:** Run: `python3 -m pytest -q && python3 -m policy_check --repo .`；Expected: 全綠。
- [ ] **Step 3:** Commit: `test(pilot): full-matrix dry-run acceptance (milestone D)`。
- [ ] **Step 4:** 之後的真模型 pilot（校準 → 凍結 → 正式）是**營運動作**，不在本 plan：依 spec §11 執行並以 Task 19 calibrate 凍結參數。

---

### Task 22: `patchmud play`——人類親自對局（HumanAdapter）

**Files:**
- Create: `patchmud/adapters/human.py`
- Modify: `patchmud/cli.py`（`patchmud play --encounter <dir> --loadout P0T0R0`）
- Test: `tests/adapters/test_human.py`、`tests/test_play_e2e.py`

**Interfaces:**
- Consumes: `ModelAdapter` 介面（Task 9）、`run_encounter`（Task 13）。
- Produces: `HumanAdapter(input_fn=input, output_fn=print)`——`complete(messages)` 先 `output_fn` 最新狀態 render，再讀 `input_fn()` 為回覆；`usage_raw = {}`（ledger 全欄位 `NA`）；`run_encounter` 收到 `human=True` 時在 run.yaml 標記 `human: true`。

- [ ] **Step 1: RED** — 以 scripted `input_fn` 餵完整命令序列打完 `mini_encounter`：run 完成且 result.yaml 有 `human: true`；ledger 全 token 欄位 `NA`、成本 `NA`；`metrics` 聚合函數（Task 15）對含 human run 的集合 raise `HumanRunExcluded`；互動順序正確（先看到 render 再要求輸入）。
  Run: `python3 -m pytest -q tests/adapters/test_human.py tests/test_play_e2e.py`；Expected: FAIL。
- [ ] **Step 2:** 實作 `HumanAdapter` 與 `play` 子命令（含 Ctrl-D → 視同 `COMMIT` 的收尾語意）。
- [ ] **Step 3:** Run 同上；Expected: PASS。
- [ ] **Step 4:** 手動驗收：真人打一場 `mini_encounter`（這同時是引擎 demo）。
- [ ] **Step 5:** Commit: `feat(play): human adapter and interactive zh-TW encounter`。

---

### Task 23: `patchmud watch`——逐回合中文戰報 viewer

**Files:**
- Create: `patchmud/store/watch.py`
- Modify: `patchmud/cli.py`（`patchmud watch <run_dir> [--turn N]`）
- Test: `tests/store/test_watch.py`

**Interfaces:**
- Consumes: `RunStore.load_events()`（Task 6）、zh-TW render pack（Task 10）。
- Produces: `render_battle_report(events, result) -> str`（全場）與 `render_turn(events, n) -> str`；只讀封存資料。

- [ ] **Step 1: RED** — 對 e2e run 的封存 events 鎖定：輸出含「回合 N」、行動敘述、queue 變化（新增/解決 issue 的中文敘事）、flood 壓力、終局結算段；`--turn N` 只輸出該回合；執行期間 `IsolationRunner` 零呼叫、run 目錄零寫入（spy + mtime 驗證）；文案全部經 render pack 查表。
  Run: `python3 -m pytest -q tests/store/test_watch.py`；Expected: FAIL。
- [ ] **Step 2:** 實作。
- [ ] **Step 3:** Run 同上；Expected: PASS。
- [ ] **Step 4:** Commit: `feat(watch): turn-by-turn zh-TW battle report viewer`。

---

## Self-review 紀錄

- Spec 覆蓋：§2–§12 全部章節皆有對應 task（§4→T1/T20、§5→T10/T13、§6→T12/T13、§7→T2/T3/T4、§8→T11/T14、§9→T5、§10→T7/T15/T19、§11→T18、§12→T6/T16、§13 acceptance 分散於各 task RED 與 T21）；§14 deferred 無 task（正確）；§15/§16 為文件性質。
- 型別一致性：`ProbeOutcome/ProbeResults`（T4）被 T5/T11/T12 引用同名；`IsolationRunner`（T2）為唯一執行 seam（invariant 3）。
- 無 placeholder：每 task 有 exact files、RED 契約或測試碼、run 命令與 commit 訊息。
