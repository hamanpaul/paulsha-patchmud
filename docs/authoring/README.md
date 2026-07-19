# 出題：把已解決的 bug 變成關卡

`patchmud author-encounter` 把一個**已解決（closed）的 bug** 以結構化 `source.yaml`
凍結成一道 deck 關卡。關鍵洞察：一個修好的 bug 天生就有出題所需的全部素材。

## closed bug ↔ deck 對應

| 已解決 bug 的產物 | deck 關卡的產物 |
|---|---|
| 修好前的原始碼 | `repo/`（待修標的） |
| 修補的 diff（PR/commit） | `hidden/reference.patch`（正解） |
| 驗證修補的測試（看得到的） | `repo/tests/public/`（public probe） |
| 更嚴的邊界／回歸測試（藏起來的） | `hidden/test_*.py`（critical probe） |
| issue 標題／描述 | `card.yaml` 的需求文字 + 白話 `briefing` |
| issue 出處（URL/#N） | `provenance.yaml` 的來源記錄 |

## 使用

```bash
patchmud author-encounter <source.yaml> --into <deck_dir>
```

新關寫入 `<deck_dir>/<issue_id>/`。範例輸入見
[`examples/discount-clamp.source.yaml`](examples/discount-clamp.source.yaml)。

## source.yaml 欄位

| 欄位 | 說明 |
|---|---|
| `schema_version` | 固定 `1` |
| `issue_id` | 關卡代號（→ 目錄名） |
| `archetype` / `difficulty` | 分類與難度（`easy`/`medium`/`hard`） |
| `summary` | 一句話 bug 說明（→ `briefing` + 缺省 MAIN-1 需求文字） |
| `origin.source` / `origin.fixed_at` | 來源 bug 出處與修補日期（→ provenance） |
| `allowed_paths` / `expected_paths` | 允許／預期修改路徑 |
| `buggy_repo` | 修好前的檔案樹（`path: 內容`）→ `repo/` |
| `reference_patch` | 把 `buggy_repo` 修好的 unified diff → `hidden/reference.patch` |
| `public_tests` | 看得到的驗證測試（`repo-relative path: 內容`）→ `repo/tests/public/` |
| `hidden_tests` | 藏起來的關鍵測試（`bare 檔名: 內容`）→ `hidden/` |
| `requirements`（選填） | 公開需求 `[{id, text}]`；缺省由 `summary` 生一條 MAIN-1，數量須與 `public_tests` 一致 |
| `rubric_points`（選填） | `{functional: N}`；缺省 60 |

## 品質閘（凍結前強制）

`author-encounter` 產出關卡後，走**與 `validate-deck` 完全相同的隔離 CI** 驗證，
唯有全過才凍結，否則 fail-closed：

- **bug 為真**：baseline（未套 patch）時 MAIN public probe 必須 **紅**（issue 可重現）、regression 綠。
- **fix 為真**：套上 `reference.patch` 後，public 與 hidden probe 必須 **全綠**、critical gate 成立。

這道閘讓「表面過、暗地掛」這個 benchmark 靈魂在**出題時就被強制**——沒有能區分
「真修好」與「表面糊弄」的 hidden 測試，題就出不出來。實跑一例：兩個解都通過看得到的
public 測試（記分板都顯示「修好了 MAIN-1」），但只夾上界、漏掉負折扣的弱解被 hidden
測試抓出來，判定未通關。

## 為什麼是凍結固定、而非即時抓現實 issue

benchmark 需要可重現（同輸入同結果、可位元重播）、要有標準答案與暗測、且要防訓練污染。
現實 issue 沒有現成的正解與 hidden robustness 測試，且模型可能訓練時已見過。因此抽換發生在
**作題時**（把現實 bug 凍結成關卡），而非**跑分時**。
