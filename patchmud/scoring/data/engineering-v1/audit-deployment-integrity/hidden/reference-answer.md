At the initial stage, the source code, manifest, and CI report agree on
source `9d4e11c2a0f8`, expected image `sha256:image-921`, and run `ci-1842`,
but the control-plane log only records a mutable `stable` tag, accepted
desired state, and a port health check.  `deployment_status=complete` and
`production-complete` are labels, not process identity evidence.  After
turn 8, `node-a-921` confirms that node-a admitted the expected digest with a
valid signature, but it says nothing about node-b.  After turn 16,
`reg-921` shows the tag resolved to the expected digest during the rollout and
`pull-b-771` was accepted for node-b.  That improves the selection evidence
but still lacks a post-start node-b digest and signed source binding.  The
fleet-wide integrity claim is therefore insufficient-evidence.  No code
change, rerun, or invented pass is justified.  Request one bounded node-b
runtime attestation containing process start time, image digest, source commit,
and signature; then compare it with the manifest and CI identities.
