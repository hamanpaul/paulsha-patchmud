"""快照差異模組：state-recovery-v1（改編自研究報告附錄 A phantom-removal 案例）。

比對兩次「掃描結果」（模擬檔案系統快照），偵測被刪除的項目。掃描本身
可能因暫時性錯誤（該次掃描沒能讀到某項目的狀態）而回傳不完整結果——
這種暫時性失敗不能被誤判為刪除；只有掃描確實成功且項目不存在時，才是
真正的刪除。
"""


def diff_removed(previous, current, scan_errors=()):
    """回傳被真正移除的項目名稱（排序 list）。

    `previous`：上次成功快照 `{name: state}`。
    `current`：本次掃描結果 `{name: state}`（掃描失敗的項目不會出現在此）。
    `scan_errors`：本次掃描失敗（暫時性錯誤）的項目名稱集合——這些項目
    即使不在 `current` 裡，也絕不能被判定為刪除。
    """
    removed = []
    for name in previous:
        if name not in current:
            removed.append(name)
    return sorted(removed)
