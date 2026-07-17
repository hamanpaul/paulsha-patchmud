"""簡易設定檔 key=value 逐行解析模組：parser-edge-v1 fixture 的待修標的。"""


def parse_kv_line(line):
    """解析設定檔一行 `key=value`；回傳 (key, value) 或 None（註解／空行／無效格式）。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "=" not in stripped:
        return None
    parts = stripped.split("=")
    key = parts[0].strip()
    value = parts[1].strip()
    if not key:
        return None
    return key, value
