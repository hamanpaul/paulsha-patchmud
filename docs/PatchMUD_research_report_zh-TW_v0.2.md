---
title: "PatchMUD：成本優先的純文字回合制程式代理評測框架"
subtitle: "以解題品質、總成本與 Issue Flooding 收斂性評估 Coding Agents"
author: "研究設計草案"
date: "2026-07-16"
version: "0.2"
status: "Conceptual Research Design"
lang: "zh-TW"
---

# PatchMUD：成本優先的純文字回合制程式代理評測框架

## 摘要

本報告提出 **PatchMUD**：一套以軟體 issue 為關卡、以程式代理模型為玩家、以純文字命令驅動的回合制評測框架。其目的不是只測量模型能否在單次輸出中產生正確程式，而是研究模型在有限時間與有限成本下，如何選擇工作策略、修正錯誤、避免回歸，並將 issue backlog 穩定收斂至零。

PatchMUD 將每個模型表示為三維角色能力：**經濟（Economy）**、**火力（Power）**與**控場（Control）**。其中經濟是最主要維度，預設權重為 55%；火力與控場分別為 25% 與 20%。經濟衡量的不是單次模型呼叫價格，而是完成一張 issue 所需的總有效成本；火力衡量最終 patch 的正確性與工程品質；控場衡量解題過程是否穩定收斂，抑或反覆重開 issue、產生 regression、擴大修改範圍，形成 **Issue Flooding**。

為避免把「便宜」等同於「有效率」，框架另設一組不重複計入三維總分的 **token-efficiency 稽核指標**：Tokens per Successful Clear、Quality-adjusted Token Yield、Effectiveness under Token Budget（EuTB）、Marginal Token Yield 與 Flood Token Ratio。這些指標用來區分一次解完、低 token 的強模型，與單次便宜但反覆修補、產生 token snowball 的弱模型。

框架包含四種核心策略：**空手自幹（SOLO）**、**PLAN**、**TDD** 與 **REVIEWER**。空手是明確的零流程開銷對照組，而不是「沒有做選擇」。在因果實驗模式中，PLAN、TDD、REVIEWER 構成 `2 × 2 × 2` 的完整因子設計，SOLO 即為 `P0T0R0`；在自主競技模式中，模型可依 issue 自行選擇或切換策略，並以選牌後悔值衡量其 orchestration 能力。

PatchMUD 的關鍵機制是動態 issue queue。每次 patch、測試或 review 後，引擎會依公開診斷、既有測試、版本差異與 reviewer finding 更新 queue。弱模型若反覆採用局部修補，可能使原始 issue 重開、生成 regression、產生 scope creep 或修改震盪。這些現象不只是最終扣分，而會增加後續 context、推理成本與修復回合，形成可觀察且可量化的 flooding 狀態。

本報告定義遊戲協定、成本模型、三維能力估計、Issue Flooding 指標、實驗設計、統計分析與最小可行實作，作為評估強模型、弱模型與地端模型在不同工作流程下之品質與成本效益的研究基礎。

**關鍵詞：** coding agent、software engineering benchmark、cost per successful issue、token efficiency、effectiveness under token budget、TDD、code review、planning、issue flooding、multi-agent、turn-based evaluation

---

## 1. 研究背景

現有程式模型評測常將問題簡化為：給定規格後，模型是否產生通過測試的最終答案。這種方法能測量結果正確性，卻較難反映真實 agent engineering 中的三個重要問題：

1. **模型的單次價格與整體解題成本不同。** 便宜模型可能因反覆修正、重跑測試與召喚 reviewer，最後比昂貴但一次完成的模型更貴。
2. **最終品質無法描述解題路徑。** 兩個模型都可能得到相同終局分數，但其中一個穩定收斂，另一個曾製造多個回歸、反覆撤銷修改，工程風險不同。
3. **流程策略具有條件性。** PLAN、TDD 與 REVIEWER 不一定總是有利。簡單 issue 可能最適合空手完成；高回歸風險 issue 則可能因 TDD 或 review 顯著降低總成本。

因此，PatchMUD 將評測單位由「單次答案」改為「一場 issue encounter」，記錄從讀題、選擇策略、修改、測試、review 到提交的完整事件軌跡。

---

## 2. 研究目標與問題

### 2.1 主要目標

建立一個可重現、可自動評分、具遊戲化介面但仍保有研究效度的評測環境，用來比較：

- 強模型、弱模型與地端模型的 **成功解題總成本**。
- PLAN、TDD、REVIEWER 與空手策略對品質、成本與收斂性的影響。
- 弱模型是否因反覆修 issue 而進入 Issue Flooding。
- 模型是否能依 issue 特徵，自主選擇成本效益較佳的策略。
- 作者模型與 reviewer 模型的配對是否存在互補性。

### 2.2 研究問題

**RQ1：** 單次推理價格較低的模型，是否也具有較低的成功解題成本？

**RQ2：** PLAN、TDD 與 REVIEWER 分別在何種 issue 類型下降低總成本或提高通關率？

**RQ3：** Issue Flooding 是否能解釋弱模型的成本膨脹與失敗率？

**RQ4：** 模型自行選牌時，能否接近該模型在相同 issue 上的最佳策略？

**RQ5：** 高能力 reviewer 是否能補償低能力作者；其收益是否大於新增的推理成本？

**RQ6：** 模型的經濟、火力與控場三維能力，能否比單一總分更穩定地預測其在不同 issue 上的表現？

**RQ7：** 在控制最終品質與通關率後，token-efficiency 是否仍能區分 one-shot 解題與反覆修補所造成的資源浪費？

### 2.3 可檢驗假設

- **H1：** 單次呼叫便宜不等於成功解題便宜；低控場模型的成本會因 reopen、regression 與 churn 非線性上升。
- **H2：** TDD 對 regression surface 高的 issue 有較大正向效果，但對局部、規則簡單的 issue 可能造成時間與 token 開銷。
- **H3：** PLAN 對狀態機、跨模組 invariant 與規格交互作用問題較有利，對單點驗證問題可能不具成本效益。
- **H4：** REVIEWER 的淨收益取決於 reviewer 找錯率、作者修正轉換率與剩餘時間；reviewer 並非必然提升結果。
- **H5：** SOLO 對低複雜、低回歸風險 issue 具有最高 one-shot 成本效益。
- **H6：** Control 對 Economy 具有中介效果：收斂性愈差，成功解題成本愈高。
- **H7：** Issue Flooding 會降低 Marginal Token Yield 並提高 Tokens per Successful Clear；低單次價格模型可能因此呈現較差的整體 token-efficiency。

