[
  {
    "id": "eng-cache-304-bodyless",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cache-invalidate-updated-key",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-ratelimit-retry-after-bounded",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-migration-dry-run-no-mutation",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-fakeclock-success-no-sleep",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-parser-duplicate-header",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "tp35-operator-edits-persist",
    "your_verdict": "not_satisfied",
    "gold": "satisfied",
    "agree": false,
    "ambiguity": "high",
    "issue": "The criterion does not restrict where the operator may edit. The implementation preserves a file only while it still starts with the staging marker; editing, removing, or preceding that marker causes the same plugin to overwrite the operator's changes."
  },
  {
    "id": "hippo-backup-same-second",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cache-summary-version",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-config-port-range",
    "your_verdict": "insufficient",
    "gold": "not_satisfied",
    "agree": false,
    "ambiguity": "high",
    "issue": "The evidence proves that parse() accepts 70000, but it does not show the component that installs the active configuration. A later validator could still reject the candidate before active state changes, so the rationale's claim that 70000 is written to active state is unsupported."
  },
  {
    "id": "eng-ratelimit-malformed-header",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-ratelimit-max-attempts",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-parser-short-row",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "tp35-artifacts-record-testbed",
    "your_verdict": "insufficient",
    "gold": "not_satisfied",
    "agree": false,
    "ambiguity": "high",
    "issue": "The search is not exhaustive and the reporter can emit an arbitrary value stored under meta['testbed']; that value could contain the path, staging state, and fields without using any searched identifier. No actual artifact or complete metadata-construction path is shown."
  },
  {
    "id": "diag-retry-duplicate-delivery",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "high",
    "issue": "The meaning and provenance of delivered=1 are undefined. If it counts only confirmed 202 responses, a timed-out first attempt could still have produced an unobserved downstream delivery. Recipient-side delivery evidence would be needed to remove this ambiguity."
  },
  {
    "id": "dut-reboot-trace-records-wait",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "diag-incident-root-cause",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cache-invalidate-unseen",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cursor-signed-claim-only",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "tp46-selection-coverage-irrelevant",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "audit-dependency-running-version",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "audit-deployment-both-nodes",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cli-json-order-unseen",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "hippo-push-shadow-recording",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cli-empty-source-stale-comment",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cursor-cross-tenant-rejected",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "tp35-plugin-switch-isolation",
    "your_verdict": "insufficient",
    "gold": "satisfied",
    "agree": false,
    "ambiguity": "high",
    "issue": "The code and test prove the staging helper's behavior, but not that an actual plugin execution invokes it before loading configs/testbed.yaml. The statement that every plugin command calls it is prose, not an executable call-site or integration result."
  },
  {
    "id": "eng-config-failed-reload-keeps-last-good",
    "your_verdict": "satisfied",
    "gold": "satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-cursor-resume-broken",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-checkpoint-comment-only",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "dut-boot-ready-probe-bootloader",
    "your_verdict": "not_satisfied",
    "gold": "not_satisfied",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-schema-replay-test-deleted",
    "your_verdict": "insufficient",
    "gold": "not_satisfied",
    "agree": false,
    "ambiguity": "high",
    "issue": "The evidence shows that one explicit replay test was deleted, but the criterion says no test remains. The complete body of the remaining test_v1_and_v2_normalize_and_preserve_unknown_fields test is omitted and could still exercise v2 followed by v1."
  },
  {
    "id": "eng-migration-checkpoint-unseen",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "hippo-timer-attestation",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "audit-release-promoted-production",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  },
  {
    "id": "eng-config-false-ci-truncated",
    "your_verdict": "insufficient",
    "gold": "insufficient",
    "agree": true,
    "ambiguity": "low",
    "issue": ""
  }
]

Recommended fixes:

- `tp35-operator-edits-persist`: Narrow the criterion to edits below an unchanged staging marker. Otherwise change the gold to `not_satisfied`.
- `eng-config-port-range`: Add a reload test showing that `PORT=70000` completes and changes `active["port"]` to 70000. Without that, change the gold to `insufficient`.
- `tp35-artifacts-record-testbed`: Add an actual generated run artifact showing the missing path, staging state, and key fields, or provide the complete construction of `meta["testbed"]`. Otherwise use `insufficient`.
- `diag-retry-duplicate-delivery`: State that `delivered=1` is a recipient-side durable delivery count, or add recipient-side records for r17 and r18.
- `tp35-plugin-switch-isolation`: Add the real command call-site plus an integration test that switches plugins and verifies what the run loads. Otherwise use `insufficient`.
- `eng-schema-replay-test-deleted`: Include the complete bodies of both remaining tests, or a sufficiently exhaustive test-source search proving neither performs v2-then-v1 replay. Otherwise use `insufficient`.
