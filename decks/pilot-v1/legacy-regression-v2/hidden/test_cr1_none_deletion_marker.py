import pytest

from configmerge import merge_configs


@pytest.mark.parametrize(
    "base,override,expected",
    [
        ({"a": 1, "b": 2}, {"b": None}, {"a": 1}),
        ({"a": 1}, {"z": None}, {"a": 1}),
        ({"a": 1, "b": 2, "c": 3}, {"b": None, "c": 9}, {"a": 1, "c": 9}),
        ({}, {"a": None}, {}),
        ({"a": None}, {}, {"a": None}),
    ],
)
def test_none_deletion_marker(base, override, expected):
    assert merge_configs(base, override) == expected
