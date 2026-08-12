---
type: feat
scope: report
---
report 同步落盤 `report.json` 機器契約（issue #26）——與 `report.yaml` 同一個 report dict、`json.dumps(ensure_ascii=False, sort_keys=True, allow_nan=False)`。YAML 供人讀、JSON 供下游程式讀：PyYAML 的 indentless sequence 與長 scalar 折行（實跑驗證中 cortex 零依賴 parser 連踩兩例）不該成為檔案契約的解析門檻；`allow_nan=False` 斷言原始 inf/nan 不得進 report。
