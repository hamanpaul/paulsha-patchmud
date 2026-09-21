class FlagStore:
    def __init__(self):
        self.values = {
            "acme": {"search_v2": False, "billing_v2": False},
            "globex": {"search_v2": False, "billing_v2": False},
            "initech": {"search_v2": False, "billing_v2": False},
        }
        self.revision = 1
        self.audit = []

    def read(self, tenant, flag):
        return self.values[tenant][flag]

    def snapshot(self, tenant):
        return dict(self.values[tenant])

    def set(self, tenant, flag, value, *, actor):
        self.revision += 1
        self.values[tenant][flag] = value
        self.audit.append({
            "revision": self.revision,
            "tenant": tenant,
            "flag": flag,
            "value": value,
            "actor": actor,
        })
        return self.revision

    def replace(self, tenant, snapshot, *, actor):
        self.revision += 1
        self.values[tenant] = dict(snapshot)
        self.audit.append({
            "revision": self.revision,
            "tenant": tenant,
            "flag": "*",
            "value": dict(snapshot),
            "actor": actor,
        })
        return self.revision
