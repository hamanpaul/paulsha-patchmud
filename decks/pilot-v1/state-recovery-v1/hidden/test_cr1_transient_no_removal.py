import pytest

from snapshot import diff_removed


@pytest.mark.parametrize(
    "prev,cur,scan_errors,expected",
    [
        ({"a": 1, "b": 2}, {"a": 1}, ("b",), []),
        ({"a": 1, "b": 2, "c": 3}, {"a": 1}, ("b",), ["c"]),
        ({"a": 1, "b": 2}, {}, ("a", "b"), []),
    ],
)
def test_transient_failure_excluded(prev, cur, scan_errors, expected):
    assert diff_removed(prev, cur, scan_errors=set(scan_errors)) == expected
