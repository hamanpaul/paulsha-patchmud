import pytest

from config import parse


def test_explicit_false_and_zero_are_values_not_missing():
    result = parse({"enabled": True, "port": 8080}, {"ENABLED": "false", "PORT": "0"})
    assert result["enabled"] is False
    assert result["port"] == 0


@pytest.mark.parametrize("value", ["-1", "65536", "eight"])
def test_port_must_be_in_range(value):
    with pytest.raises(ValueError):
        parse({}, {"PORT": value})
