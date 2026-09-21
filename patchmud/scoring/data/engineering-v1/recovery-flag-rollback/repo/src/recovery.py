from .flags import FlagStore
from .rollout import Rollout


def rollback(store: FlagStore, rollout: Rollout):
    if rollout.status != "active":
        return dict(rollout.result)

    restored = []
    skipped = []
    for tenant in rollout.applied:
        # Bug: a stale whole-tenant snapshot erases newer unrelated flags and
        # restores tenants that have already acknowledged the rollout.
        store.replace(tenant, rollout.before[tenant], actor=f"rollback:{rollout.id}")
        restored.append(tenant)

    result = {"status": "rolled_back", "restored": restored, "skipped": skipped}
    rollout.status = result["status"]
    rollout.result = dict(result)
    return result
