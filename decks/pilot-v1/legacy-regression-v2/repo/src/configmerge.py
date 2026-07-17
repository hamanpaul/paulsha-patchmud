"""設定合併模組：legacy-regression-v2（變體：合併語意的刪除標記契約，
與母題的數值型別 bug 不同機制，見 provenance.yaml）。

`override` 內某鍵的值若為 `None`，代表舊版呼叫端要求把該鍵從結果中
整個移除（既有 deletion-marker 慣例）；其餘鍵一律以 `override` 值覆蓋
`base`，`base` 獨有的鍵維持原樣不變（既有大量呼叫端仰賴此穿透行為）。
"""


def merge_configs(base, override):
    """合併設定：`override` 覆蓋 `base`；`override` 內值為 None 代表移除該鍵。"""
    result = dict(base)
    for key, value in override.items():
        result[key] = value
    return result
