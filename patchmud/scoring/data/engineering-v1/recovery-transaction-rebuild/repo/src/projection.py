class AccountProjection:
    def __init__(self):
        self.balances = {}
        self.applied = []

    def apply(self, txid, operations):
        # Bug: a replay applies the same transaction's deltas again.
        for operation in operations:
            account = operation["account"]
            self.balances[account] = self.balances.get(account, 0) + operation["delta"]
        self.applied.append(txid)
