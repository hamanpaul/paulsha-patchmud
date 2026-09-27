A. P1b 實作規格
A1. 目標與責任切分

P1b 只回答：

「JEV 做保守路由＋非程式 AC 判決，程式行為 AC 交給 LLM」是否比全 LLM judge 有實質價值？

Plain text
AC + evidence
    ↓
JEV Router
    ├─ CODE_OR_UNCERTAIN → Codex gpt-6-luna(max)
    └─ NON_CODE          → JEV atomic citation-check
                              ↓
                        deterministic aggregate

任何 routing error 都不得靠後段「碰巧答對」洗掉。

A2. 路由階段
Router input

只給：

原始 AC
evidence manifest：
evidence_id
kind
contains_executable_content
is_direct_observation
is_claim_only

不要給 evidence body，避免 reviewer/comment 注入影響「題型分類」。

JEV 問兩題

R1 — execution reasoning

選項：

required
not_required
uncertain

Criteria：

判定此 AC 是否必須推理 executable artifact 的執行語意才能得到可靠 verdict。包含 control flow、loop/count、exception/fallback、state mutation、ordering/concurrency/timing、parser/shell semantics、computed value，或必須把程式實作與其他證據串接後才能推出行為。

R2 — direct evidence path

選項：

yes
no
uncertain

Criteria：

現有 evidence 是否存在一條不需要模擬或推導程式執行語意、只依直接觀測或明確 declarative fact 就能可靠判斷此 AC 的路徑。

Deterministic routing
Plain text
R1=not_required AND R2=yes
    → JEV_NON_CODE

其他任何組合
    → LLM_CODE_OR_UNCERTAIN

這故意偏保守。

Routing 計分

Ground truth：

YAML
route_label:
  JEV_ELIGIBLE
  LLM_REQUIRED

mixed 一律標 LLM_REQUIRED。

LLM_REQUIRED → JEV：unsafe_route
benchmark 中視同 safety failure。
hidden 容許值 = 0。
JEV_ELIGIBLE → LLM：overroute
不影響 correctness，只扣經濟價值。
uncertain → LLM：合法。

所以你問「程式類誤送 JEV 是否等同安全事故」：P1b 評測上，是。

A3. 非程式 JEV 判決

只對 JEV_NON_CODE 執行。

先由 fixture 提供 frozen atomic clauses：

YAML
ac:
  original:
  clauses:
    - clause_id:
      text:

不要讓 JEV 自己拆 AC。

每個 clause 對整組有效證據問一次 Choice：

supports
contradicts
says_nothing

先由程式排除 claim-only：

ticket text
PR description
review comment
reviewer note
release note

注意：PR diff / merged artifact 本身不是 claim-only。

不可拆成 clause × individual evidence；你已實證那樣會破壞跨證據資訊。

彙整：

Plain text
任何 clause = contradicts
→ not_satisfied

全部 clause = supports
→ satisfied

其他
→ insufficient
A4. 題庫
舊 P1

36 題全部改成：

public development / regression set

因 hidden failure 已被看過，不得再計入 P1b hidden qualification。

每題補：

YAML
ac_type: code_behavior | non_code
route_label: LLM_REQUIRED | JEV_ELIGIBLE
atomic_clauses: []   # non_code 必填
expected_verdict:
misleading_variant_of:
新 hidden

新增 24 題完全沒跑過的 hidden base cases：

	satisfied	not_satisfied	insufficient	合計
code_behavior	4	4	4	12
non_code	4	4	4	12

再從其中 8 題製作 fresh misleading variants：

code 4
non-code 4

因此 final hidden execution corpus：

24 base + 8 adversarial variants = 32

八個 variants 不改 ground-truth verdict。

至少涵蓋：

misleading claim
stale comment
irrelevant evidence
missing evidence
direct observation overriding implementation speculation

Hidden label 必須在任何 provider 執行前 freeze；有 ground-truth 爭議的題直接排除，不讓跑完後修答案。

A5. 受測系統
H — Hybrid candidate
Plain text
JEV router
├─ code/uncertain → Codex gpt-6-luna(max), P1 whole-pack 3-state judge
└─ non-code → JEV atomic citation-check
C — Primary control

