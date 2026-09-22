The packet establishes that the tested build declared and scanned
`telemetry-lib==3.7.2`: `src/telemetry_identity.py`, `deps/requirements.lock`,
`deps/sbom.json`, and `ci/security-report.json` agree on the package and on
source commit `a1170f4c2a11`; the SBOM and CI report identify tested image
`sha256:image-ci-811`, and CI run `ci-811` reports 62 tests passed.  It does
not establish that production runs that image.  `deploy/manifest.json` names
source `b992e1d7f0aa`, uses mutable tag `release-811`, and has a null digest;
`logs/deployer.log` records `registry_resolution=not-recorded`.  Thus
`security-fixed` and `patched` are labels, not observed runtime identity, and
the advisory is insufficient-evidence rather than closed.  No code change,
upgrade, rollback, or invented test result is justified.  Request one bounded
attestation containing the running image digest and its SBOM, or an immutable
registry mapping from `release-811` to the tested digest and source.
