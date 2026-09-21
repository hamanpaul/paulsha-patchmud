class Rollout:
    def __init__(self, rollout_id, flag, tenants, store):
        self.id = rollout_id
        self.flag = flag
        self.tenants = list(tenants)
        self.before = {tenant: store.snapshot(tenant) for tenant in self.tenants}
        self.applied = []
        self.acknowledged = set()
        self.status = "active"
        self.result = None

    def apply(self, store):
        for tenant in self.tenants:
            store.set(tenant, self.flag, True, actor=f"rollout:{self.id}")
            self.applied.append(tenant)

    def acknowledge(self, tenant):
        self.acknowledged.add(tenant)
