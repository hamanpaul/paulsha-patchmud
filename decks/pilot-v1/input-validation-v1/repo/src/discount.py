"""折扣碼驗證模組：input-validation-v1 fixture 的待修標的。"""


def normalize_code(code):
    """正規化：去除頭尾空白並轉大寫。"""
    return code.strip().upper()


def is_valid_code(code):
    """有效折扣碼：正規化後為 6-10 個英數字元，且以英文字母開頭。"""
    code = normalize_code(code)
    if not code:
        return False
    if not code[0].isalpha():
        return False
    return code.isalnum()