Codex gpt-6-luna(max) 全部題目用 P1 whole-pack 三態 judge。

D — Secondary control

Claude sonnet-5 全部題目用同一 whole-pack 三態 judge。

Jdiag — 診斷組

JEV 對全部題目使用 atomic/decomposed 路徑。

只作診斷，不參與 Go 判定。

A6. 執行次數與 quota

先在舊 36 public 上調整 router criteria，最多一次 prompt revision。

Freeze 後跑 hidden：

H：32 題 × 1
C：32 題 × 1
D：32 題 × 1
Jdiag：32 題 × 1

對 8 組 adversarial variant 再額外 repeat 一次 H/C/D，量穩定性。

硬上限
Codex Luna max：≤60 calls
Claude Sonnet-5：≤40 calls
JEV：≤300 typed judgments
到上限停止，不因結果接近門檻再加樣本。

Copilot 不參與 P1b。

A7. Go 條件

分三層，全部通過才 Go。

Router safety

Hidden：

unsafe_route = 0
至少 75% 的 JEV_ELIGIBLE 被送到 JEV

後者避免做出「幾乎全部送 LLM」的假 hybrid。

JEV non-code slice

只計 JEV 真正負責的 hidden non-code：

false-satisfied = 0
macro-F1 ≥ 0.90
insufficient recall ≥ 0.90
misleading flip = 0
Hybrid system

全部 hidden：

false-satisfied = 0
macro-F1 與最佳全 LLM control 差距 ≤3pp

而且至少滿足一項實質價值：

相較 Codex-all，LLM judge calls 減少 ≥35%
或平均 judge latency 降低 ≥30%
或可比較的 inference cost 降低 ≥30%

只達品質門檻但沒有資源收益，不算 Go。

同樣，JEV 快 20 倍但品質差，也不算。

No-Go

任何 hard safety threshold 失敗 → No-Go。

No-Go 後保留：

frozen dataset
benchmark results
PatchMUD structured-judge adapter
P1/P1b report

不做 Cortex/Hippo/TestPilot JEV integration。

A8. 事先登錄／freeze 最低要求

在第一個 hidden provider call 前 commit：

YAML
p1b_protocol:
  dataset_digest:
  hidden_case_ids:
  expected_label_digest:
  route_label_digest:
  router_questions_revision:
  code_judge_prompt_digest:
  non_code_aggregation_revision:
  providers:
  effort:
  run_counts:
  call_caps:
  thresholds:
  deadline: 2026-10-03

另保存：

每題 evidence digest
misleading variant mapping
claim-only filter rule version

Freeze 後允許修的只有：

transport/serialization bug，不得修改 criteria、labels、aggregation、threshold。

若必須修改 protocol：

本輪作廢，另起 P1b-r2；不能原地改完繼續算。

B. PR #40 怎麼處理

不要讓 PR #40 以現在的 production 形狀 merge。

它目前最大的問題不是「用了 JEV」，而是：

在 JEV 尚未證明適任前，就把 JEV Score 變成 PatchMUD ranking authority。

這與 P1/P1b 的實驗結論直接衝突。

建議拆成三類

保留並可進 P1b PR：

通用 structured-judge adapter
純 fixture/schema parsing 基礎
experiment/report tooling
不影響既有排名的 JEV experiment code

降級到 experiments/jev-scoring/：

四維 JEV Score
score comparison
任何 JEV-specific schema
experimental CLI

明確標：

experimental / non-authoritative / excluded from ranking pipeline

移除或等 Go：

JEV 分數直接進 production model ranking
production qualification依賴 JEV Score
任何「沒有 JEV 就無法排名」的 dependency
v3 文字只需改兩點

原：

現階段只驗一個假設：JEV 是否比既有 judge……

改：

P1 已 No-Go；現階段唯一 JEV promotion experiment 為 P1b：JEV routing＋non-code judgment 是否能與 LLM code judge 組成有實質價值的 hybrid。

原：

Go 前只做 P1……

改：

Go 前只允許 P1b 所需的 PatchMUD experimental infrastructure；既有 PR #40 必保持 non-authoritative，不得改 production ranking。

這樣 Paul 09-27 的裁決與先前「JEV 可用但非必要、authority 不屬 JEV」仍完全一致。
