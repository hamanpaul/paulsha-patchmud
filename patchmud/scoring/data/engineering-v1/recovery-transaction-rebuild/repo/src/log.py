class TransactionLog:
    def __init__(self, events):
        self.events = [dict(event) for event in events]

    def __len__(self):
        return len(self.events)
