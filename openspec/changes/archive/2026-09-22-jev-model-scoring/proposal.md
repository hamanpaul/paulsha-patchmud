## Why

The frozen pilot deck saturates on one-file repairs and cannot answer the user's concrete Luna max versus Gemini Flash high selection question. The user explicitly requested a JEV-judged engineering benchmark that evaluates native coding agents, preserves reusable historical baselines and writes a persistent detailed model-score report.

## What Changes

- Add grouped base/target CLI profiles and explicit native effort without changing legacy commands.
- Add eighteen engineering cases across six categories and three reasoning depths, plus pilot and frozen-suite provenance.
- Run Codex and agy provider CLIs through `IsolationRunner`, allowing each CLI's own tools and provider network in an isolated public workspace. Staged requirements are sequential native conversation phases, with independent final public tests.
- Add pinned JEV quality scoring over native execution evidence; preserve objective facts and service errors separately.
- Add immutable run archives, compatible baseline reuse and a locked Markdown report in the user's configuration directory.

## Capabilities

### New Capabilities

- `jev-model-scoring`: native profile evaluation, engineering cases, typed JEV scoring, historical comparison and persistent reports.

### Modified Capabilities

Existing ranked metrics and frozen pilot-v1 retain their original controlled interpretation. Native protocol/tool archives and caches are separate from those historical records; the new scoring profile explicitly permits JEV judgments.

## Impact

New scoring modules and packaged fixtures, additive CLI entry point and adapter settings, tests, documentation and canonical agent policy. The native provider CLI receives only narrowly mapped auth-only state (`.codex` for Codex or `.gemini` for agy); `TYPESAFE_API_KEY`, hidden answers and the score store remain controller-side. No Cortex/Hippo/session-health runtime dependency. Related issues #21, #37 and #38 inform boundaries but are not claimed fully resolved by this feature.
