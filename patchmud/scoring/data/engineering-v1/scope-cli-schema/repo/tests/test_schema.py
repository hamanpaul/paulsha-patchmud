import json
import pytest

from src.cli import render


def test_legacy_text_is_byte_compatible():
    assert render({"id": "e1", "payload": {"n": 1}}) == "e1: {'n': 1}"


def test_source_is_optional_but_empty_is_rejected():
    assert json.loads(render({"id": "e1", "payload": {}, "source": "sensor"}, json_mode=True))["source"] == "sensor"
    with pytest.raises(ValueError, match="source"):
        render({"id": "e1", "payload": {}, "source": ""})


def test_legacy_json_omits_absent_optional_field():
    assert json.loads(render({"id": "e1", "payload": {}}, json_mode=True)) == {"id": "e1", "payload": {}}


def test_json_has_stable_contract_without_text_prefix():
    assert render({"id": "e1", "payload": {}, "source": "sensor"}, json_mode=True) == '{"id": "e1", "payload": {}, "source": "sensor"}'