---

## 3. 核心概念：把軟體修復轉成純文字回合制遊戲

PatchMUD 採用類似 MUD／MU 式的純文字互動，但將持續即時世界改為同步回合制。

| 軟體工程元素 | PatchMUD 表現 |
|---|---|
| Repository | 地城／世界 |
| Issue | 任務、遭遇或 Boss |
| 檔案與模組 | 房間與區域 |
| Stack trace | 足跡與線索 |
| Failing test | 陷阱或破綻 |
| Patch | 攻擊、技能或修復動作 |
| Regression | 新生成的敵人／支線 issue |
| Hidden tests | Boss 隱藏階段 |
| Reviewer | 隊友或召喚角色 |
| Issue queue | 戰場上的敵方 backlog |
| Total cost | 資源消耗 |

模型只能透過純文字觀察狀態、選擇行動並提交結構化 artifact。遊戲引擎負責在隔離 sandbox 中真正讀取檔案、套用 diff、執行測試與計算成本。

研究模式不使用隨機傷害或主觀 LLM 裁判。所有關鍵結果均由程式碼 artifact、測試、版本差異與可稽核事件日誌決定。

---

## 4. 模型三維角色能力

### 4.1 主能力定義

每個模型 snapshot 與 agent harness 組合都視為獨立角色，具有以下三維能力：

| 維度 | 定義 | 預設權重 |
|---|---|---:|
| **經濟 Economy** | 完成 issue 所需的總有效成本；包含失敗嘗試與 reviewer 等附加成本 | **55%** |
| **火力 Power** | 最終 patch 的功能正確性、穩健性、相容性與工程品質 | 25% |
| **控場 Control** | 解題過程的收斂能力；是否避免重開、回歸、scope creep 與修改震盪 | 20% |

角色卡示意：

```text
┌─ Model Snapshot / Harness ─────────────┐
│ 經濟 ECON      84 ± 4                  │
│ 火力 POWER     78 ± 3                  │
│ 控場 CONTROL   52 ± 6                  │
│                                       │
│ One-shot clear rate       41%          │
│ Cost per successful clear 18.4 credits │
│ 常見狀態                  Issue Flood  │
│ Plan affinity             +2.1         │
│ TDD affinity              +7.4         │
│ Reviewer receptivity      63%          │
└───────────────────────────────────────┘
```

### 4.2 能力值是評估結果，不是遊戲加成

在正式研究模式中，三維能力值只用於：

- 顯示模型輪廓。
- 配對難度與預測通關率。
- 分析策略與 issue 類型的交互作用。
- 建立長期排行榜。

能力值不會直接增加測試分數、降低 AP 或提供額外線索。否則會產生循環：先判定某模型較強，再因角色值使其更容易獲勝。

Insight、Build、Verify 等傳統能力仍可作為二級診斷特徵，但不作為主排行維度。主排行優先反映實際工程決策最關心的成本、品質與收斂。

---

## 5. 成本模型：以成功解題總成本為核心

### 5.1 單次價格不是經濟性

PatchMUD 不以 `cost per call` 作為主要經濟指標，而使用：

> **Cost per Successful Issue Clear**

弱模型即使每次呼叫便宜，只要反覆嘗試、製造回歸或最後失敗，這些成本仍全數計入。

### 5.2 Run 有效成本

單次 encounter 的有效成本可表示為：

\[
C_{run} = C_{model} + C_{reviewer} + C_{sandbox} + C_{infra} + \lambda_t T_{wall}
\]

作者模型的貨幣成本應由當次、當模型、當日期的計價快照計算：

\[
C_{model,r}
=
\frac{1}{10^6}
\sum_{k \in K}
p_{m,k,d}\,T_{r,k}
+ C_{request,r}
\]

其中 \(K\) 是該 provider 實際計價且互斥的 token 類別，例如 uncached input、cached input、visible output 與 hidden reasoning；\(p_{m,k,d}\) 是模型 \(m\) 在日期 \(d\) 對類別 \(k\) 的每百萬 token 價格。若 provider 將 reasoning token 包含在 output token 中，adapter 必須先拆成互斥欄位，禁止重複計價。

其中：

- \(C_{model}\)：作者模型輸入、輸出、reasoning、cache 與其他計價項目。
- \(C_{reviewer}\)：reviewer 的完整模型成本。
- \(C_{sandbox}\)：測試、編譯、容器與儲存成本。
- \(C_{infra}\)：API gateway、地端 serving、CPU、RAM、網路等分攤。
- \(T_{wall}\)：端到端 wall-clock time。
- \(\lambda_t\)：每單位時間的機會成本，可做低、中、高三種敏感度分析。

### 5.3 API 與地端模型的成本對齊

API 模型應記錄實際 token 類型、呼叫次數與當次採用的價格快照。地端模型則可採用：

\[
C_{local} = C_{energy} + C_{hardware\ amortization} + C_{serving} + C_{operations}
\]

其中硬體攤提需明確記錄：

- GPU／主機購置成本。
- 預估可用時數。
- 同時服務的 session 數。
- 功耗、利用率與電價假設。
- 量化、batching 與 serving stack。

成本報告應同時保留原始 token、秒數、kWh 與價格參數，避免單一換算假設掩蓋差異。

### 5.4 成功解題成本

跨多次 run 的主要經濟指標為：

\[
CostPerClear = \frac{\sum C_{run}}{N_{successful\ clears}}
\]

失敗 run 的成本保留在分子中，但不增加成功數。若完全沒有成功通關，CostPerClear 視為無限大或未定義，Economy 設為 0。

### 5.5 Economy 分數

