## Context

Existing adapters accept effort but the legacy CLI fixes high; the eight frozen pilot cases have only 13–22 source lines each. Existing Workspace, model adapter and IsolationRunner seams are retained. The accepted implementation plan is docs/superpowers/plans/2026-09-21-jev-model-scoring.md.

## Goals / Non-Goals

Goals: user-requested CLI, 18 substantive cases, all-JEV quality scoring, honest evidence and errors, compatible historical baselines, persistent detailed reports and live paired evaluation.

Non-goals: native agent execution, autonomous routing, Cortex integration, modifying frozen pilot-v1, retroactively changing old metric meanings, merge or release.

## Decisions

- Add an independent versioned scoring profile with plain JSON artifacts. Existing subcommands remain compatible; new orchestration lives outside legacy cli.py.
- Reuse materialization, workspace enforcement, controlled CLI adapters and IsolationRunner. A scoring-specific action parser accepts multiline final reports; stage messages follow fixed milestones.
- Cases ship as package data. Four case-specific anchored JEV Score questions are equally averaged to a 0–100 case score; three cases per category and six categories have equal weights. Correctness facts enter JEV state, not a second arithmetic score.
- JEV is pinned to jev-1.13.0. Store native distributions, actual returned identity and usage. Bound evidence and requests, reject missing/invalid answers, keep service errors separate from model failures. No invented explanation text or confidence penalties.
- Cache identity includes substantive engine content, suite/private-asset hash, rubric, judge, tools, harness version, requested/resolved model/effort, budget and repetitions. Unknown observed identity remains unknown.
- Immutable digest-checked JSON runs own the data; locked atomic Markdown is a view. Public case snapshots exclude hidden answers. Raw model and judge usage remain separate with unknown amounts null.

## Risks / Trade-offs

- JEV semantic errors and CJK performance: reference/partial/wrong answer anchors and live pilot include concrete counterexamples; confidence is descriptive.
- Similar model scores: report limited discrimination; do not modify cases merely to force separation.
- Missing credentials or provider failure: archive partial evidence, publish no full total, retain live gates as incomplete.
- CLI ambient tools/configuration: isolate the completion runtime and fail closed if native tools cannot be disabled.
- Full evaluation takes up to six hours per profile plus judging: checkpoint evidence, enforce per-call remaining time, terminate descendant processes on timeout.

## Migration Plan

Install wheel with additive paulsha-patchmud entry point and packaged suite. Preserve patchmud legacy commands and old archives. New feature can be rolled back without rewriting its immutable run artifacts.

## Open Questions

The current shell lacks TYPESAFE_API_KEY; the user has been asked for its loading mechanism without revealing credentials. Installed Codex 0.155.1 and agy 1.2.7 cannot guarantee native tools are disabled, so strict controlled execution is blocked before model invocation. Choosing external isolation with native-tool rejection or native-agent evaluation would change the accepted execution contract; that decision remains pending with the user.
