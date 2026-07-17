from discount import is_valid_code


def test_too_short_code_rejected():
    assert is_valid_code("AB12") is False


def test_too_long_code_rejected():
    assert is_valid_code("AB1234567890") is False