原始成本應是主要報告值。若遊戲介面需要 0–100 分，可依每張 issue 的校準參考成本 \(C_{ref,i}\) 轉換：

\[
Economy_i = 100 \times \min\left(1, \sqrt{\frac{C_{ref,i}}{C_{run,i}}}\right)
\]

此公式只是建議。正式係數應在 pilot 後校準，並搭配原始成本與敏感度分析發布。

### 5.6 Token-efficiency：成本以外的資源效率稽核

貨幣成本已透過 \(C_{model}\) 進入 Economy，因此 token-efficiency **不應再作為第四個主維度，也不應在 Utility 中重複加權**。它的用途是：

- 在價格改動時提供較穩定的資源使用紀錄。
- 比較同一模型在 SOLO、PLAN、TDD、REVIEWER 下的 token 開銷。
- 辨識弱模型因反覆探索、reopen 與 regression 造成的 token snowball。
- 建立固定 token budget 的 capability-normalized track。

#### 5.6.1 互斥 token ledger

每個 provider adapter 必須將 usage metadata 映射成互斥欄位：

\[
T_r^{work}
=
T_{r,in}^{uncached}
+T_{r,in}^{cached}
+T_{r,out}^{visible}
+T_r^{reasoning}
\]

規則如下：

1. 若 `cached_tokens` 是 `input_tokens` 的子集合，先計算 uncached input，禁止兩者重複相加。
2. 若 reasoning 已包含於 output total，先扣除後再填入 visible output。
3. Provider 未揭露的 reasoning token 記為 `NA`，不得當作 0；該模型只計算 observable-token 版本。
4. 同時保留 prompt bytes、generated bytes、API calls、agent turns 與 wall-clock，供 tokenizer 差異的敏感度分析。

#### 5.6.2 Tokens per Successful Clear

跨多個 encounter 的第一個直觀指標為：

\[
TokensPerClear_m
=
\frac{\sum_r T_{m,r}^{work}}
{\sum_r Clear_{m,r}}
\]

值愈低愈好。失敗 run 的 token 留在分子，但不增加分母，因此「反覆嘗試後仍失敗」會被完整反映。完全沒有成功時，此值視為無限大。

#### 5.6.3 Quality-adjusted Token Yield

只看通關數可能鼓勵低品質、剛好過 gate 的 patch，因此增加品質調整版本：

\[
QATY_m
=
\frac{
10^6\sum_r Clear_{m,r}\left(Power_{m,r}/100\right)
}
{\sum_r T_{m,r}^{work}}
\]

`QATY` 表示每一百萬 observable work tokens 產生多少個品質調整後的成功解答，值愈高愈好。失敗解答的產出為 0，但其 token 仍計入分母。

#### 5.6.4 Effectiveness under Token Budget（EuTB）

為避免單一 ratio 被少數極端 run 支配，正式 leaderboard 應另報 token-budget 曲線與其面積。對模型 \(m\)，在 token budget \(b\) 下的通關率定義為：

\[
R_m(b)
=
\frac{1}{N}
\sum_{i=1}^{N}
\mathbf{1}
\left(Clear_{m,i}=1 \land T_{m,i}^{work}\le b\right)
\]

在預先註冊的最大預算 \(B\) 下：

\[
EuTB_m(B)
=
\frac{1}{B}
\int_0^B R_m(b)\,db
\]

`EuTB` 同時獎勵「解得多」與「用得少」。最大預算 \(B\) 必須由 pilot set 決定後凍結，不得依正式測試結果事後調整。此設計採用 SWE-Effi 所提出的 resource-effectiveness 思路；該工作同時區分 token、貨幣成本、CPU time 與 inference time 的 budget-AUC 指標。

#### 5.6.5 回合級 Marginal Token Yield

為量化「每一輪追加 token 是否真的讓解答變好」，保存每個 checkpoint 的離線 artifact score \(Q_{r,t}\)，並計算：

\[
MTY_{r,t}
=
\frac{Q_{r,t}-Q_{r,t-1}}
{\Delta T_{r,t}^{work}/1000}
\]

- 正值：每千 token 產生可量測進展。
- 接近 0：大量敘述、搜尋或重寫但幾乎沒有品質增益。
- 負值：新增 token 後反而引入 regression。

中間 checkpoint 的 hidden score 只在賽後離線重播時使用，不能回饋給同一場 agent。

#### 5.6.6 Flood Token Ratio

Issue Flooding 對 token 的直接影響可表示為：

\[
FTR_r
=
\frac{
\sum_t \Delta T_{r,t}^{work}\,
\mathbf{1}\left(B_{r,t}>B_{r,0}\right)
}
{\sum_t \Delta T_{r,t}^{work}}
\]

其中 \(B_{r,t}\) 為第 \(t\) 回合可觀測 backlog。`FTR` 是 agent 在「自己製造出的額外 backlog」存在期間所消耗的 token 比例。它不把修復 flood 的 token 武斷視為浪費，而是客觀揭露有多少資源被迫投入自生債務狀態。

#### 5.6.7 解讀原則

Token 指標提高的是**可稽核性與資源效度**，不是自動保證整體研究有效。正式報告應遵守：

- 主經濟結論仍以 Cost per Successful Clear 為主；token、時間、能耗為平行指標。
- 原始 token 最適合同模型、同 tokenizer、同 harness 的策略比較；跨模型比較必須搭配貨幣成本、bytes、時間與能耗。
- cached、reasoning 與 visible output 必須分欄發布，不以單一 total 掩蓋差異。
- `EuTB`、`TokensPerClear`、`QATY`、`MTY` 與 `FTR` 均應提供 bootstrap confidence interval。
- 不把 token-efficiency 再加進 55/25/20 Utility，避免 Economy 被重複計分。

---

## 6. 火力：最終 Artifact 品質

Power 只評估時間截止時的最終 artifact，不因模型聲稱完成、使用某張策略牌或產生漂亮解釋而加分。

建議 100 分結構：

| 項目 | 分數 |
|---|---:|
| 功能與 critical requirements | 60 |
| Robustness、property 與邊界條件 | 15 |
| Regression／API compatibility | 10 |
| 可維護性與 focused diff | 10 |
| 產出程式的時間／空間效率 | 5 |

