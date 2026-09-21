"""Layered configuration with an intentionally broken reload boundary."""

DEFAULTS = {"enabled": True, "port": 8080, "region": "us-east"}


def _bool(value: str | None, default: bool) -> bool:
    # The bug: an explicit false is treated as if the variable were absent.
    if not value:
        return default
    if value.lower() in {"1", "true", "yes"}:
        return True
    if value.lower() in {"0", "false", "no"}:
        return False
    raise ValueError("ENABLED must be a boolean")


def _port(value: str | None, default: int) -> int:
    if value is None:
        return default
    return int(value)


def parse(file_values: dict[str, object], env: dict[str, str]) -> dict[str, object]:
    result = dict(DEFAULTS)
    result.update(file_values)
    result["enabled"] = _bool(env.get("ENABLED"), bool(result["enabled"]))
    result["port"] = _port(env.get("PORT"), int(result["port"]))
    return result
