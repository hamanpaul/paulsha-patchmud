# Dependency boundary audit fixture

This synthetic packet describes a security advisory for `telemetry-lib`.
`patched` is a human-facing status, while the lock, SBOM, CI image, and
deployment record describe different points in the supply chain.  The audit
must not promote a declared or scanned version into an observed runtime
version without an immutable link.

The fixture is reconstructed from public-style release artifacts.  It contains
no private session, credential, or hidden operational instruction.
