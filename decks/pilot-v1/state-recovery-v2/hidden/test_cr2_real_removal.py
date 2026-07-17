import pytest

from roster import diff_removed


@pytest.mark.parametrize(
    "prev,cur,expected",
    [
        ({"d1": "OK", "d2": "OK"}, {"d1": "OK", "d2": "OK"}, []),
        ({"d1": "OK", "d2": "OK"}, {"d1": "OK"}, ["d2"]),
        ({}, {}, []),
        ({"d1": "OK"}, {"d1": "OK", "d9": "OK"}, []),
    ],
)
def test_real_removal_detection(prev, cur, expected):
    assert diff_removed(prev, cur) == expected
