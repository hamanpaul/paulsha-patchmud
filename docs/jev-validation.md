# JEV scoring validation record

This file separates implementation checks from live benchmark evidence. No
model score is inferred from fake adapters or scripted reference answers.

## Live gates

- JEV credential: unavailable in the task process and tmux global environment;
  the key value was never printed. Live JEV calibration is **not run**.
- Installed harnesses: Codex 0.155.1 and agy 1.2.7 have no supported switch
  guaranteeing all native tools are disabled. Strict controlled evaluation is
  **blocked before model invocation** pending an explicit execution contract.
- Paired six-case pilot: **not run**.
- Final suite freeze and paired eighteen-case evaluation: **not run**.
- `~/.config/paulsha-patchmud/models-score.md`: no synthetic score has been
  written to the user's real results store.

## Root integration evidence

The grouped CLI and real score store are exercised with injected model and JEV
responses. The tests cover target reruns, compatible base reuse, refresh,
incomplete coverage, pilot exclusion, cancellation, unknown runtime identity,
and unsupported harness preflight. They validate program behavior, not model
quality or provider compatibility.

Separate real namespace probes using synthetic data verified these boundaries:

| Probe | Observation |
|---|---|
| Private package asset mounted with Python toolchain | Readable without mask; absent with package mask |
| Candidate writes `.git/config` | Blocked by read-only mount |
| Candidate writes original fixture test | Blocked by read-only mount |
| Candidate writes `tests/agent/test_new.py` | Allowed |

The isolation changes are opt-in for the scoring runner; the existing runner
API retains its defaults. Timeout and cancellation terminate the child process
group and reap the process before workspace cleanup.

Runtime inspection also confirmed that engine pytest and sandbox pytest differ
on this machine (9.0.3 and 9.1.1 respectively). Both identities are recorded in
new scoring runs instead of assuming the invoking interpreter describes the
test environment.

The initial scoring-focused run passed: `94 passed`; subsequent cancellation
and history regressions passed separately and are included in the final full
test gate. The adapter-timeout regression
was observed failing, then passed after keeping the budget-exhaustion result
judgeable. A real synthetic child process received 950,000 bytes through stdin;
this checks transport without invoking a model.

The final offline anchor matrix replayed all 54 anchors through the runner and
real namespace isolation. All 18 reference executions passed structural checks;
the 12 coding cases' partial and wrong anchors each produced expected test
failures. The six answer-only cases have no executable correctness test, so
their semantic ordering remains unjudged. There were no structural failures.
The private JSON is mode 600 under a mode-700 directory, outside the repository.
Its status is `offline_unjudged`, with `live_judge_status=not_run`.

The exercised suite hash is
`a98a3311416f0397a793c7ff0c1fbfcd58e127cf5a3ab9a2f549b3576fca1c77`.
This records the tested content; it is not a declaration that the live pilot
and suite-freeze gates passed.

A wheel built with `pip wheel --no-deps --no-build-isolation` was installed in
a temporary venv and exercised outside the checkout with `PYTHONPATH` unset.
The new CLI help and 18-case listing passed. All public/private case assets
were packaged, source and installed suite hashes matched, and the wheel
contained no Python/test caches or Git metadata. The existing `patchmud --help`
continues to return its pre-existing unsupported-subcommand exit 2.

Two independent code reviews passed after reproducing and fixing adapter
timeout classification and JEV-interruption evidence preservation. The latter
now archives the current execution with an unscored judgment before stopping.
Compatible historical targets are intentionally reusable as later baselines;
an application regression confirms that the new target still reruns.

A separate adversarial review found no reproducible BLOCKER/MAJOR and passed
30 focused checks covering the runner, judge and store. It also reviewed the
CLI, adapters, workspace materialization and isolation boundary. Neither review
invoked a live model or JEV.

The first full preflight's test gate passed (330.38 seconds), while R-21 rejected
a synthetic personal-style path in a unit test. The test now uses a neutral
workspace path; R-21 passes with no exemption. Final preflight after the
cancellation fix and added history regression passed:

```text
policy: PASS (exit=0)
openspec: PASS (exit=0)
tests: PASS 335.67s (exit=0)
PREFLIGHT PASS
```

The final collection contains 781 tests. This local gate uses the canonical
skill-owned conventions engine at `b281c5da5cda0b7e3c67e148c759d99304893c56`.
It does not satisfy any of the live benchmark gates listed above.
