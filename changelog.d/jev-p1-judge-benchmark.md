---
type: feat
scope: judge
---
新增 JEV P1 semantic-acceptance-judge 實驗（issue #42）：`patchmud/judge/` 提供 structured-judge 契約（typed Choice／Noul／Score request 與 fail-closed answer 驗證）、TypeSafe HTTP adapter（pin `jev-1.13.0`，429／529 退避重試，牌價換算成本）與 Claude／Copilot CLI 純補全 judge；36 題三態驗收判決題庫（hidden 12、誤導情境 21）附凍結 digest；runner 逐次落盤 request／response hash、延遲與成本 basis 並可續跑；確定性 evaluator 依預先登錄門檻判 go／no-go。入口 `python -m patchmud.judge`，不接入 engine／pilot／report，production 零 JEV 依賴。首輪 `20260927-p1` 判定 **no-go**（JEV hidden false-satisfied 2、macro-F1 0.817、誘導翻轉率 11.1%；成本與延遲遠優於 LLM judge，但品質落後最佳既有 judge 約 17pp），結論見 `experiments/jev-p1/results/20260927-p1/findings.md`。
