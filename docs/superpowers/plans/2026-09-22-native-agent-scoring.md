# Native coding-agent scoring amendment

User decision on 2026-09-22: evaluate native coding agents and allow each CLI's
own tools. This supersedes the completion-only/native-tools-off requirement in
the accepted 2026-09-21 JEV plan. Keep the user-facing grouped CLI, pinned JEV,
18 substantive cases, historical comparison and detailed immutable output.

## Execution contract

- Unit of evaluation: native harness × model × effort. Codex and agy use their
  own tools inside a disposable public workspace. A native tool event is normal
  evidence, not a protocol violation.
- The entire provider CLI runs through IsolationRunner. Add explicit opt-in
  provider-network access, narrowly mapped runtime/auth state, clean HOME and
  stdin prompt support. Existing isolated candidate-test callers keep their
  defaults. Do not mount the real HOME, case source, hidden answers or score
  store. Never pass TYPESAFE_API_KEY to the provider CLI.
- The only shared execution limit is the existing 600/1200/1800-second case
  budget. Native internal turns/tool calls are observed where available, not
  pretended to be equivalent or constrained by the old action-turn limit.
- Staged cases execute sequential user interactions in the same workspace and
  native conversation; missing continuation identity is an execution error. Divide the total wall budget among the
  initial request and successive stages; later stages are not disclosed early.
  Record the phase policy in the comparison fingerprint. Stop on infrastructure
  failure; normal incomplete/budget outcomes remain judgeable by JEV.
- Initial fixture tests remain protected from candidate writes; tests/agent and
  the disposable Git repository remain writable so native checkpoint/rollback
  commands work. The controller computes final diffs from regular public files
  without executing Git against candidate-controlled metadata. Inspect changes against allowed paths as
  JEV evidence, not an additional numeric score. Native tools can inspect,
  modify, test and diagnose within the exposed workspace.
- Save native events, transcript, final report, diff, independent final public
  test evidence, per-call usage, exit and timing. Redact authentication material
  before archival. Preserve partial evidence on timeout/interruption.
- Separate the native protocol/tool cohort from prior controlled archives so
  history cannot mix evaluation contracts. Keep the scripted controlled runner
  for private anchor replay and old tests; it is not the native model runner.

## Implementation seams and ownership

- Root: orchestration, native runner/session state, minimal auth/runtime setup,
  public metadata/fingerprints, docs, OpenSpec, live evidence and PR integration.
- Isolation worker: patchmud/sandbox/isolate.py and a new
  tests/scoring/test_native_isolation.py only. Add explicit network-access,
  mapped read-only/read-write binds, sandbox env and stdin options without
  changing existing callers. Preserve cancellation cleanup and attach collected
  execution evidence to KeyboardInterrupt for native callers.
- Harness worker: first research native argv/events/resume contracts read-only;
  after root handoff own patchmud/scoring/native_cli.py plus its tests. Pure argv
  and event parsing; no direct subprocess execution outside IsolationRunner.
- Case worker: audit protocol-dependent public wording and stage requirements;
  root approves concrete data changes before edits. Preserve case intent and
  private reference/partial/wrong quality ordering. Adapt anchors only when the
  new native path contract makes an existing replay invalid, then recalibrate.

## Gates

1. RED regressions for native CLI entry, native tool acceptance, stage delivery,
   partial results, wall limits, identity/blinding and new isolation options.
2. Independent code review and root namespace probes with synthetic credentials
   and public workspaces; prove hidden assets are absent and provider tools run.
3. Verify real JEV authentication, run private anchor calibration, inspect native
   one-case provider execution before the six-case paired pilot. Record and fix
   actual schema/runtime failures; no fake model results in the real score store.
4. Inspect pilot saturation/floor and judge failures, freeze suite after accepted
   evidence, then perform full 18-case paired native evaluation.
5. Full tests, wheel install outside checkout, policy/preflight and independent
   adversarial review before updating PR #40. Archive OpenSpec only after all
   required live gates pass. No merge or release is authorized.
