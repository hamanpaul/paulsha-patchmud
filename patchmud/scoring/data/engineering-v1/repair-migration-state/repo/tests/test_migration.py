import pytest

from src.journal import Journal
from src.migrate import run
from src.records import RecordStore


def _store():
    return RecordStore([{"id": "a", "name": " Ada ", "version": 1}, {"id": "b", "name": "Bea", "version": 1}])


def test_write_then_restart_is_idempotent_and_checkpoint_follows_write():
    store, journal = _store(), Journal()
    with pytest.raises(RuntimeError):
        run(store, journal, fail_after_write=True)
    assert journal.cursor == 0
    run(store, journal)
    assert journal.cursor == 2
    assert len({row["id"] for row in store.rows}) == 2
    assert len(store.rows) == 2
    assert [event["kind"] for event in journal.events] == ["checkpoint"]


def test_resume_uses_checkpoint_without_skipping_records():
    store, journal = _store(), Journal()
    run(store, journal, batch_size=1)
    run(store, journal, batch_size=1)
    assert journal.cursor == 2
    assert all(row["version"] == 2 for row in store.rows)


def test_dry_run_does_not_write_or_advance_journal():
    store, journal = _store(), Journal()
    before = store.snapshot()
    result = run(store, journal, dry_run=True)
    assert result["planned"] == 2
    assert store.snapshot() == before
    assert journal.events == []
