---
status: accepted
work_item: jev-model-scoring
task_type: judge-contract-amendment
---

# Dimension-evidence JEV judging amendment

User decision on 2026-09-22: submit complete, dimension-appropriate public
evidence to JEV and preserve the complete source archives. The previous
single full-state judge contract is replaced for the native engineering score
by `judge_protocol_version=dimension-evidence-v1`; the pinned judge remains
`jev-1.13.0`.

## Contract

Each case sends four independent one-Score requests, one for each of:

- `fulfillment`
- `evidence`
- `constraints`
- `verification`

Every request retains the complete public case snapshot, including source
files, requirements and staged messages. It also receives the same selected
public execution whitelist. The `evidence` view excludes only `final_diff`.
The fulfillment, constraints and verification views exclude only
`transcript`. All four views retain `final_report`, every complete tool event,
every test result, status and error. No tool-name or regex filtering and no
output truncation are permitted.

The sole provider-specific projection is bounded AGY wrapper deduplication. In
`native_tool.output`, remove only redundant `conversation_id`, `step_index`,
`step_type`, `tool_name` and `duration_seconds`. Preserve complete `tool_info`,
`error`, `state`, unknown fields and all other events. The raw execution
archive is immutable and unchanged; repeated text may use only integrity-
checked, lossless request references.

## Scoring and failure semantics

JEV's four native scores are preserved independently. The case score is
`25 × mean(four native scores)`. Confidence, probabilities, test outcomes and
other objective facts remain evidence and do not change that arithmetic. A
single dimension request failure, invalid response or unresolved context limit
makes the whole case `error` with a null score. Successful dimensions remain
available for diagnosis, but the system never averages the other three or
converts a failure to zero.

Each case result preserves `per_dimension_results`. For every independent HTTP
request, retain request hash, request bytes, usage and all attempts, including
retry errors and original/final request hashes where a lossless context retry
was used. The composite `request_hash` is a digest of the ordered dimension
manifest and carries an explicit `request_hash_kind`; it is not the hash of a
single HTTP body.

## Rejudgment and identity

Rejudgment validates the immutable source archive, reuses the original public
case and execution with the original execution engine/protocol, and calls the
new judge engine/protocol. It creates an immutable `derived-rejudgment-v1`
record containing source run/date/content digest, source execution engine,
source judge protocol, current judge engine/protocol and reused-execution
provenance. The source record and any original judge failure remain unchanged.

Derived records are not ordinary base candidates. Native execution protocol,
judge protocol, engine digests, evidence policy, suite/rubric and profile
identity must match before a comparison is allowed. Native and controlled
archives, and old `full-state-v1` and `dimension-evidence-v1` judge caches,
never mix.

## Validation sequence

1. Preserve all existing native execution archives and the original judge
   failures.
2. Rebuild and inspect the four evidence views, including AGY wrapper
   projection, unknown event preservation and no truncation.
3. Run the full 18-case × four-variant reference/partial/wrong and
   injection-control calibration for `dimension-evidence-v1`. The completed
   calibration scored all 72 composites / 288 dimensions; no formal total is
   inferred from the previous protocol.
4. Rejudge completed native executions from their archives without rerunning
   model conversations. Each formal native run remains an execution source; its rejudgment acquires
   a total only when all four dimensions of every case complete under the new
   contract.
5. Inspect per-dimension HTTP hashes, bytes, usage, attempts, composite hash
   kind, fail-closed nulls, derived provenance and cache rejection before any
   formal comparison or delivery decision.

The old controlled runner remains available for private anchor replay and old
tests. This amendment changes judge evidence and rejudgment identity only; it
does not alter the frozen suite manifest or rerun model conversations.
