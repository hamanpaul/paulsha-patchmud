import pytest

from version import compare_versions


@pytest.mark.parametrize(
    "v1,v2,expected",
    [
        ("1.2.3", "1.2.3", 0),
        ("1.2", "1.2.0", 0),
        ("1.9", "1.10", -1),
        ("1.10", "1.9", 1),
        ("1.2.9", "1.2.10", -1),
        ("2.0", "1.9", 1),
        ("1.2", "1.2.1", -1),
        ("10.0", "9.0", 1),
    ],
)
def test_version_ordering_cases(v1, v2, expected):
    assert compare_versions(v1, v2) == expected
