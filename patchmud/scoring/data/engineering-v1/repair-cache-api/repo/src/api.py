from .cache import ProfileCache
from .store import ProfileStore


class ProfileAPI:
    def __init__(self):
        self.store = ProfileStore()
        self.cache = ProfileCache()

    def read(self, profile_id, if_none_match=None):
        row = self.cache.get(profile_id, self.store.get)
        etag = f"v{row['version']}"
        if if_none_match == etag:
            return 304, etag, row  # Bug: a 304 response must not carry a body.
        return 200, etag, row

    def update(self, profile_id, name):
        row = self.store.update(profile_id, name)
        self.cache.invalidate(profile_id)
        return row