需區分兩種成本：

- **Economy**：模型與 agent 解題所消耗的操作成本。
- **Power 內的效率**：產出程式本身的演算法與執行效率。

### 6.1 Hard gates

為避免免費但無效的模型在經濟維度獲勝，採用下列門檻：

- Critical requirements 未通過：`Clear = 0`，Economy = 0，綜合 rating 最高 49。
- 無法 import、compile 或啟動測試：Power 上限 15。
- 公開 API 被不相容地更改：Power 上限 50。
- 存取 hidden tests、reference patch 或 benchmark metadata：該 run 無效。

### 6.2 避免重複懲罰

- 最終仍存在的 regression：影響 Power。
- 中途產生但最後修復的 regression：不影響最終 Power，但影響 Control 與 Economy。
- 額外模型呼叫與測試：只依真實支出影響 Economy。

如此可分辨「終局品質」與「過程失控」，避免所有事件都被無差別重複扣分。

---

## 7. 控場：Issue Flooding 與收斂性

### 7.1 Issue Flooding 定義

Issue Flooding 指模型在修復原始 issue 的過程中，因不完整理解、shotgun patch、回歸或重複工作，使 defect backlog 擴張或長時間無法下降的現象。

弱模型的典型軌跡可能是：

```text
初始 backlog：1
第 1 次 patch：3
第 2 次 patch：4
第 3 次 patch：3
第 4 次 patch：5
時間截止：2
```

強控場模型則可能是：

```text
1 → 1 → 0
```

### 7.2 Issue queue 類型

| 類型 | 觸發條件 |
|---|---|
| `MAIN` | 原始 issue 的 acceptance requirement |
| `REOPENED` | 模型宣稱修復，但同一 requirement 仍由公開診斷證明失敗 |
| `REGRESSION` | 先前通過的公開測試、smoke test 或 compatibility check 變為失敗 |
| `SCOPE` | 修改不相關模組、API 或行為，超出 canonical spec |
| `REVIEW-DEBT` | reviewer 提出可驗證 finding，但作者未處理或錯誤處理 |
| `CHURN` | 同區域反覆修改、撤銷、重寫，未形成淨進展 |
| `DUPLICATE` | 重複提出或重複修理同一已知問題 |

### 7.3 Backlog 動態

每回合更新：

\[
B_{t+1} = B_t - Resolved_t + New_t + Reopened_t
\]

僅看最終 backlog 不足以描述失控程度，因此定義 Issue Flood Area：

\[
FloodArea = \sum_t B_t \Delta t
\]

FloodArea 表示 backlog 在整場遊戲中累積存在的面積。即使模型最後勉強修完，若前面長期維持大量缺陷，仍會反映在 Control 與 Economy 中。

### 7.4 Flood Index

建議初始公式：

\[
F = \frac{
FloodArea
+ 2.0N_{regression}
+ 1.5N_{reopen}
+ 1.0N_{review\ debt}
+ 0.5N_{duplicate}
+ 0.02LOC_{reverted}
+ S_{scope}
}{D_{issue}}
\]

其中 \(D_{issue}\) 是 issue 難度或預期修改規模的校準項，避免較大 issue 天然具有較高 flood 值。

Control 可轉換為：

\[
Control = 100 \times e^{-F/\tau}
\]

\(\tau\) 由 calibration encounters 決定。

### 7.5 Flood 狀態

對 10 分鐘 micro-issue，可先採用：

| Backlog | 狀態 | 遊戲顯示 |
|---:|---|---|
| 0–1 | Stable | 戰場可控 |
| 2–3 | Noisy | context 與診斷負擔上升 |
| 4–5 | Flooded | queue 顯著擴張，需優先 triage |
| 6+ | Meltdown | 幾乎無法在剩餘時間收斂 |

這些門檻是介面提示，不應在正式排名中任意增加隨機懲罰。正式分數仍由實際 backlog、事件與成本計算。

### 7.6 Flooding 如何自然提高成本

Issue Flooding 會透過真實機制增加成本：

- 下一回合需輸入更長的 issue queue 與測試摘要。
- 需要更多 patch、test run、rollback 或 reviewer 呼叫。
- 反覆修改增加輸出 token 與 wall-clock time。
- review findings 增加作者修正負擔。
- 最終失敗使所有已花費成本仍進入 CostPerClear 分子。

因此不必額外虛構「弱模型稅」；flooding 本身即可形成經濟懲罰。

---

## 8. 四種核心策略

### 8.1 SOLO：空手自幹

SOLO 是明確策略與正式 baseline：

```text
不建立 PLAN artifact
不強制 test-first
不召喚 reviewer
直接探索、修改、測試與提交
```

優勢是最低流程開銷與最快 one-shot clear；風險是漏掉規格、邊界與 regression。

SOLO 不提供人工 bonus。其優勢完全來自較少模型呼叫、較少 token 與較短 wall-clock time。

### 8.2 PLAN

PLAN 要求在第一次 production patch 前提交並凍結結構化計畫：

```yaml
requirements:
  - ...
invariants:
  - ...
files_to_inspect:
  - ...
risks:
  - ...
test_targets:
  - ...
```

其成本為額外一回合與相應 token。PLAN 的價值須透過較少錯誤、較低 flooding 或較高 Power 反映，不能直接加分。

### 8.3 TDD

TDD 要求：

```text
test-only patch
→ 執行測試
→ 因缺少目標行為而得到預期失敗
→ production patch
→ 測試轉綠
```

Syntax error、import error 或與需求無關的失敗不算有效 red。完成實作後才補上永遠會過的測試，也不算 TDD compliance。

### 8.4 REVIEWER

REVIEWER 召喚 fresh-context agent，輸入僅包含：

- Canonical issue spec。
- 目前 production code 與 diff。
- 公開測試與執行紀錄。
- 作者的可見 artifacts。

Reviewer 不得看到 hidden tests 或 reference patch。Reviewer 最多回傳固定數量、帶證據的 findings；作者仍須自行判斷與修正。

Reviewer 的完整成本計入該 run。

### 8.5 策略組合與切換

