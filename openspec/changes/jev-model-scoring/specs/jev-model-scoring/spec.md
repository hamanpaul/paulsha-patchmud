## ADDED Requirements

### Requirement: Independent execution profiles
The CLI SHALL accept one target and an optional base, each with independent harness/model/native effort, and preserve requested, resolved and observed identities without implicit fallback.

#### Scenario: Cross-provider comparison
- **WHEN** agy Gemini 3.8 Flash high is base and Codex Luna max is target
- **THEN** each receives its specified effort and the report distinguishes the profiles and unknown observed identities.

### Requirement: Representative controlled cases
The suite SHALL contain eighteen substantive cases across six categories and three depths, with 8/16/24 turns and 600/1200/1800 seconds, fixed public requirements and immutable private evaluation assets.

#### Scenario: Deep case execution
- **WHEN** a model executes a staged case
- **THEN** controlled tools and fixed stage evidence are used, all candidate code runs through IsolationRunner, timeouts stop descendants, and hidden assets remain unavailable.

### Requirement: Typed JEV quality scores
The system SHALL use pinned JEV judgments for all four case dimensions and SHALL compute equal-weight case/category/overall scores without objective-score overrides or confidence multipliers.

#### Scenario: Valid judge response
- **WHEN** all four native scores and probability distributions validate
- **THEN** the report preserves them with evidence references and computes the documented 0–100 score.

#### Scenario: Service failure
- **WHEN** execution infrastructure or JEV transport/validation fails
- **THEN** the case has an error and no fabricated zero, and incomplete coverage has no overall total.

### Requirement: Evidence-preserving historical comparison
The system SHALL reuse only complete digest-verified baselines matching suite, rubric, judge, engine, tool protocol, harness version, model, effort, budget and repetitions.

#### Scenario: Incompatible historical base
- **WHEN** any relevant fingerprint input differs or a record is incomplete or altered
- **THEN** it is not reused and the base is executed anew.

### Requirement: Persistent detailed reports
The system SHALL preserve immutable run histories and atomically render models-score.md under the user's requested configuration directory, including public cases, per-dimension results, provenance, unknown values and comparison dates.

#### Scenario: Concurrent completed runs
- **WHEN** two processes finish concurrently
- **THEN** both immutable histories remain present and the locked Markdown includes both without leaking hidden materials.

### Requirement: Validated suite freeze
The delivery SHALL include reference/partial/wrong anchor checks, a six-case two-profile pilot and frozen eighteen-case formal results, keeping pilot and formal evidence distinct.

#### Scenario: Unavailable live prerequisite
- **WHEN** credentials or runtime capabilities are unavailable
- **THEN** offline verification may finish but the live gates remain explicitly incomplete.

### Requirement: Backwards compatibility and installed resources
The new entry point SHALL function from outside the source checkout with packaged cases, while old commands, symlinks and frozen pilot fixtures retain their contracts.

#### Scenario: Installed wheel invocation
- **WHEN** the new CLI is invoked from an unrelated working directory
- **THEN** it resolves its packaged suite and writes the requested report location.
