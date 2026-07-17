import pytest

from kvconfig import parse_kv_line


@pytest.mark.parametrize(
    "line,expected",
    [
        ("a=1", ("a", "1")),
        ("url=http://x?a=1", ("url", "http://x?a=1")),
        ("k=v=v2=v3", ("k", "v=v2=v3")),
        ("# c=1", None),
        ("", None),
        ("noeq", None),
        ("  spaced = val ue  ", ("spaced", "val ue")),
    ],
)
def test_kv_parsing_cases(line, expected):
    assert parse_kv_line(line) == expected
