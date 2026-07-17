from snapshot import diff_removed


def test_transient_scan_failure_not_reported_as_removed():
    prev = {"a.txt": "h1", "b.txt": "h2"}
    cur = {"a.txt": "h1"}  # b.txt 這次掃描失敗，未出現在 current
    assert diff_removed(prev, cur, scan_errors={"b.txt"}) == []
