import pytest

from src.parser import parse


def test_single_row():
    assert parse("id,name\n1,Ada") == [{"id": "1", "name": "Ada"}]


def test_quoted_comma_and_escaped_quote():
    assert parse('id,name,note\n1,"Ada, Lovelace","said ""hello"""') == [
        {"id": "1", "name": "Ada, Lovelace", "note": 'said "hello"'}
    ]


def test_blank_optional_and_duplicate_headers_are_distinct_errors():
    assert parse("id,name,note\n1,Ada,")[0]["note"] == ""
    with pytest.raises(ValueError, match="header"):
        parse("id,id\n1,2")
    with pytest.raises(ValueError, match="row width"):
        parse("id,name\n1")
