class Outbox:
    def __init__(self):
        self.events = []

    def publish(self, txid, operations):
        # Bug: a replay publishes a second downstream event.
        self.events.append({"txid": txid, "operations": [dict(item) for item in operations]})
