from .records import RecordStore
from .journal import Journal


def _v2(row):
    return {"id": row["id"], "name": row["name"].strip(), "version": 2}


def run(store: RecordStore, journal: Journal, batch_size=2, *, dry_run=False, fail_after_write=False):
    cursor = journal.cursor
    batch = store.rows[cursor:cursor + batch_size]
    if not batch:
        return {"cursor": cursor, "planned": 0}
    # Bug: dry-run writes, and checkpoint is recorded before durable writes.
    if dry_run:
        for row in batch:
            store.write_v2(_v2(row))
        return {"cursor": cursor + len(batch), "planned": len(batch)}
    journal.checkpoint(cursor + len(batch))
    for row in batch:
        store.write_v2(_v2(row))
    if fail_after_write:
        raise RuntimeError("worker restarted after write")
    return {"cursor": cursor + len(batch), "planned": len(batch)}
