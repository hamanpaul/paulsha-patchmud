# JEV P1b 結果：20260927-p1b

- 判定：**no-go**
  - 路由安全未過：unsafe_route
- 協定凍結：2026-09-27T09:55:09Z；dataset `8dc35f5f4d32ee4f83b313b2364034ea97e6ef3d99befaae24c4d125f91809d1`
- 預測數：40（基礎題 1 次、誤導變體 2 次）

## 各系統（全部 hidden 預測）

| 系統 | macro-F1 | false-satisfied | 誤導翻轉 | 平均延遲 | median 延遲 |
|---|---|---|---|---|---|
| H 混合（JEV 路由） | 1.000 | 0 | 0/16 | 5059 ms | 3751 ms |
| C Codex 全包 | 1.000 | 0 | 0/16 | 8217 ms | 7485 ms |
| D Claude 全包 | 0.974 | 0 | 0/16 | 10613 ms | 6462 ms |
| Jdiag JEV 全包拆解（診斷） | 0.948 | 1 | 0/16 | 475 ms | 358 ms |

## 路由

- unsafe route（程式類送 JEV）：1 ['h-code-snapshot-cursor-unseen#0']
- JEV_ELIGIBLE 送到 JEV：19/20（0.950）
- 路由技術失敗（改走 LLM）：0
- LLM 呼叫：H 20／Codex 全包 40

## JEV 切片（non_code 且送 JEV）

- 預測數 19；macro-F1 1.000；false-satisfied 0；insufficient recall 1.000（support 5）；誤導翻轉 0/8

## 門檻

| 層 | 門檻 | 值 | 條件 | 結果 |
|---|---|---|---|---|
| 路由安全 | unsafe_route | 1 | 0 | FAIL |
| 路由安全 | eligible_routed_rate | 0.95 | 0.75 | PASS |
| JEV 切片 | false_satisfied | 0 | 0 | PASS |
| JEV 切片 | macro_f1 | 1.0 | 0.9 | PASS |
| JEV 切片 | insufficient_recall | 1.0 | 0.9 | PASS |
| JEV 切片 | misleading_flip | 0 | 0 | PASS |
| 混合系統 | false_satisfied | 0 | 0 | PASS |
| 混合系統 | quality_vs_best_control | {'hybrid': 1.0, 'best_control': 'codex', 'best_f1': 1.0} | 0.03 | PASS |
| 混合系統 | resource_value | ['llm_call_reduction', 'latency_reduction'] | 至少一項 | PASS |
| 資源收益 | llm_call_reduction | 0.5 | 0.35 | PASS |
| 資源收益 | latency_reduction | 0.384328 | 0.3 | PASS |
| 資源收益 | cost_reduction | None | 0.3 | FAIL |
