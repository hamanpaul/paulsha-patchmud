class Journal:
    def __init__(self):
        self.events = []

    @property
    def cursor(self):
        checkpoints = [event["cursor"] for event in self.events if event["kind"] == "checkpoint"]
        return checkpoints[-1] if checkpoints else 0

    def checkpoint(self, cursor):
        self.events.append({"kind": "checkpoint", "cursor": cursor})
