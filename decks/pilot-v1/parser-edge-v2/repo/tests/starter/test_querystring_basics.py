from querystring import parse_query_string


def test_simple_pairs():
    assert parse_query_string("a=1&b=2") == {"a": "1", "b": "2"}


def test_value_without_equals_is_empty_string():
    assert parse_query_string("flag") == {"flag": ""}


def test_last_value_wins_on_duplicate_key():
    assert parse_query_string("a=1&a=2") == {"a": "2"}
