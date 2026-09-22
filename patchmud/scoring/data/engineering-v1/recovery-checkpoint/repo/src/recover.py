from .checkpoint import CheckpointJournal
from .records import RecordStore


def recover(store: RecordStore, journal: CheckpointJournal, rows, batch_size=2,
            *, fail_before_write=False, fail_after_write=False):
    """Process one ordered batch, returning the planned next cursor."""
    cursor = journal.cursor
    batch = list(rows[cursor:cursor + batch_size])
    if not batch:
        return {"cursor": cursor, "processed": 0}

    # Bug: the cursor is advanced before the batch is durable.
    journal.append(cursor + len(batch))
    if fail_before_write:
        raise RuntimeError("worker stopped before batch write")
    for row in batch:
        store.write(row)
    if fail_after_write:
        raise RuntimeError("worker stopped after batch write")
    return {"cursor": cursor + len(batch), "processed": len(batch)}
