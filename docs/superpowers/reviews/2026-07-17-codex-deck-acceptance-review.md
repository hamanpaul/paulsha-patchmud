# codex (gpt-5.6-sol) 對抗審查：Task 20 pilot deck + Task 21 矩陣驗收（2026-07-17）

審查者：codex CLI `gpt-5.6-sol`（xhigh），read-only。範圍：`decks/pilot-v1/**`（8 encounter）與 `tests/test_pilot_dryrun_e2e.py`，並牽連 evaluator / replay / deck 契約。

codex 先自證排除的假問題（有價值的負面結論）：
- 4 組 v1/v2 都真的改了邊界規則（input 長度→尾碼數字、parser 只切第一個 `=`→忽略空 token、state `scan_errors`→`UNREACHABLE` sentinel、legacy 版本排序→`None` 刪除契約），**非只改名**。
- 8 張卡 `expected_paths ⊆ allowed_paths`、critical↔functional group 一致、rubric 檔案皆存在；reference patch LOC 2/2/2/2/3/4/4/5 全落在各卡 range，且都通過 `git apply --check`。
- Task 21 確實展開 128 run、檢查 schedule hash、128 個 result、report、resume 集合與 3 個 replay sample，無 `assert True` / `count>=0` 假斷言。

## Findings 與處置

| # | Sev | 摘要 | 主迴圈驗證 | 處置 |
| --- | --- | --- | --- | --- |
| F1 | blocker | evaluator 把 hidden probe 複製進 candidate 可讀 rw checkout；`hidden_access_detected` 恆 False 未接 gate → candidate 可讀題偽裝答案 | **確認**：`evaluate.py:134 _overlay_hidden` 複製進 checkout；`hidden_access_detected` 預設 False 從未計算 | 修（gate 接線＋hidden 移出 candidate 可讀樹＋殘留限制文件化） |
| F2 | blocker | `replay_l1` 只驗 snapshot 自洽（`b_t==len(open_items)`），未依規則重建 queue，未比對 Flood/Control/strategy → 「位元一致」不完整 | **確認**：`_queue_trajectory_diffs` 僅自洽檢查 | 修（重建 queue＋比對 flood/control/strategy） |
| F3 | major | `materialize_repo` 用預設 `shutil.copytree`（解 symlink）→ `repo/x -> ../hidden/ref.patch` 會把 hidden bytes 複製進 worktree | 待驗 | 修（拒 symlink/特殊檔＋materialize 掃描） |
| F4 | major | public/hidden 隔離只用 `startswith("hidden/")` 未正規化 `..` → `hidden/../repo/tests/public/x.py` 可偽裝 critical hidden probe | 待驗 | 修（deck path 正規化＋root containment） |
| F5 | major | 缺 frozen manifest 綁 hash → 改 `repo/src`/hidden/card 後不被判 drift | **確認** | **已修**：provenance pin `content_sha256`（card+repo+hidden，排除 reference_timings），validate-deck 重算比對，漂移 fail（`19d0858` 後續 commit） |
| F6 | major | Task 21 T1 fixer 用永久 `assert False,"red"` 假 red，32 個 T1 fixer run 必 `strategy_violation`，測試只斷言 `clear==1` 未讀 strategy | **確認** | **已修**（`d37ea63`）：fixer WRITE_TEST 鏡射 public MAIN 測試（真 red→green），斷言全 T1 fixer `tdd_compliant=true`/`strategy_violation=false`；128-run e2e 通過 |
| F7 | major | namespace 不足時整份 128-run `pytest.skip` 仍綠；CI 未裝 bwrap | **確認**（本機有 bwrap 實跑 379s；codex 沙箱無 → skip） | **已修**（`d37ea63`）：acceptance caps 不足 `pytest.fail`（可 env 降級）；CI tests.yml 安裝並驗證 bubblewrap |
| F8 | major | 8 卡 `runtime_efficiency.probes` 重用 functional critical test，無 workload → 量到 pytest 啟動雜訊且與 functional 重複計分 | **確認** | **scoped 延後**：5 分 runtime 子分數的量測品質問題，不讓 agent 假造 Clear/Power；正式修法需為 8 卡各撰寫獨立 perf workload（含 pinned reference timing），列為 deck 內容硬化 workstream（spec §14） |
| F9 | minor | Task 21「kill 中途」在真 wiring 前拋例外，未驗 mid-run partial（unit test 有覆蓋） | **確認**（codex 自述 `tests/engine/test_pilot.py` 已有 partial-dir unit test） | **scoped 延後**：真實 mid-run partial 已由 unit test 覆蓋；e2e 只驗 inter-run 續跑冪等。強化 e2e 為 follow-up（spec §14） |
| F10 | minor | `state-recovery-v1` 需求文字英中混排，其他 7 卡純 zh-TW，每回合顯示給模型 | **確認** | **已修**（`c1e800f`） |

## 最終處置摘要

- **10 條全數確認成立**（codex 自證排除了「只改名」「reference patch 過不了」等假問題）。
- **已修 8 條**：F1、F2（兩個 blocker）、F3、F4、F5、F6、F7、F10——涵蓋 codex verdict 點名的**全部三個「最危險」項**（F1 隔離、F2 replay、F6/F7 假綠）。
- **scoped 延後 2 條**：F8（perf probe 量測品質，需 8 卡 workload 撰寫）、F9（e2e mid-run 保真度，unit test 已覆蓋真實情境）——皆非可被 agent 利用取得不當 Clear/Power 的洞，列入 spec §14 deferred workstream。

## Verdict（codex）

最危險三項：
1. hidden evaluator 隔離實際失效（F1）——namespace 擋 host deck，卻主動把 hidden 放回 candidate 可讀 checkout，摧毀 Power/Clear 可信度。
2. L1 replay「位元一致」不完整（F2）——未重建 deterministic queue、未對回 Flood/Control/全 metrics。
3. Task 21 兩條假綠路徑（F7 無 namespace 靜默 skip；F6 32 個 T1 fixer 以假 red 通關未驗 strategy）。
