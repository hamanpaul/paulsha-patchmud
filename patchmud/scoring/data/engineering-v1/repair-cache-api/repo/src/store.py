class ProfileStore:
    def __init__(self):
        self.rows = {"a": {"name": "Ada", "version": 1}, "b": {"name": "Bea", "version": 1}}

    def get(self, profile_id):
        return dict(self.rows[profile_id])

    def update(self, profile_id, name):
        row = dict(self.rows[profile_id])
        row["name"] = name
        row["version"] += 1
        self.rows[profile_id] = row
        return dict(row)
