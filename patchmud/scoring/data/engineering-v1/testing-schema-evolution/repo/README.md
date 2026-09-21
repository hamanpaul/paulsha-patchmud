# Evolving envelope test fixture

The envelope parser accepts v1 and v2 during a compatibility window.  The
normalizer and sink expose version, tenant, replay ordering, unknown fields,
and input-error boundaries for deterministic regression tests.
