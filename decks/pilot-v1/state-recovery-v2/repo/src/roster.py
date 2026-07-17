"""裝置名冊差異模組：state-recovery-v2（變體：以 sentinel 值標記暫時性失敗，
而非獨立錯誤清單；邊界規則與常數與母題不同，見 provenance.yaml）。

`current` 內某裝置的狀態若為 `"UNREACHABLE"`，代表本次同步時該裝置暫時
連不上（暫時性網路問題），不代表裝置真的被移除。只有裝置完全不在
`current` 內（同步時直接查無此裝置）才是真正的移除。
"""

_UNREACHABLE = "UNREACHABLE"


def diff_removed(previous, current):
    """回傳 `current` 相對 `previous` 真正被移除的裝置名稱（排序 list）。"""
    removed = []
    for name in previous:
        if name not in current or current[name] == _UNREACHABLE:
            removed.append(name)
    return sorted(removed)
