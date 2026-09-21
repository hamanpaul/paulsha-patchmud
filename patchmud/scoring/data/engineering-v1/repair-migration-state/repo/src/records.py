class RecordStore:
    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]

    def write_v2(self, row):
        # Bug: a replay appends another copy instead of replacing by id.
        self.rows.append({"id": row["id"], "name": row["name"], "version": 2})

    def snapshot(self):
        return [dict(row) for row in self.rows]
