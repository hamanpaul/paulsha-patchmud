"""倉儲儲位代碼驗證模組：input-validation-v2（變體）fixture 的待修標的。"""


def normalize_bin_code(code):
    """正規化：去除頭尾空白並轉大寫。"""
    return code.strip().upper()


def is_valid_bin_code(code):
    """有效儲位代碼：正規化後為 4-8 個英數字元、以英文字母開頭、以數字結尾（檢查碼）。"""
    code = normalize_bin_code(code)
    if not (4 <= len(code) <= 8):
        return False
    if not code[:1].isalpha():
        return False
    return code.isalnum()