在因果實驗中，PLAN、TDD、REVIEWER 是三個二元因子，共八種 loadout：

```text
SOLO                    = P0 T0 R0
PLAN                    = P1 T0 R0
TDD                     = P0 T1 R0
REVIEWER                = P0 T0 R1
PLAN + TDD              = P1 T1 R0
PLAN + REVIEWER         = P1 T0 R1
TDD + REVIEWER          = P0 T1 R1
PLAN + TDD + REVIEWER   = P1 T1 R1
```

SOLO 因而同時是遊戲中的「空手策略」與研究中的 control condition。

在自主模式中，模型可從 SOLO 開局後升級：

```text
SOLO → TDD
SOLO → REVIEWER
PLAN → TDD → REVIEWER
```

但先前花費的成本、時間與 flooding 不會重置。策略切換本身也是 orchestration 能力的一部分。

---

## 9. 純文字回合協定

### 9.1 基本限制

建議最小設定：

```yaml
wall_clock_seconds: 600
max_turns: 8
network_access: false
hidden_tests_visible: false
repo_commit: frozen
```

研究可另發布兩種 track：

1. **End-to-end track**：固定 10 分鐘，真實反映 latency 與工具迴圈。
2. **Capability-normalized track**：固定 action、token 或 tool-call budget，降低 serving latency 與硬體速度的干擾。

### 9.2 可用命令

| 命令 | 功能 |
|---|---|
| `LOOK` | 查看當前 repo／issue 摘要 |
| `INSPECT <path>` | 深讀指定檔案或 symbol |
| `STANCE SOLO` | 宣告空手策略 |
| `PLAY PLAN` | 提交並凍結計畫 |
| `PLAY TDD` | 啟動 test-first 約束 |
| `WRITE_TEST` | 提交 test-only patch |
| `PATCH` | 提交 production diff |
| `RUN_TEST` | 執行允許的公開測試 |
| `SUMMON REVIEWER` | 召喚 reviewer |
| `TRIAGE` | 合併重複 issue、排序 queue、指定下一個目標 |
| `ROLLBACK` | 撤回最近一個有害 patch |
| `COMMIT` | 提交最終 artifact |

### 9.3 Agent 回覆格式

```text
ACTION: PATCH
TARGET_ISSUES: MAIN-1, REG-2
FILES: src/example.py, tests/test_example.py
CLAIM: 修復 recovery 後的重複事件
PATCH:
<unified diff>
```

系統不需要模型輸出私密 chain-of-thought。模型只需提交簡短 intent、目標與可執行 artifact。

### 9.4 每回合流程

```text
1. 引擎顯示目前狀態、成本、剩餘時間與 issue queue
2. Agent 選擇一個動作
3. 引擎在 sandbox 中執行
4. 公開診斷與既有測試更新 issue queue
5. 記錄 token、成本、diff、測試與 backlog
6. 進入下一回合或最終 COMMIT
```

---

## 10. 防止 Hidden-Test 洩漏

動態 flooding 不應把 hidden tests 變成逐步提示。建議分為兩層：

### 10.1 遊戲中可見診斷

可用於更新 queue：

- Public tests。
- Starter repo 原有測試。
- Smoke tests。
- 靜態檢查與 API compatibility checks。
- 明確宣告的 public invariant probes。
- Reviewer findings。
- VCS churn、修改範圍與 rollback 事件。

### 10.2 最終隱藏評分

只在 COMMIT 或時間截止後執行：

- Hidden examples。
- Property-based tests。
- Differential tests。
- Curated mutation tests。
- 效能與資源測試。

Hidden failures可在賽後顯示為 postmortem debt，但不能在同一 run 中回饋給 agent。

---

## 11. 綜合評分與排行榜

### 11.1 綜合分數

通過 hard gate 後，遊戲可顯示：

\[
Utility = 0.55Economy + 0.25Power + 0.20Control
\]

若 critical requirements 未通過：

```text
Clear = 0
Economy = 0
Utility 上限 = 49
```

### 11.2 不應只發布一張總榜

建議至少同時發布：

1. **Clear Rate**：通關率。
2. **Cost per Successful Clear**：成功解題成本。
3. **Tokens per Successful Clear**：包含失敗嘗試的 token-to-clear。
4. **Quality-adjusted Token Yield**：每百萬 token 的品質調整成功產出。
5. **EuTB**：通關率對 token budget 曲線的正規化面積。
6. **Power**：最終 artifact 品質。
7. **Control**：收斂性與 Flood Index。
8. **Flood Token Ratio**：在 backlog 高於初始值時消耗的 token 比例。
9. **One-shot Clear Rate**：空手或第一次正式 patch 即通關比例。
10. **Pareto Frontier**：成本、token、時間與品質的非支配前緣。
11. **Composite Utility**：55/25/20 的遊戲化總分。

Composite 便於遊戲顯示，但研究結論應優先依賴原始維度與 Pareto 分析，避免權重選擇掩蓋真實 trade-off。

### 11.3 策略指標

- `Plan uplift`
- `TDD uplift`
- `Reviewer uplift`
- `Review harm rate`
- `Strategy switch cost`
- `Solo one-shot clear rate`
- `Reviewer finding precision`
- `Author fix conversion`
- `Selection regret`

自主選牌的後悔值：

\[
Regret = U_{best\ forced\ loadout} - U_{autonomous\ choice}
\]

此值衡量模型是否知道「什麼題該空手、什麼題該花成本使用流程」。

---

## 12. Issue Deck 設計

### 12.1 每張 Issue Card 的必要欄位

```yaml
issue_id: phantom-removal-v1
repo_commit: <frozen sha>
archetype: state-recovery
difficulty: medium
wall_clock_seconds: 600
expected_patch_loc: 30-80
public_spec: benchmark/spec.md
public_tests: tests/public/
hidden_tests: benchmark/hidden/
critical_requirements:
  - transient failure must not emit removal
  - real deletion must remain detectable
```

### 12.2 適合的 micro-issue 類型

