import hashlib


def _checksum(cursor):
    return hashlib.sha256(f"cursor:{cursor}".encode("ascii")).hexdigest()[:12]


class CheckpointJournal:
    def __init__(self, events=None):
        self.events = [dict(event) for event in (events or [])]

    @property
    def cursor(self):
        # Bug: a torn latest event can move recovery past unwritten records.
        checkpoints = [
            event["cursor"]
            for event in self.events
            if event.get("kind") == "checkpoint"
        ]
        return checkpoints[-1] if checkpoints else 0

    def append(self, cursor):
        # Bug: the event has no integrity value, so a partial write is trusted.
        self.events.append({"kind": "checkpoint", "cursor": cursor})
