class ProfileCache:
    def __init__(self):
        self.items = {}

    def get(self, profile_id, loader):
        if profile_id not in self.items:
            self.items[profile_id] = loader(profile_id)
        return dict(self.items[profile_id])

    def invalidate(self, profile_id):
        # Bug: every update flushes every profile and callers can retain stale data.
        self.items.clear()
