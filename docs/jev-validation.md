# JEV scoring validation record

The native paired evaluation and the `dimension-evidence-v1` rejudgment are
complete. Both profiles have 18/18 valid case judgments. Full public cases,
execution evidence and raw judgments are persisted in
`~/.config/paulsha-patchmud/models-score.md` and immutable `runs/<run-id>/run.json`
archives. [Machine-readable results](engineering-v1-results.json) record the
scores, source digests, per-case outcomes and comparison.

## Formal results

| Profile | JEV score / 100 | Coverage | Public test checks | Execution wall time, excluding JEV |
| --- | ---: | ---: | --- | ---: |
| agy / gemini-3.8-flash / high | 91.1771 | 18/18 | 12 passed | 3552.551 s |
| codex / gpt-5.6-luna / max | 91.4167 | 18/18 | 11 passed, 1 failed | 4425.414 s |

Luna's observed difference is +0.2396 points. Each
profile ran each case once; this small difference does not establish statistical
superiority. The fixtures cover six engineering categories and three depths,
not the full range of real repositories or firmware work. Different harnesses
have different native usage semantics. No verified pricing snapshot was
available, so cost remains unknown rather than zero.

The failed Luna public-test check is `repair-migration-state`: a dry-run
journal contained `write` before `checkpoint`, while the expected journal
contained only `checkpoint`. This is a candidate outcome, not an infrastructure
error. All 36 native processes completed normally. Process completion and
passing all task requirements are distinct facts; public tests are JEV evidence
and do not override the native quality score.

## Execution and judging contracts

The [original freeze](engineering-v1-freeze.json) remains unchanged. It pins
`engineering-v1` 1.1.0, suite hash
`77c5913af8725ae584c9e8c0a73220b77eb1e107293c4d51578dceea24a4bca4`,
and source execution engine
`0ab4c023b6c9c4c769c154fef8aa43fb713f15459766df5974cf53fcaaaeb22b`.
The [judge amendment](engineering-v1-judge-amendment.json) freezes the changed
evidence contract without changing any case, rubric or native execution.

Codex and agy execute their complete CLI through `IsolationRunner`, with their
own tools and provider/native-tool network access. An isolated home carries
only necessary auth state: Codex `.codex`, agy `.gemini`. Real HOME, user
conversations/settings/skills, private answers, score store and the JEV key are
not mounted into the candidate environment. Staged requirements continue in
the same native conversation. Cases have 600/1200/1800 second wall budgets,
with the final 30 seconds reserved for independent public tests and evidence
capture; native tool turns are not treated as comparable limits.

Fixture tests/configuration are protected. `tests/agent/**` is writable only
when the case permits it, and disposable Git supports checkpoints and rollback.
The controller computes diffs from regular files without executing candidate
Git metadata. Independent public tests use trusted fixture tests/configuration
plus candidate source, with network disabled and `tests/agent` excluded.
A real namespace adversarial check confirmed that a candidate pytest hook could
not suppress an original failing test.

Pinned `jev-1.13.0` judges four independent Score requests per case. The
`evidence` view omits only final diff; fulfillment, constraints and verification
omit only transcript. All four retain the complete public case/source/stages,
final report, complete tool events, public tests and execution status/errors.
AGY redundant wrapper identity/timing fields are omitted from the judgment
view; full tool payloads and all raw archives remain preserved. Exact shared
text and bounded fragment references retain complete evidence without output
truncation. A dimension failure yields a null case score; confidence does not
multiply the mean of the four native scores.

## Context failure and immutable rejudgment

The original installed paired command exited 1 because the original base had
two JEV `max_tokens_exceeded` errors: `testing-schema-evolution` and
`recovery-transaction-rebuild`. All native executions completed; the old base
remains 16/18 judged with a null total. The old target completed 18/18 under the
old judge contract. Those original judgments are retained and are not mixed
with the new protocol's scores.

Lossless deduplication alone did not fit the two long records. After the user
selected four dimension-specific evidence views, the exact two records passed
8/8 real Score requests. The final rejudgments below reused the original case,
execution and repetition verbatim; no model conversation was rerun.

