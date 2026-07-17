import pytest

from roster import diff_removed


@pytest.mark.parametrize(
    "prev,cur,expected",
    [
        ({"d1": "OK", "d2": "OK"}, {"d1": "OK", "d2": "UNREACHABLE"}, []),
        (
            {"d1": "OK", "d2": "OK", "d3": "OK"},
            {"d1": "OK", "d2": "UNREACHABLE"},
            ["d3"],
        ),
        ({"d1": "OK"}, {"d1": "UNREACHABLE"}, []),
    ],
)
def test_unreachable_excluded(prev, cur, expected):
    assert diff_removed(prev, cur) == expected
