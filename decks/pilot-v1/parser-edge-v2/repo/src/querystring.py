"""URL 查詢字串解析模組：parser-edge-v2（變體）fixture 的待修標的。"""


def parse_query_string(qs):
    """解析查詢字串 `a=1&b=2` 為 dict；應忽略連續或收尾的 `&` 產生的空 token。"""
    result = {}
    for token in qs.split("&"):
        if "=" in token:
            key, value = token.split("=", 1)
        else:
            key, value = token, ""
        result[key] = value
    return result
