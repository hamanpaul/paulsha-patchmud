from version import compare_versions


def test_double_digit_minor_version_ordered_correctly():
    assert compare_versions("1.9", "1.10") == -1