- 輸入驗證與錯誤契約。
- Parser edge cases。
- 狀態機與 recovery。
- Legacy regression。
- 資源／成本規則。
- deterministic tie-break。
- 小型演算法或效能 invariant。
- 遊戲系統中的牌組選擇、冷啟動與 budget fallback。

### 12.3 不適合 10 分鐘牌庫的 issue

- 需要外部 credential 或不穩定服務。
- 跨多個 deployment 系統的 epic。
- Acceptance criteria 無法客觀化。
- 主要工作是 UI、視覺、敘事或主觀風格判斷。
- 需要數小時建立環境或下載大型依賴。

### 12.4 污染控制

經典題與公開 issue 可能已存在於模型資料中。應採用：

- Frozen commit。
- Canonical spec。
- 不公開 hidden tests。
- 每個母題建立數個語意變體。
- 變更邊界規則、錯誤契約、常數與 recovery 行為，而非只換變數名稱。
- 記錄 issue 原始公開時間與 benchmark 封存時間。

---

## 13. 實驗設計

### 13.1 Forced Loadout：因果效果

目的：估計 PLAN、TDD 與 REVIEWER 的因果 uplift。

設計：

```text
Plan     ∈ {0,1}
TDD      ∈ {0,1}
Reviewer ∈ {0,1}
```

同一模型需在平衡的 issue variants 上跑完八種條件。所有模型每回合解同一張 frozen issue，不能讓不同模型各自抽到不同難度的 issue 後直接比較。

### 13.2 Autonomous Draft：自主策略

目的：評估模型是否能依 issue 特徵選擇適當策略。

流程：

```text
唯讀偵察
→ 選擇 SOLO／PLAN／TDD／REVIEWER 組合
→ 可在後續回合升級或切換
→ 記錄成本與 selection regret
```

### 13.3 Reviewer Track

建議分開發布：

- **Self-team track**：作者與 reviewer 使用同一模型、不同 context。
- **Standard-review track**：所有作者使用同一固定 reviewer。
- **Cross-model party track**：探索作者與 reviewer 的最佳配對。

三種 track 回答不同研究問題，不應混為同一排行榜。

### 13.4 隨機化與配對

- Issue 在「回合」層級抽取，所有模型共享同一題。
- 依 archetype 與 difficulty 分層抽樣。
- 模型順序、loadout 與 issue variant 使用 block randomization。
- 共享相同 repo commit、工具、測試命令與網路限制。
- 每個模型 snapshot、reasoning 設定、量化與 harness 視為獨立 treatment。

### 13.5 建議規模

Pilot：

```text
4 個母題 × 2 個語意變體 × 8 個 loadout
= 每模型 64 runs
```

正式版：

```text
6 個母題 × 3 個語意變體 × 8 個 loadout
= 每模型 144 runs
```

自主選牌另作一輪，不與 forced loadout 共用同一次 run。

---

## 14. 資料蒐集

每個 run 至少記錄：

```yaml
model:
  provider: ...
  snapshot: ...
  reasoning_setting: ...
  quantization: ...
  serving_stack: ...

resources:
  input_tokens: ...
  output_tokens: ...
  reasoning_tokens: ...
  cached_tokens: ...
  wall_clock_ms: ...
  sandbox_cpu_ms: ...
  gpu_seconds: ...
  energy_kwh: ...
  effective_cost: ...
  prompt_bytes: ...
  generated_bytes: ...
  api_calls: ...
  agent_turns: ...

process:
  strategy_loadout: ...
  strategy_switches: ...
  turns: ...
  tool_calls: ...
  test_runs: ...
  reviewer_calls: ...
  patch_count: ...
  touched_files: ...
  added_loc: ...
  deleted_loc: ...
  reverted_loc: ...

quality:
  public_tests: ...
  hidden_tests: ...
  mutation_score: ...
  power_score: ...
  critical_gate: ...

control:
  backlog_by_turn: [...]
  regression_count: ...
  reopen_count: ...
  review_debt_count: ...
  scope_score: ...
  flood_area: ...
  flood_index: ...
  control_score: ...
```

所有 patch、測試結果與 reviewer finding 應具有時間戳與 commit hash，形成可重播的 event log。

---

## 15. 統計分析

### 15.1 主要結果

- Clear rate。
- Cost per successful clear。
- Tokens per successful clear。
- Quality-adjusted Token Yield。
- Effectiveness under Token Budget（EuTB）。
- Marginal Token Yield 與 Flood Token Ratio。
- Power。
- Control／Flood Index。
- Composite Utility。

### 15.2 建議模型

對 Power／Utility：

```text
score ~ model * plan * tdd * reviewer
        + issue_archetype
        + (1 | issue_variant)
```

對 Clear：

```text
logit(clear) ~ model * plan * tdd * reviewer
               + (1 | issue_variant)
```

對成本與 token consumption 可使用 log-normal、Gamma 或 hurdle model；完全未通關的模型應保留為失敗觀察，而非從成本或 token 分析中刪除。`EuTB` 使用 encounter-level bootstrap 建立信賴區間，並對預先註冊的 token budget 上限做敏感度分析。

對 flooding：

```text
flood_index ~ model * strategy
              + issue_regression_surface
              + (1 | issue_variant)
```

### 15.3 三維能力估計

建議採用階層式多維模型，將 issue 難度與類型分離：

```text
θ_model = [Economy, Power, Control]
β_issue = [cost pressure, correctness difficulty, regression surface]
```

角色卡顯示 posterior mean 與不確定區間，而非只顯示單一點估計。

### 15.4 中介與交互作用

重點分析：

- `Model × TDD`：弱模型是否更依賴 TDD。
- `Model × Reviewer`：reviewer 是否補償作者弱點。
- `TDD × Reviewer`：兩者互補或重複。
- `Issue archetype × Plan`：規格複雜度是否決定 planning 收益。
- `Control → Cost`：flooding 是否中介模型能力與成本。
- `Control → TokensPerClear`：低收斂性是否造成 token snowball。
- `Flood Token Ratio → Marginal Token Yield`：自生 backlog 是否降低每千 token 的品質增益。
- `Solo one-shot clear → Economy`：空手成功率是否驅動成本優勢。

---

## 16. 遊戲介面範例

