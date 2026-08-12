---
type: fix
scope: ci
---
`tests.yml` 的依賴安裝不再吞失敗——原本 `pip install -e ".[test]" || python -m pip install -e . || true` 的結尾 `|| true` 會讓 packaging／依賴壞掉時靜默放行。測試 gate 改為無條件執行後這尤其危險：安裝失敗會以難解的 import error 收場，或更糟——測到的是上一次殘留的環境。改為直接 `pip install -e ".[test]"`，失敗即當場中斷（`[test]` extras 本 repo 恆定存在，fallback 分支無實益）。
