---
type: feat
scope: authoring
---
結構化出題模組——patchmud/authoring/ 把已解決的 closed bug 以 source.yaml 凍結成 deck 關卡（buggy_repo→repo/、reference_patch→hidden/、public/hidden 測試各就位、summary→briefing），含 bug 可重現 + fix 為真的雙向品質閘（走與 validate-deck 相同的隔離 CI）。新增 author-encounter 子命令、示範 source 與產出的 authored-demo 關卡、docs/authoring 出題文件。
