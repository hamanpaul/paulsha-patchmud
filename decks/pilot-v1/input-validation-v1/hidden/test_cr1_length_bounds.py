import pytest

from discount import is_valid_code


@pytest.mark.parametrize(
    "code,expected",
    [
        ("AB1234", True),
        ("A12345", True),
        ("ABCDEFGHIJ", True),
        ("AB12", False),
        ("ABCDEFGHIJK", False),
        ("", False),
        ("   ", False),
        ("AB1234567890", False),
    ],
)
def test_length_boundary_cases(code, expected):
    assert is_valid_code(code) is expected