```text
════════════════════════════════════════
 PATCHMUD — TURN 5 / 8
════════════════════════════════════════

ENCOUNTER
  Phantom Removal
  Type: STATE / RECOVERY / REGRESSION

MODEL
  Economy   81
  Power     59
  Control   34

STRATEGY
  Opening stance: SOLO
  Current mode: Emergency TDD

RESOURCE
  Cost spent:       24.8 credits
  Time remaining:   02:41
  Context tokens:   18,420

ISSUE QUEUE
  [MAIN]       Project false removal
  [REOPEN ×2]  Subtree failure still removes project
  [REGRESSION] Real deletion no longer detected
  [REGRESSION] Duplicate change event
  [DEBT]       Reviewer finding not addressed

FLOOD PRESSURE
  ███████░░░  7 / 10
  STATUS: ISSUE FLOOD

AVAILABLE ACTIONS
  PATCH
  RUN_TEST
  TRIAGE
  ROLLBACK
  SUMMON_REVIEWER
  COMMIT

> PATCH TARGET=REGRESSION-1
```

引擎回覆：

```text
PATCH RESULT

Resolved:
  [REGRESSION] Real deletion no longer detected

Still open:
  [MAIN]
  [REOPEN ×2]
  [REGRESSION] Duplicate change event

New issues:
  None

Flood pressure:
  7 → 5

Net turn result:
  +1 issue resolved
  +0 issues spawned
  Cost: 3.1 credits
```

最終結算：

```text
FINAL ENCOUNTER

Critical gate                  PASS
Functional correctness       54 / 60
Robustness                    12 / 15
Compatibility                  9 / 10
Maintainability                8 / 10
Runtime efficiency             5 /  5
────────────────────────────────────
POWER                          88 / 100

Effective cost                27.9 credits
Reference cost                18.0 credits
ECONOMY                        80 / 100

Flood area                    16.2
Regression spawned             2
Reopened issues                2
CONTROL                        48 / 100

COMPOSITE UTILITY
  0.55×80 + 0.25×88 + 0.20×48 = 75.6

RESULT: CLEAR, BUT FLOODED
```

---

## 17. 系統架構

### 17.1 元件

```text
┌────────────────────┐
│ Text Game Frontend │
└─────────┬──────────┘
          │ structured actions
┌─────────▼──────────┐
│ Turn / Rule Engine │
├────────────────────┤
│ Strategy Enforcer  │
│ Cost Ledger        │
│ Issue Queue        │
│ Event Log          │
└─────────┬──────────┘
          │
┌─────────▼──────────┐
│ Sandbox Executor   │
├────────────────────┤
│ Repo checkout      │
│ Patch application  │
│ Public tests       │
│ Static checks      │
└─────────┬──────────┘
          │ final commit
┌─────────▼──────────┐
│ Hidden Evaluator   │
├────────────────────┤
│ Hidden tests       │
│ Property tests     │
│ Mutation tests     │
│ Performance tests  │
└─────────┬──────────┘
          │
┌─────────▼──────────┐
│ Metrics / Research │
│ Store              │
└────────────────────┘
```

### 17.2 引擎必須保證

- 每個 run 從相同 frozen commit 開始。
- 所有工具輸入輸出可稽核。
- 不允許模型讀取 hidden evaluator。
- Reviewer context 與作者 context 分離。
- 成本計價表有版本與時間戳。
- 隨機 seed、issue variant 與執行環境可重播。
- 公開診斷與隱藏評分嚴格隔離。

---

## 18. 排名模式與遊戲模式的分離

### 18.1 Ranked Research Mode

- 無隨機暴擊、卡牌掉落或角色 buff。
- 三維能力是事後估計，不改變工具權限。
- 所有模型共享同一 issue、commit、時間與測試環境。
- 最終結果以 artifact evaluator 與成本 ledger 為準。

### 18.2 RPG Exhibition Mode

可加入：

- Debt cards 佔據手牌。
- Flooded 狀態限制部分動作。
- 角色職業與隊伍分工。
- 隨機事件、卡牌稀有度與賽季資源。

但這些機制不得混入正式研究排行榜，除非所有模型共享相同 seed，且研究問題正是探討該機制。

---

## 19. 有效性威脅與限制

### 19.1 成本可比性

API 價格、地端折舊與時間價值具有假設性。應發布多組成本情境，並保留原始資源數據。

### 19.2 Benchmark 污染

公開 issue 與經典 kata 可能被模型記憶。需要語意變體、封存時間與定期更新題庫。

### 19.3 Turn 限制效應

固定回合可能偏好單次輸出能力；固定 wall-clock 可能偏好低 latency serving。兩種 track 應同時存在。

### 19.4 Hidden-test 過度代表規格

Hidden tests 仍可能不完整。應搭配 requirement-group weighting、property tests、mutation tests 與人工 audit。

### 19.5 Composite 權重爭議

55/25/20 是成本優先場景的預設，而非普遍真理。應提供權重敏感度分析與 Pareto frontier。

### 19.6 Reviewer 混雜

同模型 reviewer 同時混入作者能力與 reviewer 能力；固定 reviewer 又可能偏好特定程式風格。兩種 track 應分開報告。

### 19.7 Issue Flooding 的可觀測性

遊戲中只能依公開診斷判定 flooding，無法即時使用 hidden failures。最終 hidden failures應視為賽後 artifact 缺陷，而非同場提示。

### 19.8 Token 單位的跨模型可比性

不同模型與語言可能使用不同 tokenizer；同一段程式碼的 token 數不一定相同。Provider 對 reasoning、cached input 與 output 的揭露方式也不一致。因此 raw token 不應被宣稱為跨模型的精確 FLOP 或能源代理。研究需同時發布互斥 token 類別、原始 bytes、wall-clock、貨幣成本與地端能耗，並把 raw-token 比較主要限制在同模型或同 tokenizer 的 ablation。

### 19.9 Token budget 選擇偏誤

`EuTB` 的最大預算若依正式結果事後選擇，會造成排名偏誤。預算上限與積分網格應由 pilot set 預先註冊，並發布至少一組敏感度分析。

---

## 20. 最小可行研究版本

