# Feature-flag rollback fixture

The fixture contains a rollout record, a per-tenant flag store, and a recovery
helper. Rollout `rollout-42` enables `search_v2` for `acme` and `globex`; the
store also contains an unrelated `billing_v2` flag. The incident runbook
records a newer control-plane publication and a later acknowledgement policy.

Rollback owns one flag and one cohort. A captured tenant snapshot is useful as
evidence of the previous value, but replacing that whole snapshot can erase a
newer unrelated flag. Recovery also has to distinguish an acknowledged tenant
from an unacknowledged one and make its result safe to invoke twice.
