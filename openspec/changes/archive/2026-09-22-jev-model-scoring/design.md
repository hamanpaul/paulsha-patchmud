## Context

Existing adapters accept effort but the legacy CLI fixes high; the eight frozen pilot cases have only 13–22 source lines each. Existing Workspace, model adapter and IsolationRunner seams are retained. The accepted native amendment is `docs/superpowers/plans/2026-09-22-native-agent-scoring.md`: Codex and agy are evaluated as native coding agents with their own CLI tools inside an isolated public workspace. The older completion-only/native-tools-off plan is superseded for this profile.

## Goals / Non-Goals

Goals: user-requested CLI, 18 substantive cases, native Codex/agy evaluation across model and effort, all-JEV quality scoring, lossless dimension-specific evidence, traceable rejudgment of archived executions, honest errors, compatible historical baselines, persistent detailed reports and live paired evaluation.

Non-goals: autonomous routing, Cortex integration, modifying frozen pilot-v1, retroactively changing old metric meanings, merge or release. The old ranked and controlled pilot flows remain available for their existing contracts.

## Decisions

- Add an independent versioned native scoring profile with plain JSON artifacts. Existing subcommands remain compatible; grouped scoring orchestration uses the native runner while the old controlled runner remains available for legacy and private anchor replay.
- Run the complete provider CLI through `IsolationRunner` with explicit provider-network access, a clean HOME, narrowly mapped auth-only state and stdin prompts. Codex receives `.codex` auth state and agy receives `.gemini` auth state; `TYPESAFE_API_KEY` stays in the controller. Native tools are valid evidence.
- Execute staged requirements as sequential user interactions in the same workspace and native conversation when supported. Use only the 600/1200/1800-second wall budget, with no portable turn cap and a final 30-second reserve for independent public tests and evidence capture. Later stages are not disclosed early.
- Protect original fixture tests and pytest/harness configuration. Permit `tests/agent/**` and disposable Git metadata for agent-created tests and checkpoint/rollback work. The controller computes diffs from regular public files and never executes candidate-controlled Git metadata.
- Cases ship as package data. Each case sends four independent one-Score requests under `judge_protocol_version=dimension-evidence-v1`: fulfillment, evidence, constraints and verification. The evidence view excludes only `final_diff`; the other three views exclude only `transcript`. Every view retains the complete public case/source/stages and the selected public execution whitelist, including final report, all complete tool events, test results, status and error. No tool-name or regex filtering and no output truncation are allowed. A single dimension failure makes the whole case error/null; successful dimensions are retained for diagnosis but are not averaged.
- JEV is pinned to `jev-1.13.0`. Store native distributions, actual returned identity and usage, `per_dimension_results`, every HTTP request hash/bytes/usage/attempt, and a composite ordered-dimension manifest hash with an explicit `request_hash_kind`; the composite is not one HTTP-body hash. Bound evidence and requests, reject missing/invalid answers, keep service errors separate from model failures. No invented explanation text or confidence penalties.
- AGY lifecycle wrapper deduplication is limited to redundant `conversation_id`, `step_index`, `step_type`, `tool_name` and `duration_seconds` fields inside `native_tool.output`. Preserve full `tool_info`, `error`, `state`, unknown fields and all other provider events; raw execution archives remain unchanged. Repeated text may use integrity-checked lossless references only in the request view.
- Cache identity includes substantive engine content, suite/private-asset hash, rubric, judge model and protocol, native tool cohort, phase policy, harness version, requested/resolved model/effort, budget and repetitions. Unknown observed identity remains unknown. Native `native-engineering-v1` archives and `dimension-evidence-v1` judge results cannot be reused as old controlled or old `full-state-v1` judge-protocol archives, or vice versa.
- Immutable digest-checked JSON runs own the data; locked atomic Markdown is a view. `models-score.md` includes public cases and per-case public execution results. Public case snapshots exclude hidden answers. Raw model and judge usage remain separate with unknown amounts null. Save native events, transcript, final report, diff, independent public-test evidence, exit and timing, redacting authentication material and retaining partial evidence.
- A rejudgment reuses the digest-verified source case and execution with its original execution engine, while recording the new judge engine, `dimension-evidence-v1` protocol and source judge protocol. It creates `derived-rejudgment-v1`, preserves the source archive and is never eligible for ordinary base caching or mixed comparison with ordinary runs.

## Risks / Trade-offs

- JEV semantic errors and CJK performance: reference/partial/wrong answer anchors and live pilot include concrete counterexamples; confidence is descriptive.
- Four independent requests increase cost and partial failure surface: preserve all per-dimension attempts and fail the whole case closed when one request fails; never average the remaining dimensions.
- A large complete evidence view can exceed provider context: apply only integrity-checked lossless repeated-text references for a bounded retry, retain the original request and raw archive, and treat any unresolved context failure as an error.
- Similar model scores: report limited discrimination; do not modify cases merely to force separation.
- Missing credentials or provider failure: archive partial evidence, publish no full total, retain live gates as incomplete.
- Native provider tools and network increase the surface for nondeterminism: keep the disposable public workspace, mapped auth-only HOME and explicit namespace boundary, and record events and timing. This is a native-agent contract, not a claim that native tools are unavailable.
- Full evaluation takes up to six hours per profile plus judging: checkpoint evidence, enforce per-call remaining time, terminate descendant processes on timeout.

## Migration Plan

Install wheel with additive paulsha-patchmud entry point and packaged suite. Preserve patchmud legacy commands and old archives. After the approved judge contract is available, rejudge complete native archives in place through derived records without rerunning model conversations or rewriting source artifacts. New feature can be rolled back without rewriting its immutable run artifacts.

## Open Questions

The final native runtime gates are documented separately. Full `engineering-v1` recalibration for `dimension-evidence-v1` passed all 72 anchor/control composites and 288 independent dimensions. Complete formal native executions are rejudged from their immutable archives without rerunning models; final results and provenance are documented in `docs/jev-validation.md`. Offline tests, fake adapters and source inspection do not substitute for these live results. The old controlled runner remains the path for private anchor replay and legacy tests.
