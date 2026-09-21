The manifest and CI report agree on the expected source and image, and the
turn-eight receipt confirms node-a.  The registry later maps the stable tag
and accepts node-b's pull, so the report calls the rollout complete.  It does
not distinguish a pull from a running process or request the missing node-b
runtime attestation.
