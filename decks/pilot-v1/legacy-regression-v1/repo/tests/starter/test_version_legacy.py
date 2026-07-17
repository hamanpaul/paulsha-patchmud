from version import compare_versions


def test_equal_versions():
    assert compare_versions("1.2.3", "1.2.3") == 0


def test_short_version_padded_with_zero():
    assert compare_versions("1.2", "1.2.0") == 0


def test_shorter_version_less_when_extra_nonzero():
    assert compare_versions("1.2", "1.2.1") == -1


def test_basic_major_bump_greater():
    assert compare_versions("2.0", "1.9") == 1
