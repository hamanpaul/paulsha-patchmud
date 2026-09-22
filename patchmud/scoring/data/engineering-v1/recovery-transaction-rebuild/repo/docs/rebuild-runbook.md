# Rebuild runbook and evidence

The fixture log contains `txn-1`, an aborted `txn-abort`, committed
`txn-2`, and a trailing `txn-open` without a commit marker. A bounded run may
stop after `txn-1`; the next run must start from the saved event cursor and
then process `txn-2`.

The restart drill injects a stop after the projection accepts `txn-1` and
before its outbox publication. At that point the durable cursor is expected to
remain zero. Re-running the same transaction must leave one balance effect and
one outbox event, then advance the cursor to the commit boundary.

Evidence is the combination of transaction markers, projection balances,
outbox ids, and cursor values. A passing balance alone is not proof that the
downstream publication or recovery ordering is correct.
