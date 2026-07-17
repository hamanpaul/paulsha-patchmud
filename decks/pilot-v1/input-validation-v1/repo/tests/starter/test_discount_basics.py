from discount import is_valid_code, normalize_code


def test_normalize_strips_and_uppercases():
    assert normalize_code("  ab12cd  ") == "AB12CD"


def test_digit_start_rejected():
    assert is_valid_code("1AB234") is False


def test_non_alnum_rejected():
    assert is_valid_code("AB-123") is False


def test_valid_code_accepted():
    assert is_valid_code("AB1234") is True
