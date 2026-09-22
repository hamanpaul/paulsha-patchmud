import pytest

from src.cursor import RecoveryCursor
from src.log import TransactionLog
from src.outbox import Outbox
from src.projection import AccountProjection
from src.rebuild import rebuild


EVENTS = [
    {"kind": "begin", "txid": "txn-1"},
    {"kind": "operation", "txid": "txn-1", "account": "acct-a", "delta": 10},
    {"kind": "operation", "txid": "txn-1", "account": "acct-a", "delta": -3},
    {"kind": "commit", "txid": "txn-1"},
    {"kind": "begin", "txid": "txn-abort"},
    {"kind": "operation", "txid": "txn-abort", "account": "acct-a", "delta": 99},
    {"kind": "abort", "txid": "txn-abort"},
    {"kind": "begin", "txid": "txn-2"},
    {"kind": "operation", "txid": "txn-2", "account": "acct-a", "delta": 5},
    {"kind": "commit", "txid": "txn-2"},
    {"kind": "begin", "txid": "txn-open"},
    {"kind": "operation", "txid": "txn-open", "account": "acct-a", "delta": 1000},
]


def _components():
    return TransactionLog(EVENTS), AccountProjection(), Outbox(), RecoveryCursor()


def test_clean_rebuild_filters_abort_and_incomplete_groups():
    log, projection, outbox, cursor = _components()
    result = rebuild(log, projection, outbox, cursor)
    assert result == {"cursor": 10, "processed": 2}
    assert projection.balances == {"acct-a": 12}
    assert projection.applied == ["txn-1", "txn-2"]
    assert [event["txid"] for event in outbox.events] == ["txn-1", "txn-2"]
    assert cursor.value == 10


def test_bounded_rebuild_resumes_at_next_transaction():
    log, projection, outbox, cursor = _components()
    first = rebuild(log, projection, outbox, cursor, max_transactions=1)
    assert first == {"cursor": 4, "processed": 1}
    second = rebuild(log, projection, outbox, cursor)
    assert second == {"cursor": 10, "processed": 1}
    assert projection.balances["acct-a"] == 12
    assert [event["txid"] for event in outbox.events] == ["txn-1", "txn-2"]


def test_projection_before_outbox_failure_replays_once():
    log, projection, outbox, cursor = _components()
    with pytest.raises(RuntimeError, match="before outbox"):
        rebuild(log, projection, outbox, cursor, max_transactions=1,
                fail_after_projection=True)
    assert cursor.value == 0
    assert projection.balances == {"acct-a": 7}
    assert outbox.events == []

    result = rebuild(log, projection, outbox, cursor, max_transactions=1)
    assert result == {"cursor": 4, "processed": 1}
    assert projection.balances == {"acct-a": 7}
    assert [event["txid"] for event in outbox.events] == ["txn-1"]
