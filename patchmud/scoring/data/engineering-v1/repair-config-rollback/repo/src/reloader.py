from config import parse


class ConfigStore:
    """Keep the last known-good configuration and its derived cache."""

    def __init__(self, file_values: dict[str, object], env: dict[str, str]) -> None:
        self.file_values = dict(file_values)
        self.active = parse(self.file_values, env)
        self.cache = self._derive(self.active)

    @staticmethod
    def _derive(config: dict[str, object]) -> dict[str, str]:
        if not isinstance(config["region"], str) or not config["region"]:
            raise ValueError("region must be non-empty")
        return {"endpoint": f"{config['region']}:{config['port']}"}

    def reload(self, file_values: dict[str, object], env: dict[str, str]) -> None:
        # The bug: active and cache are changed before the complete candidate
        # has been validated, so an exception leaves a half-reloaded store.
        candidate = parse(file_values, env)
        self.file_values = dict(file_values)
        self.active.update(candidate)
        self.cache = self._derive(self.active)
