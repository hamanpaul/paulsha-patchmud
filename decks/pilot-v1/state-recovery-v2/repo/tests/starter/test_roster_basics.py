from roster import diff_removed


def test_no_changes_no_removals():
    assert diff_removed({"d1": "OK", "d2": "OK"}, {"d1": "OK", "d2": "OK"}) == []


def test_real_removal_detected():
    assert diff_removed({"d1": "OK", "d2": "OK"}, {"d1": "OK"}) == ["d2"]


def test_new_device_not_reported_removed():
    assert diff_removed({"d1": "OK"}, {"d1": "OK", "d3": "OK"}) == []
