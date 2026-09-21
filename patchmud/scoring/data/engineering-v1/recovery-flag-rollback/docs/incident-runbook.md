# Evolving incident evidence

At rollout start, `search_v2` is false for `acme` and `globex`; rollout-42
captures those values and enables the flag. `initech` is outside the cohort.

Before rollback, the control plane publishes `billing_v2=true` for both
cohort tenants at a newer store revision. This publication is unrelated to
rollout-42 and must survive recovery. The incident commander then confirms
that `acme` acknowledged `search_v2`, while `globex` did not.

The final report must identify `globex` as restored, `acme` as intentionally
retained, and `initech` as untouched. It should cite the revision and audit
delta, explain why a whole-snapshot restore is unsafe, and show that invoking
rollback again does not add another audit event.