### Phase 1：離線評分核心

- 建立 4 個 issue 母題、每題 2 個變體。
- 實作 frozen repo、public tests 與 hidden evaluator。
- 建立 Power 與成本 ledger。
- 記錄完整 patch/test event log。

### Phase 2：回合引擎與四策略

- 實作 SOLO、PLAN、TDD、REVIEWER。
- 加入 8-turn／600-second 限制。
- 驗證策略 compliance。
- 建立 fresh-context reviewer protocol。

### Phase 3：Issue Flooding

- 實作 dynamic issue queue。
- 偵測 reopen、regression、scope creep、review debt 與 churn。
- 計算 FloodArea、FloodIndex 與 Control。

### Phase 4：Forced Loadout Pilot

```text
4 題 × 2 變體 × 8 loadouts × N models
```

校準：

- 經濟轉換函數。
- Flood 狀態門檻。
- Power pass gate。
- Issue difficulty 與預期修改規模。

### Phase 5：Autonomous Draft

- 允許模型自行選牌與切換策略。
- 計算 selection regret。
- 比較模型自我管理能力。

### Phase 6：正式賽季

- 擴增至 6–12 個 issue 母題。
- 加入 fixed reviewer 與 cross-model party track。
- 發布三維角色卡、Pareto frontier 與可重播 logs。

---

## 21. 預期研究貢獻

PatchMUD 的主要貢獻不是把傳統 benchmark 套上遊戲皮，而是把真實 coding-agent 工作中容易被忽略的三個面向變成一級評測對象：

1. **成本優先：** 從單次價格轉向 Cost per Successful Issue Clear。
2. **過程可觀測：** 從只看 final patch 轉向分析 backlog 軌跡、regression 與 churn。
3. **策略可比較：** 將空手、PLAN、TDD 與 REVIEWER 置於同一可控實驗中。

最終要回答的不是單純「哪個模型最會寫程式」，而是：

> **哪個模型能以最低總成本，把 issue queue 穩定地從 1 降到 0，並留下可維護、可驗證、沒有額外債務的 patch？**

---

## 22. 參考文獻與方法依據

1. Fan, Z., Vasilevski, K., Lin, D., et al. (2025). **SWE-Effi: Re-Evaluating Software AI Agent System Effectiveness Under Resource Constraints.** arXiv:2509.09853. 本報告的 EuTB 與 resource-budget AUC 採用其核心方法方向，並將 token snowball／expensive failure 延伸為 PatchMUD 的 Flood Token Ratio 與 Issue Flooding 軌跡分析。  
   https://arxiv.org/abs/2509.09853

2. Ding, Y., & Zhang, L. (2026). **SWE-Replay: Efficient Test-Time Scaling for Software Engineering Agents.** arXiv:2601.22129. 該研究同時報告 resolve rate、input/output tokens 與平均成本，支持將解題能力和資源消耗共同分析。  
   https://arxiv.org/abs/2601.22129

3. **SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents** (2026). arXiv:2601.16746. 該研究顯示 coding-agent context 管理可同時改變 token consumption、interaction rounds、成本與成功率，支持將 token efficiency 視為獨立稽核維度，而非僅以最終正確率衡量。  
   https://arxiv.org/abs/2601.16746

4. OpenAI (持續更新). **What are tokens and how to count them?** 用於說明 tokenization 會因模型與 encoding 而異，且 usage metadata 可區分 input、output、cached 與 reasoning 類別。  
   https://help.openai.com/articles/4936856-what-are-tokens-and-how-to-count-them

---

# 附錄 A：Encounter 設定範例

```yaml
encounter:
  id: phantom-removal-v1
  repo: example/project
  repo_commit: abc123
  archetype: state-recovery
  difficulty: medium
  expected_patch_loc: [30, 80]

limits:
  wall_clock_seconds: 600
  max_turns: 8
  network_access: false

strategies:
  solo: true
  plan: optional
  tdd: optional
  reviewer: optional

scoring:
  composite_weights:
    economy: 0.55
    power: 0.25
    control: 0.20

  power:
    functional: 60
    robustness: 15
    compatibility: 10
    maintainability: 10
    runtime_efficiency: 5

critical_requirements:
  - id: CR-1
    text: transient scan failure must not emit removal
  - id: CR-2
    text: successful recovery must still detect real deletion

flooding:
  visible_sources:
    - public_tests
    - starter_tests
    - smoke_tests
    - compatibility_checks
    - reviewer_findings
    - vcs_churn
  hidden_sources_visible_during_run: false
```

# 附錄 B：事件日誌範例

```json
{
  "run_id": "run-2026-0017",
  "turn": 4,
  "timestamp": "2026-07-16T10:24:18Z",
  "action": "PATCH",
  "strategy_state": ["SOLO", "TDD_ESCALATED"],
  "target_issues": ["MAIN-1", "REG-2"],
  "cost_delta": 2.81,
  "files_changed": [
    "src/snapshot.py",
    "tests/test_snapshot.py"
  ],
  "test_result": {
    "passed": 42,
    "failed": 1
  },
  "queue_before": 4,
  "resolved": ["REG-2"],
  "spawned": [],
  "queue_after": 3,
  "reverted_loc": 0
}
```

# 附錄 C：研究輸出範例

```text
MODEL A
  Clear rate                 82%
  Cost per successful clear 14.2 credits
  Tokens per clear          36.4k
  QATY                       22.7 / M tokens
  EuTB                       0.74
  Flood token ratio          0.08
  Economy                    88 ± 3
  Power                      84 ± 2
  Control                    79 ± 4
  Solo one-shot clear        51%
  Best strategy              SOLO on local fixes
  Highest uplift             TDD on regressions

MODEL B
  Clear rate                 76%
  Cost per successful clear 19.8 credits
  Tokens per clear          94.1k
  QATY                        8.3 / M tokens
  EuTB                       0.41
  Flood token ratio          0.37
  Economy                    71 ± 5
  Power                      80 ± 3
  Control                    46 ± 7
  Solo one-shot clear        24%
  Common failure mode        Issue Flooding
  Highest uplift             TDD + fixed reviewer
```
