from kvconfig import parse_kv_line


def test_value_with_equals_preserved():
    assert parse_kv_line("url=http://x?a=1") == ("url", "http://x?a=1")
