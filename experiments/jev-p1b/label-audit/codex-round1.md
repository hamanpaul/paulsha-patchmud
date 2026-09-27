[
  {"id":"h-code-checkpoint-after-write","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-json-key-order","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"medium","issue":"“Has a source” is undefined when the event contains a source key whose value is null; the code omits that key."},
  {"id":"h-code-outbox-tenant-identity","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-parser-quoted-comma","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-rollout-duplicate-operation","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-migration-dryrun-writes","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-rollout-overwrites-operator","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-outbox-observe-only-mutates","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-ratelimit-retries-500","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-export-next-cursor-missing","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"The clause omits “on the first page,” a condition in the criterion."},
  {"id":"h-code-rollout-restart-unseen","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-snapshot-cursor-unseen","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-outbox-effect-unseen","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-projection-test-unrun","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-cli-exit-unseen","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-ci-run-all-passed","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"The clauses omit the criterion’s requirement that the run finished."},
  {"id":"h-nc-version-declared","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-node-a-admitted","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-issue-closed-by-pr","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-flag-disabled","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-tp35-not-auto-closed","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"The issue being open five days later does not rule out closure followed by reopening. The records provide no complete closure history."},
  {"id":"h-nc-ci-run-one-failed","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"The sole clause omits the specified commit and the requirement that the run finished."},
  {"id":"h-nc-release-rejected-promotion","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"The deployment log is complete only for September 23. A rejected promotion that day does not establish that 2.3.1 was never deployed to production."},
  {"id":"h-nc-retention-too-short","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-owner-mismatch","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"The clause omits “in the service catalog,” which limits the ownership claim."},
  {"id":"h-nc-fleet-host3-missing","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-restore-never-observed","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-slo-half-month","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-migration-tenant-c-missing","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-oncall-ack-unknown","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"The clause omits the criterion’s timing anchor: within five minutes of the page being sent."},
  {"id":"hv-code-dryrun-claimed-fixed","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-code-checkpoint-stale-comment","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-code-outbox-effect-lgtm","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-code-tenant-identity-noise","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-nc-ci-passed-old-failure","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"As in its base item, the clauses omit the requirement that run 4412 finished."},
  {"id":"hv-nc-release-live-label","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"As in its base item, the log covers only September 23. The release note and dashboard label do not resolve whether a production deployment occurred at another time."},
  {"id":"hv-nc-fleet-status-complete","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"hv-nc-retention-policy-doc","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""}
]

Recommend **EXCLUDING**:

- `h-code-json-key-order` — “has a source” is ambiguous for a present key with a null value.
- `h-code-export-next-cursor-missing` — its clause drops the first-page condition.
- `h-nc-ci-run-all-passed` — its clauses omit completion of the run.
- `h-nc-tp35-not-auto-closed` — the evidence does not rule out closure followed by reopening.
- `h-nc-ci-run-one-failed` — its clause omits the commit and completion conditions.
- `h-nc-release-rejected-promotion` — a one-day log cannot establish that deployment never occurred.
- `h-nc-owner-mismatch` — its clause drops the service-catalog scope.
- `h-nc-oncall-ack-unknown` — its clause drops the timing anchor.
- `hv-nc-ci-passed-old-failure` — it inherits the base item’s clause omission.
- `hv-nc-release-live-label` — it inherits the base item’s incomplete time coverage.
