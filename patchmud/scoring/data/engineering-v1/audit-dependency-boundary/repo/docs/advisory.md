# TL-2026-04 advisory note

The release dashboard labels `telemetry-lib` as `patched` because the public
CI security report scanned version 3.7.2 in build `build-811`.  The lock file
and SBOM describe that tested build, whose source is `a1170f4c2a11` and whose
image digest is `sha256:image-ci-811`.

The deployment manifest instead names source `b992e1d7f0aa` and a mutable
`release-811` tag.  It records no image digest, and the deploy log explicitly
says registry resolution is not recorded.  The label therefore cannot close
the advisory for the running service without a runtime or registry
attestation.
