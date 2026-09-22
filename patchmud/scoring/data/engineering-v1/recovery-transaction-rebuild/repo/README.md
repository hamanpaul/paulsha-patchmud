# Transaction rebuild fixture

The fixture reconstructs two downstream views from a transaction log. A
projection keeps account balances and an outbox keeps one notification per
committed transaction. `src/rebuild.py` owns the recovery cursor and is the
boundary where a crash can happen after one view is changed but before the
other is published.

The public contract treats `commit` as the durability marker. Aborted groups
and a transaction whose commit has not arrived are not applied. Replaying a
completed transaction is expected during recovery, so both sinks must use the
transaction id as an idempotency key and the cursor must move only after both
side effects are complete.
