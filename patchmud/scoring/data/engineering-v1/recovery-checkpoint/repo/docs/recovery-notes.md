# Incident evidence

The worker processes records `evt-101`, `evt-102`, and `evt-103` in order.
The first incident stopped the process before its first write while a journal
append had already been attempted. The durable destination was empty, so a
restart must begin at cursor zero.

The second incident wrote `evt-101` and `evt-102` and stopped before the
checkpoint append. The journal still reported cursor zero. Replaying those
records is expected, but the destination must contain one row per id and keep
the original order.

The journal reader also receives a latest event with a mismatched integrity
value. That event is evidence of a torn write, not permission to skip the
records before its cursor. The recovery report should distinguish a durable
checkpoint from a planned cursor and cite the tests that observe both.
