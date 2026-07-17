from kvconfig import parse_kv_line


def test_simple_pair():
    assert parse_kv_line("name=Ada") == ("name", "Ada")


def test_comment_line_returns_none():
    assert parse_kv_line("# comment") is None


def test_blank_line_returns_none():
    assert parse_kv_line("   ") is None


def test_missing_equals_returns_none():
    assert parse_kv_line("noequals") is None


def test_whitespace_trimmed():
    assert parse_kv_line("  key = value  ") == ("key", "value")
