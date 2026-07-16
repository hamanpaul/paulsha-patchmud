import pytest

from inventory import Inventory


def test_remove_missing_item_raises_keyerror():
    inv = Inventory()
    with pytest.raises(KeyError):
        inv.remove("ghost", 1)
