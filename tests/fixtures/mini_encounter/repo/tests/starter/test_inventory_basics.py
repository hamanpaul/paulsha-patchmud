import pytest

from inventory import Inventory


def test_add_and_total():
    inv = Inventory()
    inv.add("apple", 3)
    inv.add("banana", 2)
    assert inv.total() == 5


def test_add_negative_rejected():
    inv = Inventory()
    with pytest.raises(ValueError):
        inv.add("apple", -1)


def test_remove_reduces_total():
    inv = Inventory()
    inv.add("apple", 3)
    inv.remove("apple", 1)
    assert inv.total() == 2
