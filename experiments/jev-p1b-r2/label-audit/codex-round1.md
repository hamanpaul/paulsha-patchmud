[
  {"id":"h-code-cli-exit-unseen","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-code-export-next-cursor-missing","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-changelog-known-issue","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-flag-disabled","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"h-nc-oncall-ack-unknown","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-cache-summary-version","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-cli-exit-observed","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-cli-exit-zero-observed","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-dispatch-sleeps-after-ok","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-flag-ack-skipped","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-flag-erases-unrelated","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"The clause omits the criterion’s restriction to tenants that did not acknowledge the rollout."},
  {"id":"r2-code-outbox-publish-idempotent","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-parser-unrelated-output","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-projection-guard-partial","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-rebuild-abort-unseen","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-code-rebuild-cursor-after-publish","your_verdict":"insufficient","gold":"satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"The code proves publish() is called before cursor.save(), but does not show that publish() has actually published the event when it returns."},
  {"id":"r2-code-rebuild-max-off-by-one","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"The off-by-one branch would process a third yielded transaction, but the cursor and unseen _transactions() implementation do not establish that three transactions are yielded from this log."},
  {"id":"r2-code-rebuild-replay-unobserved","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-backup-retention","your_verdict":"insufficient","gold":"satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"A 35-day retention setting establishes the configured policy, but does not directly demonstrate that backups are actually retained for 35 days."},
  {"id":"r2-nc-canary-metrics-missing","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-cert-rotation-planned","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-dns-two-of-three","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-license-scan-partial","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-lock-pins-requests","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-node-b-wrong-digest","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"The receipt rules out image-921 during the rollout, but the criterion has no rollout time limit; node-b could have admitted it at another time."},
  {"id":"r2-nc-oncall-primary","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-pr-merged-without-approval","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-region-mismatch","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"The applied configuration specifies eu-west-1, but the criterion concerns where the deployment runs; no runtime placement is observed."},
  {"id":"r2-nc-sbom-older-version","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"r2-nc-tag-points-to-commit","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"rv-code-cli-exit-observed-noise","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"rv-code-flag-erases-claimed","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":false,"ambiguity":"medium","issue":"Like its base item, the clause omits the restriction to tenants that did not acknowledge the rollout."},
  {"id":"rv-code-parser-unrelated-lgtm","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"rv-code-rebuild-stale-comment","your_verdict":"insufficient","gold":"satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"As in the base item, call order alone does not prove that the outbox event has been published before the cursor is saved."},
  {"id":"rv-nc-canary-healthy-label","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"rv-nc-license-scan-claimed","your_verdict":"insufficient","gold":"insufficient","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"rv-nc-pr-approved-offline","your_verdict":"not_satisfied","gold":"not_satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""},
  {"id":"rv-nc-region-architecture-doc","your_verdict":"insufficient","gold":"not_satisfied","agree":false,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"high","issue":"As in the base item, the applied configuration does not directly observe where the deployment runs; the older architecture document is only an assertion."},
  {"id":"rv-nc-tag-other-tag","your_verdict":"satisfied","gold":"satisfied","agree":true,"ac_type_ok":true,"route_ok":true,"clauses_ok":true,"ambiguity":"low","issue":""}
]

Recommend **EXCLUDING**:

- `r2-code-flag-erases-unrelated` — Its clause drops the non-acknowledgment condition.
- `rv-code-flag-erases-claimed` — It inherits the same clause mismatch.
- `r2-code-rebuild-cursor-after-publish` — Calling `publish()` before saving does not establish completed publication.
- `rv-code-rebuild-stale-comment` — It inherits the same publication ambiguity.
- `r2-code-rebuild-max-off-by-one` — The unseen transaction generator and unspecified cursor leave the number yielded uncertain.
- `r2-nc-backup-retention` — The setting does not prove actual backup retention.
- `r2-nc-node-b-wrong-digest` — The evidence excludes image-921 only during the rollout, while the criterion is unbounded.
- `r2-nc-region-mismatch` — Applied configuration does not directly establish runtime placement.
- `rv-nc-region-architecture-doc` — It inherits the same runtime-placement ambiguity.
