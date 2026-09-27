# JEV P1b 結果：20260927-p1b-r2

- 判定：**no-go**
  - JEV 切片未過：false_satisfied, macro_f1, insufficient_recall
  - 混合系統未過：false_satisfied, quality_vs_best_control
- 協定凍結：2026-09-27T10:52:19Z；dataset `6343964ac6fd04ca17b457de93c110a25c914c781635267ddd0ab6845f2d338c`
- 預測數：40（基礎題 1 次、誤導變體 2 次）

## 各系統（全部 hidden 預測）

| 系統 | macro-F1 | false-satisfied | 誤導翻轉 | 平均延遲 | median 延遲 |
|---|---|---|---|---|---|
| H 混合（JEV 路由） | 0.923 | 3 | 0/16 | 3871 ms | 2911 ms |
| C Codex 全包 | 1.000 | 0 | 0/16 | 6795 ms | 5994 ms |
| D Claude 全包 | 1.000 | 0 | 0/16 | 5556 ms | 5407 ms |
| Jdiag JEV 全包拆解（診斷） | 0.923 | 3 | 0/16 | 333 ms | 308 ms |

## 路由

- unsafe route（程式類送 JEV）：0 []
- JEV_ELIGIBLE 送到 JEV：20/20（1.000）
- 路由技術失敗（改走 LLM）：0
- LLM 呼叫：H 20／Codex 全包 40

## JEV 切片（non_code 且送 JEV）

- 預測數 20；macro-F1 0.856；false-satisfied 3；insufficient recall 0.625（support 8）；誤導翻轉 0/8

## 門檻

| 層 | 門檻 | 值 | 條件 | 結果 |
|---|---|---|---|---|
| 路由安全 | unsafe_route | 0 | 0 | PASS |
| 路由安全 | eligible_routed_rate | 1.0 | 0.75 | PASS |
| JEV 切片 | false_satisfied | 3 | 0 | FAIL |
| JEV 切片 | macro_f1 | 0.85641 | 0.9 | FAIL |
| JEV 切片 | insufficient_recall | 0.625 | 0.9 | FAIL |
| JEV 切片 | misleading_flip | 0 | 0 | PASS |
| 混合系統 | false_satisfied | 3 | 0 | FAIL |
| 混合系統 | quality_vs_best_control | {'hybrid': 0.922963, 'best_control': 'codex', 'best_f1': 1.0} | 0.03 | FAIL |
| 混合系統 | resource_value | ['llm_call_reduction', 'latency_reduction'] | 至少一項 | PASS |
| 資源收益 | llm_call_reduction | 0.5 | 0.35 | PASS |
| 資源收益 | latency_reduction | 0.43034 | 0.3 | PASS |
| 資源收益 | cost_reduction | None | 0.3 | FAIL |
