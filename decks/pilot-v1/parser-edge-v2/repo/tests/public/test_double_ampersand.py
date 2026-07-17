from querystring import parse_query_string


def test_double_ampersand_no_bogus_key():
    assert parse_query_string("a=1&&b=2") == {"a": "1", "b": "2"}
