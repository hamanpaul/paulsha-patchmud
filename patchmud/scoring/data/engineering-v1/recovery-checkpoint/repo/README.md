# Checkpoint recovery fixture

This fixture models a worker that consumes an ordered list of records, writes
them to a destination store, and records the next input cursor in a journal.
The incident report in `docs/recovery-notes.md` describes two restart windows:
a process can stop before writing, or after writing but before checkpointing.

The public contract is deliberately small. A recovery run must not skip input
when a checkpoint is ahead of durable data, and replaying the same record must
replace the existing id rather than append a duplicate. Journal entries carry
a short integrity value; a damaged latest entry must not hide an older valid
cursor. The tests under `tests/public/` are immutable benchmark evidence.
