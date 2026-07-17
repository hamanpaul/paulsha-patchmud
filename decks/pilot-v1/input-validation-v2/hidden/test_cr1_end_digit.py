import pytest

from bincode import is_valid_bin_code


@pytest.mark.parametrize(
    "code,expected",
    [
        ("A1B2", True),
        ("AB12", True),
        ("A1234567", True),
        ("ABCD", False),
        ("AB12C", False),
        ("1BCD", False),
        ("A1", False),
        ("A123456789", False),
    ],
)
def test_bin_code_rules(code, expected):
    assert is_valid_bin_code(code) is expected
