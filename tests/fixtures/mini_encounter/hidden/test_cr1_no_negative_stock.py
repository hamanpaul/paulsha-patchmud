import pytest

from inventory import Inventory


def test_remove_missing_raises_keyerror():
    inv = Inventory()
    with pytest.raises(KeyError):
        inv.remove("ghost", 1)


def test_remove_exceeding_stock_raises_valueerror():
    inv = Inventory()
    inv.add("apple", 2)
    with pytest.raises(ValueError):
        inv.remove("apple", 3)


def test_total_never_negative_after_removals():
    inv = Inventory()
    inv.add("apple", 5)
    inv.remove("apple", 3)
    assert inv.total() == 2
