"""execution profile v1 wire 契約與 canonical key 回歸測試。"""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from patchmud.execution_profile import (
    ExecutionProfileError,
    actual_condition_key,
    canonical_profile_bytes,
    parse_descriptor,
    parse_profile,
    profile_key,
)


def _descriptor(*, adapter_version: str = "1", model_revision: str = "unknown") -> dict:
    return {
        "schema_version": 1,
        "id": "fake:atlas-v1",
        "adapter": {
            "id": "fake-adapter",
            "protocol_id": "patchmud.complete",
            "protocol_version": "1",
            "runtime_version": adapter_version,
        },
        "model": {"id": "atlas-v1", "revision": model_revision},
        "effort_grammar": {"type": "string"},
        "provenance": [],
        "metadata": {},
    }


def _profile(plane: str = "resolved") -> dict:
    return {
        "schema_version": 1,
        "plane": plane,
        "conditions": {
            "adapter": {
                "state": "known",
                "value": {
                    "id": "fake-adapter",
                    "protocol_id": "patchmud.complete",
                    "protocol_version": "1",
                    "runtime_version": "1",
                },
            },
            "model": {
                "state": "known",
                "value": {"id": "atlas-v1", "revision": "unknown"},
            },
            "effort": {"state": "known", "value": "deep-native"},
            "loadout": {
                "state": "known",
                "value": {"id": "P0T0R0", "version": "1"},
            },
            "toolset": {
                "state": "known",
                "value": [
                    {"id": "patchmud.tool-mode.none", "version": "1"},
                    {"id": "patchmud.adapter.complete", "version": "1"},
                ],
            },
            "sandbox": {
                "state": "known",
                "value": {"id": "provider-sandbox", "version": "1"},
            },
            "permissions": {
                "state": "known",
                "value": [{"id": "read-only", "version": "1"}],
            },
            "toolchain": {
                "state": "known",
                "value": {"id": "patchmud", "version": "0.0.1"},
            },
        },
        "requirements": {
            "role": {"state": "known", "value": "builder"},
            "minimum_quality": {"state": "unknown", "reason": "not requested"},
            "pin": {"state": "unknown", "reason": "not requested"},
            "independence": {"state": "unknown", "reason": "not requested"},
        },
        "provenance": [],
        "metadata": {},
    }


def test_profile_keys_match_v1_domains_and_canonical_wire_vector():
    descriptor = parse_descriptor(_descriptor())
    keys = {}
    for plane in ("requested", "resolved", "observed"):
        parsed = parse_profile(_profile(plane), descriptor)
        keys[plane] = profile_key(parsed)
        assert canonical_profile_bytes(parsed).startswith(b'["o"')

    assert keys["requested"] == (
        "epk:v1:request:966ce504ff2020188dd7fbb6fff32a2485332db124d18ba7aa85a954325985b1"
    )
    assert keys["resolved"] == (
        "epk:v1:resolved:4caede3aa1b252cbe3dc7734d612089676f353e9f341ff35b4f30b4d7e20ab4d"
    )
    assert keys["observed"] == (
        "epk:v1:observed:32fe12dd9a2e8de43dfac2eca1aaf2d758e99bb4669572609b109aec17df7951"
    )
    assert actual_condition_key(parse_profile(_profile("observed"), descriptor)) == (
        "epk:v1:actual:bbec87b1c3abc288d95baaadf2cb26f586bd219732b3c2e29fac64accc4d530b"
    )


def test_key_tracks_execution_conditions_and_ignores_non_key_metadata():
    descriptor = parse_descriptor(_descriptor())
    base = _profile()
    base_key = profile_key(parse_profile(base, descriptor))

    for field, value in (
        ("effort", {"state": "known", "value": "quick-native"}),
        ("loadout", {"state": "known", "value": {"id": "P1T0R0", "version": "1"}}),
        ("sandbox", {"state": "known", "value": {"id": "other-sandbox", "version": "1"}}),
        ("toolchain", {"state": "known", "value": {"id": "patchmud", "version": "0.0.2"}}),
        ("permissions", {"state": "known", "value": [{"id": "read-write", "version": "1"}]}),
    ):
        changed = deepcopy(base)
        changed["conditions"][field] = value
        assert profile_key(parse_profile(changed, descriptor)) != base_key

    changed_descriptor = parse_descriptor(_descriptor(adapter_version="2"))
    changed_adapter = deepcopy(base)
    changed_adapter["conditions"]["adapter"]["value"]["runtime_version"] = "2"
    assert profile_key(parse_profile(changed_adapter, changed_descriptor)) != base_key

    changed_descriptor = parse_descriptor(_descriptor(model_revision="revision-2"))
    changed_model = deepcopy(base)
    changed_model["conditions"]["model"]["value"]["revision"] = "revision-2"
    assert profile_key(parse_profile(changed_model, changed_descriptor)) != base_key

    reordered = deepcopy(base)
    reordered["conditions"]["toolset"]["value"].reverse()
    reordered["conditions"]["toolset"]["value"].append(
        deepcopy(reordered["conditions"]["toolset"]["value"][0])
    )
    reordered["conditions"]["permissions"]["value"].append(
        deepcopy(reordered["conditions"]["permissions"]["value"][0])
    )
    reordered["metadata"] = {
        "pricing": {"snapshot": "price-b"},
        "timestamps": {"created": "later"},
    }
    assert profile_key(parse_profile(reordered, descriptor)) == base_key


def test_descriptor_and_profile_reject_schema_or_descriptor_mismatch():
    bad_descriptor = _descriptor()
    bad_descriptor["schema_version"] = 2
    with pytest.raises(ExecutionProfileError, match="unsupported_schema"):
        parse_descriptor(bad_descriptor)

    descriptor = parse_descriptor(_descriptor())
    bad_profile = _profile()
    bad_profile["conditions"]["effort"]["value"] = {"unexpected": True}
    with pytest.raises(ExecutionProfileError):
        parse_profile(bad_profile, descriptor)

    bad_profile = _profile()
    bad_profile["conditions"]["model"]["value"]["id"] = "different-model"
    with pytest.raises(ExecutionProfileError, match="descriptor_mismatch"):
        parse_profile(bad_profile, descriptor)


def test_native_effort_grammar_preserves_integer_type():
    descriptor_data = _descriptor()
    descriptor_data["effort_grammar"] = {"type": "integer", "min": 0}
    descriptor = parse_descriptor(descriptor_data)
    profile_data = _profile()
    profile_data["conditions"]["effort"]["value"] = 7
    parsed = parse_profile(profile_data, descriptor)
    assert type(parsed.conditions["effort"]["value"]) is int

    profile_data["conditions"]["effort"]["value"] = 7.0
    with pytest.raises(ExecutionProfileError, match="invalid_type"):
        parse_profile(profile_data, descriptor)


def test_patchmud_runtime_sources_do_not_import_cortex():
    package_root = Path(__file__).resolve().parents[1] / "patchmud"
    for source in package_root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=source.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(
                name == "cortex"
                or name.startswith("cortex.")
                or name == "paulsha_cortex"
                or name.startswith("paulsha_cortex.")
                for name in names
            ), f"{source.name} 出現 Cortex runtime import"
