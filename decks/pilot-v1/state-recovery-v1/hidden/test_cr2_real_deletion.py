import pytest

from snapshot import diff_removed


@pytest.mark.parametrize(
    "prev,cur,expected",
    [
        ({"a": 1, "b": 2}, {"a": 1, "b": 2}, []),
        ({"a": 1, "b": 2}, {"a": 1}, ["b"]),
        ({"a": 1}, {"a": 1, "d": 4}, []),
        ({}, {}, []),
    ],
)
def test_real_deletion_detection(prev, cur, expected):
    assert diff_removed(prev, cur) == expected
