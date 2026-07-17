import pytest

from querystring import parse_query_string


@pytest.mark.parametrize(
    "qs,expected",
    [
        ("a=1&b=2", {"a": "1", "b": "2"}),
        ("a=1&&b=2", {"a": "1", "b": "2"}),
        ("a=1&", {"a": "1"}),
        ("&a=1", {"a": "1"}),
        ("flag", {"flag": ""}),
        ("", {}),
        ("a=1&a=2", {"a": "2"}),
    ],
)
def test_query_string_cases(qs, expected):
    assert parse_query_string(qs) == expected
