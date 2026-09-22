## ADDED Requirements

### Requirement: Independent execution profiles
The CLI SHALL accept one target and an optional base, each with independent harness/model/native effort, and preserve requested, resolved and observed identities without implicit fallback.

#### Scenario: Cross-provider comparison
- **WHEN** agy Gemini 3.8 Flash high is base and Codex Luna max is target
- **THEN** each receives its specified effort and the report distinguishes the profiles and unknown observed identities.

### Requirement: Representative native engineering cases
The suite SHALL contain eighteen substantive cases across six categories and three depths, with 600/1200/1800-second wall budgets, no portable turn cap, fixed public requirements and immutable private evaluation assets.

#### Scenario: Native staged execution
- **WHEN** a native model executes a staged case
- **THEN** its own CLI tools are accepted, initial and successive requirements are delivered as sequential native conversation phases in the same workspace when supported, later stages are not disclosed early, all candidate code runs through IsolationRunner, the final 30 seconds are reserved for independent public tests and evidence capture, timeouts stop descendants, and hidden assets remain unavailable.

### Requirement: Native workspace and evidence boundary
The native runner SHALL expose only the public case repository, protect original fixture tests and pytest/harness configuration, permit `tests/agent/**` and disposable Git metadata for agent work, and collect evidence without executing candidate-controlled Git metadata.

#### Scenario: Public workspace inspection
- **WHEN** a native agent edits, tests, checkpoints or rolls back its workspace
- **THEN** the controller permits native tools inside the isolated workspace, runs final public tests independently, computes the final diff from regular public files, and stores native events, transcript, report, diff, test evidence, usage, exit and timing without exposing hidden answers or the score store.

### Requirement: Native authentication and protocol isolation
The native runner SHALL provide only auth-only provider state in an isolated HOME, keep JEV credentials controller-side, and keep native protocol/tool archives and caches distinct from the old controlled protocol.

#### Scenario: Provider and cache boundary
- **WHEN** a Codex or agy native profile starts
- **THEN** Codex receives mapped `.codex` auth state or agy receives mapped `.gemini` auth state, `TYPESAFE_API_KEY` is absent from the provider CLI environment, and a native `native-engineering-v1` record cannot reuse or overwrite a controlled-protocol baseline.

### Requirement: Dimension-specific typed JEV quality scores
The system SHALL use `judge_protocol_version=dimension-evidence-v1` with pinned `jev-1.13.0` and four independent one-Score requests per case: fulfillment, evidence, constraints and verification. The evidence view SHALL be the public execution whitelist excluding only `final_diff`; the other three views SHALL exclude only `transcript`. Each view SHALL retain the complete public case/source/stages and complete selected execution evidence, including final report, tool events, test results, status and error. The system SHALL compute the case score as the mean of the four validated native scores multiplied by 25, without objective-score overrides or confidence multipliers.

#### Scenario: Valid judge response
- **WHEN** all four independent native Score responses and probability distributions validate
- **THEN** the report preserves each dimension, its evidence references, request hash, bytes, usage and attempts, records the ordered-dimension composite hash with an explicit `request_hash_kind`, and computes the documented 0–100 score.

#### Scenario: Lossless evidence projection
- **WHEN** the controller builds the four dimension requests
- **THEN** it does not filter or truncate by tool name or regex; it preserves all complete tool events, test results, status and error, and removes only the specified `final_diff` or `transcript` field for the corresponding view.

#### Scenario: Single dimension failure
- **WHEN** any one independent dimension request fails, exceeds the bounded evidence context or returns an invalid response
- **THEN** the case judgment is `error` with a null score, successful dimension results remain available for diagnosis, and the system does not average the remaining three dimensions or fabricate zero.

### Requirement: Provider wrapper preservation and raw archive integrity
The system SHALL deduplicate only redundant AGY `native_tool.output` wrapper fields `conversation_id`, `step_index`, `step_type`, `tool_name` and `duration_seconds` in the request view. It SHALL preserve complete `tool_info`, `error`, `state`, unknown fields and all other provider events, while leaving the raw execution archive unchanged.

#### Scenario: AGY lifecycle wrapper
- **WHEN** an AGY native tool event is projected for JEV
- **THEN** only those five redundant output wrapper fields are removed; outer evidence and all tool payload, error, state and unknown content remain available to the judge.

#### Scenario: Service failure
- **WHEN** execution infrastructure or JEV transport/validation fails
- **THEN** the case has an error and no fabricated zero, and incomplete coverage has no overall total.

### Requirement: Evidence-preserving historical comparison
The system SHALL reuse only complete digest-verified baselines matching suite, rubric, judge model and `judge_protocol_version`, execution engine, tool protocol, harness version, model, effort, budget and repetitions.

#### Scenario: Incompatible historical base
- **WHEN** any relevant fingerprint input differs, the native phase/tool policy or judge protocol differs, a record is incomplete or a record is altered
- **THEN** it is not reused and the base is executed anew; native and old controlled protocol/cache records, and `dimension-evidence-v1` and old `full-state-v1` judge protocol/cache records, never mix.

### Requirement: Persistent detailed reports
The system SHALL preserve immutable run histories and atomically render models-score.md under the user's requested configuration directory, including public cases, per-dimension results, request hashes and `request_hash_kind`, provenance, unknown values and comparison dates.

#### Scenario: Concurrent completed runs
- **WHEN** two processes finish concurrently
- **THEN** both immutable histories remain present and the locked Markdown includes both public cases and per-case public execution results without leaking hidden materials.

### Requirement: Traceable rejudgment of archived execution
The system SHALL permit `dimension-evidence-v1` JEV rejudgment of digest-verified public case and execution snapshots without rerunning models or changing the source archive. Derived records SHALL preserve the original execution engine and protocol, disclose the source run and execution date, record the new judge engine and protocol plus the source judge protocol, identify reused execution evidence, and remain ineligible for ordinary baseline caching.

#### Scenario: Recovering a judge failure
- **WHEN** the operator rejudges archived model execution after a judge implementation correction
- **THEN** a new immutable `derived-rejudgment-v1` record preserves the exact source case and execution, retains the original failure in the source archive, and publishes a total only for complete formal coverage; derived comparisons require matching source execution engines and current judge engines/protocols, and cannot silently select a different historical base or compare against ordinary runs.

### Requirement: Validated suite freeze
The delivery SHALL include reference/partial/wrong anchor checks, a six-case two-profile pilot and frozen eighteen-case formal results, keeping pilot and formal evidence distinct.

#### Scenario: Unavailable live prerequisite
- **WHEN** credentials, native provider runtime or namespace capabilities are unavailable
- **THEN** offline verification may finish but the native live gates remain explicitly incomplete and no real score is claimed.

#### Scenario: Dimension protocol calibration
- **WHEN** the judge contract changes from the prior protocol to `dimension-evidence-v1`
- **THEN** the full 18-case × four-dimension anchor/control calibration is rerun before formal totals are accepted, while any already completed native execution is rejudged from its immutable archive without rerunning the model.

### Requirement: Backwards compatibility and installed resources
The new entry point SHALL function from outside the source checkout with packaged cases, while old commands, symlinks and frozen pilot fixtures retain their contracts.

#### Scenario: Installed wheel invocation
- **WHEN** the new CLI is invoked from an unrelated working directory
- **THEN** it resolves its packaged suite and writes the requested report location.
