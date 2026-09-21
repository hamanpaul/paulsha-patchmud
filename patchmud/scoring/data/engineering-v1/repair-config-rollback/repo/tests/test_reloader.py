import pytest

from reloader import ConfigStore


def test_failed_reload_preserves_active_and_cache():
    store = ConfigStore({"region": "west", "port": 9000}, {})
    before = (dict(store.active), dict(store.cache), dict(store.file_values))
    with pytest.raises(ValueError):
        store.reload({"region": "", "port": 9999}, {})
    assert (store.active, store.cache, store.file_values) == before
