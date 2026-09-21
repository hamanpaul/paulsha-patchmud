import pytest

from src.checkpoint import CheckpointJournal, _checksum
from src.recover import recover
from src.records import RecordStore


ROWS = [
    {"id": "evt-101", "payload": "open"},
    {"id": "evt-102", "payload": "charge"},
    {"id": "evt-103", "payload": "close"},
]


def test_interruption_before_write_does_not_advance_checkpoint():
    store, journal = RecordStore(), CheckpointJournal()
    with pytest.raises(RuntimeError, match="before batch write"):
        recover(store, journal, ROWS, fail_before_write=True)
    assert store.snapshot() == []
    assert journal.cursor == 0
    assert recover(store, journal, ROWS)["cursor"] == 2


def test_write_before_checkpoint_replays_without_duplicates_or_reordering():
    store, journal = RecordStore(), CheckpointJournal()
    with pytest.raises(RuntimeError, match="after batch write"):
        recover(store, journal, ROWS, fail_after_write=True)
    assert journal.cursor == 0
    recover(store, journal, ROWS)
    assert [row["id"] for row in store.rows] == ["evt-101", "evt-102"]
    assert journal.cursor == 2
    recover(store, journal, ROWS)
    assert [row["id"] for row in store.rows] == ["evt-101", "evt-102", "evt-103"]


def test_latest_torn_event_does_not_override_older_valid_cursor():
    journal = CheckpointJournal()
    journal.append(1)
    journal.events.append({"kind": "checkpoint", "cursor": 3, "checksum": "torn"})
    assert journal.cursor == 1
    assert journal.events[0]["checksum"] == _checksum(1)
