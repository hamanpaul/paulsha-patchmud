class RecordStore:
    """Tiny ordered destination used by the recovery worker."""

    def __init__(self):
        self.rows = []

    def write(self, row):
        # Bug: replaying a batch appends a second copy of an already durable id.
        self.rows.append(dict(row))

    def snapshot(self):
        return [dict(row) for row in self.rows]
