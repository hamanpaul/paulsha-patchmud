"""adapter profile descriptor 的動態註冊與 conformance 測試。"""

from __future__ import annotations

from patchmud.adapters.base import AdapterResponse, ModelAdapter
from patchmud.adapters.profile import (
    AdapterCapabilities,
    AdapterRegistration,
    AdapterResolutionError,
    ProfiledAdapter,
    build_default_registrations,
    build_execution_profile_record,
    build_registered_adapter,
    require_profiled_adapter,
)


class FakeAdapter(ModelAdapter):
    usage_provider = "fake"

    def complete(self, messages: list[dict]) -> AdapterResponse:
        return AdapterResponse(text="ok", usage_raw={}, wall_ms=1)


def _registration(
    factory_calls: list[tuple[str, object, dict]],
    *,
    model_parameters: dict | None = None,
) -> AdapterRegistration:
    capabilities = AdapterCapabilities(
        adapter_id="test.fake-adapter",
        protocol_id="patchmud.complete",
        protocol_version="1",
        runtime_version="fake-runtime-3",
        effort_grammar={"type": "string", "enum": ["steady", "deep-native"]},
        default_effort="steady",
        tool_modes={
            "none": ({"id": "patchmud.tool-mode.none", "version": "1"},),
            "read-only": ({"id": "patchmud.tool-mode.read-only", "version": "1"},),
        },
        default_tool_mode="none",
        sandbox={"id": "fake.sandbox", "version": "2"},
        permissions=({"id": "read-only", "version": "1"},),
        toolchain={"id": "patchmud", "version": "0.0.1"},
        model_parameters=model_parameters or {"max_output": 1024},
    )

    def factory(model_id: str, effort: object, options: dict) -> ModelAdapter:
        factory_calls.append((model_id, effort, options))
        return FakeAdapter()

    return AdapterRegistration(
        scheme="fake",
        capabilities=capabilities,
        factory=factory,
    )


def test_new_model_and_native_effort_use_descriptor_and_adapter_protocol():
    calls: list[tuple[str, object, dict]] = []
    adapter = build_registered_adapter(
        "fake:atlas-next",
        registrations=(_registration(calls),),
        effort="deep-native",
        tool_mode="none",
    )

    assert isinstance(adapter, ModelAdapter)
    assert require_profiled_adapter(adapter) is adapter
    assert callable(ProfiledAdapter.complete)
    assert adapter.complete([{"role": "user", "content": "offline"}]).text == "ok"
    assert calls == [("atlas-next", "deep-native", {"tool_mode": "none"})]
    assert adapter.execution_profile_descriptor.model["id"] == "atlas-next"

    record = build_execution_profile_record(adapter, "P0T0R0")
    assert record["profile_id"].startswith("epk:v1:resolved:")
    assert record["resolved"]["conditions"]["effort"] == {
        "state": "known",
        "value": "deep-native",
    }
    assert record["actual_condition_key"] is None

    read_only = build_registered_adapter(
        "fake:atlas-next",
        registrations=(_registration([]),),
        effort="deep-native",
        tool_mode="read-only",
    )
    other_record = build_execution_profile_record(read_only, "P0T0R0")
    assert record["profile_id"] != other_record["profile_id"]

    changed_parameters = build_registered_adapter(
        "fake:atlas-next",
        registrations=(_registration([], model_parameters={"max_output": 2048}),),
        effort="deep-native",
        tool_mode="none",
    )
    parameter_record = build_execution_profile_record(changed_parameters, "P0T0R0")
    assert parameter_record["profile_id"] != record["profile_id"]
    assert parameter_record["descriptor"]["metadata"]["discovery"][
        "model_parameters"
    ] == {"max_output": 2048}


def test_unsupported_effort_or_tool_mode_is_rejected_before_factory():
    calls: list[tuple[str, object, dict]] = []
    registration = _registration(calls)

    for kwargs in ({"effort": "unlisted-effort"}, {"tool_mode": "write-access"}):
        try:
            build_registered_adapter(
                "fake:atlas-next", registrations=(registration,), **kwargs
            )
        except AdapterResolutionError:
            pass
        else:
            raise AssertionError("未支援設定應在建立 adapter 前拒絕")

    assert calls == []


def test_custom_api_endpoint_changes_profile_identity_without_storing_url():
    registrations = build_default_registrations()
    first = build_registered_adapter(
        "openai:atlas@http://local-a.invalid/v1", registrations=registrations
    )
    second = build_registered_adapter(
        "openai:atlas@http://local-b.invalid/v1", registrations=registrations
    )

    first_record = build_execution_profile_record(first, "P0T0R0")
    second_record = build_execution_profile_record(second, "P0T0R0")
    first_model = first_record["descriptor"]["model"]["id"]
    assert first_model.startswith("atlas@endpoint-sha256-")
    assert "local-a.invalid" not in first_model
    assert first_record["profile_id"] != second_record["profile_id"]