| Role | Immutable derived run | Original source run |
| --- | --- | --- |
| base | `20260922T032512-derived-20260922T011608Z-base-3ff7a8b332cd-cf7f3bc25680` | `20260922T011608Z-base-3ff7a8b332cd` |
| target | `20260922T033017-derived-20260922T021546Z-target-79fd15f788fa-3bcf3d9cbf57` | `20260922T021546Z-target-79fd15f788fa` |

Both installed rejudgment commands exited 0. The base used 79 HTTP attempts
for 72 dimension scores, including seven context-error attempts followed by
lossless fallback; the target used 72 attempts for 72 scores. All 144 dimensions
scored. Root independently verified all source digests, exact case/execution
preservation for 36 rows, and reconstructed every dimension's original/final
HTTP body hash and byte count from the archived evidence. The target comparison
links the original requested base's derived run. Derived records explicitly
retain the source execution engine and separate current judge engine/protocol;
they are excluded from ordinary baseline caching.

## Calibration and pilot

Private `calibration-dimension-evidence-v1.json` contains one complete live
calibration: 18 cases × reference/partial/wrong/injection variants = 72 composite
judgments and 288 independent dimension requests. All 288 HTTP attempts scored,
without retries, with 288 unique body hashes. All 18 case orderings,
false-completion controls, injection controls and reference objective gates
passed; no structural failure remained. Calibration uses scripted anchor replay
through the controlled `IsolationRunner`, explicitly `native_cli=false`; these
are judge checks, not model benchmark scores. The private artifact digest is
`5e1f2edc873895c56622981c384abe8cd40e0b6a8f9a76978abf5f9aa24b555a`.

The original paired pilot ran six selected native cases per profile, with
12/12 scored under the previous full-state judge protocol and all eight coding
public-test checks passing. Pilot scores ranged 89.5625–95.0 with limited
separation; no case or rubric was tuned to force a gap. All 20 native phases
replayed correctly through the final parser. The original 72 full-state anchor
judgments and targeted 24 staged judgments also passed. Those earlier results
remain separate in the original freeze; the new 288-request calibration covers
the new evidence contract.

## Installation, tests and review

Final engine digest: `f0411bf9af0cbd18ce603c0984d8fd0c3ececb33c451b577996ab731d671e3a1`.
Final wheel SHA-256: `6798a4c988c53cee9c2ddbe5369b525e58ae46001985b8686dab5c6b7c044fd6`.
A fresh wheel was installed into a temporary venv and used from `/tmp`.
`paulsha-patchmud --help`, `--list-cases`, and the rejudgment module help returned
0; all 18 installed cases matched the frozen suite, and installed/source engine
digests matched. Both final real rejudgments used that installed wheel.
The observed native CLIs were Codex `0.155.1` and agy `1.2.7`.

Final focused judge/views/CLI/store/aggregation/rejudgment/calibration checks:
97 passed. Independent new-contract review passed 16 focused checks with no
remaining BLOCKER/MAJOR. Earlier native parser/runner/isolation reviews and the
real namespace adversarial verification also passed. Canonical preflight ended:

```text
engine: PASS (hamanpaul/paulsha-conventions@b281c5da5cda0b7e3c67e148c759d99304893c56)
policy: PASS 0.46s (exit=0)
openspec: PASS 0.85s (exit=0)
tests: PASS 297.48s (exit=0)
PREFLIGHT PASS
```

The final post-staging policy check also used CI's exact engine
`9e7fabbf0b5eea9ad933fa6798764b723934a0b7` with final PR metadata and
public visibility: 25 passed, zero failures, one R-22 advisory for output/doc
references. Synthetic isolation-test home paths use `/sandbox-home`; all nine
affected isolation tests passed. This test-only correction does not change the
engine digest, installed wheel or evaluated evidence.

Earlier immutable startup, transport, context and archive-recovery failures
remain in the local history. No hidden answers, private calibration artifacts,
or credentials are included in the public result summary. The detailed local
Markdown includes raw histories and is approximately 115 MB. This delivery
does not merge, release or replace the global installed CLI.
