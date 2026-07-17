from roster import diff_removed


def test_unreachable_device_not_reported_removed():
    assert diff_removed({"d1": "OK", "d2": "OK"}, {"d1": "OK", "d2": "UNREACHABLE"}) == []
