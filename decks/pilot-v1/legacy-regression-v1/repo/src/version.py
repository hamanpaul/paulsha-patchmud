"""版本字串比較模組：legacy-regression-v1 fixture 的待修標的。"""


def compare_versions(v1, v2):
    """比較兩個版本字串；v1 < v2 回傳 -1，相等回傳 0，v1 > v2 回傳 1。

    舊版相容：允許版本字串長度不同（例如 "1.2" 與 "1.2.0"），缺的分量
    視為 0（既有呼叫端仍大量使用兩段式版本號，此行為不得破壞）。
    """
    parts1 = v1.split(".")
    parts2 = v2.split(".")
    length = max(len(parts1), len(parts2))
    for i in range(length):
        p1 = parts1[i] if i < len(parts1) else "0"
        p2 = parts2[i] if i < len(parts2) else "0"
        if p1 != p2:
            return -1 if p1 < p2 else 1
    return 0
