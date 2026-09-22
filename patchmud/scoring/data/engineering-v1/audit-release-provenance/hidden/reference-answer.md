The artifact provenance chain is internally consistent: `src/release_identity.py`,
`release/source.json`, and `ci/test-report.json` all identify source commit
`8f31c0d9b7e4`, and the source, CI, and deployment records all carry
`sha256:artifact-44b7`; CI run `ci-742` reports 184 tests passed.  The
contradiction is the human-facing `verified-production` label in
`release/label.json` and the release note.  `deploy/events.log` observes only
a canary apply and health check, and its promotion event is
`promotion_status=not-recorded`.  Therefore the fixture proves a tested
canary artifact, not a production promotion.  No repository change or
invented test result is justified.  The bounded follow-up is to obtain the
promotion receipt naming this digest and environment; until then the label
should remain an unverified publication claim.
