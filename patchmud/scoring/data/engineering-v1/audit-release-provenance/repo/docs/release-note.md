# v4.12.0 production release note

The registry entry calls this release `verified-production` and associates it
with source commit `8f31c0d9b7e4`, artifact `sha256:artifact-44b7`, and CI run
`ci-742`.  The public CI report records 184 passing tests for that exact
commit and digest.

The deployment record is the authoritative environment observation in this
fixture.  It records a canary apply and a canary health check, followed by a
promotion event whose status is `not-recorded`.  This note is intentionally
not a promotion receipt; the label must not be used to fill that gap.
