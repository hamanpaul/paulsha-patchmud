"""最小庫存模組：mini_encounter fixture 的待修標的。"""


class Inventory:
    """以品項名稱對應數量的簡單庫存。"""

    def __init__(self):
        self._items = {}

    def add(self, name, qty):
        if qty < 0:
            raise ValueError("qty must be >= 0")
        self._items[name] = self._items.get(name, 0) + qty

    def remove(self, name, qty):
        current = self._items.get(name, 0)
        self._items[name] = current - qty

    def total(self):
        return sum(self._items.values())
