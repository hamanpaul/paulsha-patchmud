import pytest

from src.envelope import parse
from src.normalizer import normalize
from src.sink import accept


def test_v1_and_v2_normalize_and_preserve_unknown_fields():
    state = {}
    old = normalize(parse({"id": "e1", "version": 1, "tenant": "a", "payload": {"n": 1}}), state)
    new = normalize(parse({"id": "e1", "version": 2, "tenant": "a", "payload": {"n": 2}, "extra": {"trace": "t"}}), state)
    assert accept(old, "a")["version"] == 2
    assert new["trace"] == "t"
    assert state["e1"]["payload"] == {"n": 2}


def test_old_replay_does_not_erase_newer_state():
    state = {}
    normalize(parse({"id": "e2", "version": 2, "tenant": "a", "payload": {"n": 2}}), state)
    normalize(parse({"id": "e2", "version": 1, "tenant": "a", "payload": {"n": 1}}), state)
    assert state["e2"]["payload"] == {"n": 2}


def test_tenant_and_version_errors_are_input_errors():
    with pytest.raises(ValueError, match="tenant"):
        accept({"id": "e3", "version": 2, "tenant": "b", "payload": {}}, "a")
    with pytest.raises(ValueError, match="version"):
        parse({"id": "e4", "version": "wat", "tenant": "a", "payload": {}})
