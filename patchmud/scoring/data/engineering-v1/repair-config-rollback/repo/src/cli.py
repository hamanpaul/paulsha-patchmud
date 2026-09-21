from .reloader import ConfigStore


def summary(store: ConfigStore) -> str:
    return f"enabled={store.active['enabled']} port={store.active['port']} endpoint={store.cache['endpoint']}"
