# Synthetic rollout policy

The control plane records `deployment_status=complete` when it accepts a
manifest and observes a healthy process port on every named node.  That event
does not include the process image digest or a source signature.  The
`production-complete` dashboard label is therefore a desired-state label,
not a fleet-wide provenance attestation.

The expected identity for this packet is source `9d4e11c2a0f8` and image
`sha256:image-921`, as declared by the manifest and CI report.  A node receipt
or runtime identity must be checked separately before claiming integrity.
