from bincode import is_valid_bin_code


def test_letter_ending_rejected():
    assert is_valid_bin_code("ABCD") is False


def test_letter_ending_after_digits_rejected():
    assert is_valid_bin_code("AB12C") is False
