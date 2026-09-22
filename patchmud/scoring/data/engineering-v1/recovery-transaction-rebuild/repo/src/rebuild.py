from .cursor import RecoveryCursor
from .log import TransactionLog
from .outbox import Outbox
from .projection import AccountProjection


def _transactions(log: TransactionLog, start):
    pending = {}
    index = start
    while index < len(log.events):
        event = log.events[index]
        kind = event.get("kind")
        txid = event.get("txid")
        if kind == "begin":
            pending[txid] = []
        elif kind == "operation" and txid in pending:
            pending[txid].append(dict(event))
        elif kind == "abort":
            pending.pop(txid, None)
        elif kind == "commit" and txid in pending:
            yield txid, pending.pop(txid), index + 1
        index += 1


def rebuild(log: TransactionLog, projection: AccountProjection, outbox: Outbox,
            cursor: RecoveryCursor, *, max_transactions=None,
            fail_after_projection=False):
    processed = 0
    next_cursor = cursor.value
    for txid, operations, end_cursor in _transactions(log, cursor.value):
        projection.apply(txid, operations)
        # Bug: the cursor is durable before the downstream side effect.
        cursor.save(end_cursor)
        if fail_after_projection:
            raise RuntimeError("rebuild stopped before outbox publication")
        outbox.publish(txid, operations)
        next_cursor = end_cursor
        processed += 1
        if max_transactions is not None and processed >= max_transactions:
            break
    return {"cursor": next_cursor, "processed": processed}
