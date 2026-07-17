from snapshot import diff_removed


def test_no_changes_no_removals():
    prev = {"a.txt": "h1", "b.txt": "h2"}
    cur = {"a.txt": "h1", "b.txt": "h2"}
    assert diff_removed(prev, cur) == []


def test_real_deletion_detected():
    prev = {"a.txt": "h1", "b.txt": "h2"}
    cur = {"a.txt": "h1"}
    assert diff_removed(prev, cur) == ["b.txt"]


def test_new_file_not_reported_as_removed():
    prev = {"a.txt": "h1"}
    cur = {"a.txt": "h1", "c.txt": "h3"}
    assert diff_removed(prev, cur) == []
