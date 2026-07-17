from bincode import is_valid_bin_code, normalize_bin_code


def test_normalize_strips_and_uppercases():
    assert normalize_bin_code("  a1b2  ") == "A1B2"


def test_too_short_rejected():
    assert is_valid_bin_code("A1") is False


def test_too_long_rejected():
    assert is_valid_bin_code("A123456789") is False


def test_digit_start_rejected():
    assert is_valid_bin_code("1ABC2") is False


def test_valid_code_with_digit_end_accepted():
    assert is_valid_bin_code("A1B2") is True
