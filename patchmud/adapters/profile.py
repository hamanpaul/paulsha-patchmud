"""Adapter capability descriptor、註冊解析與 profile v1 封存資料。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Protocol, cast

from patchmud.adapters.base import AdapterError, AdapterResponse, ModelAdapter
from patchmud.execution_profile import (
    ExecutionProfileDescriptor,
    ExecutionProfileError,
    actual_condition_key,
    parse_descriptor,
    parse_profile,
    profile_key,
    validate_effort_value,
)

__all__ = [
    "AdapterCapabilities",
    "AdapterRegistration",
    "AdapterResolutionError",
    "ProfiledAdapter",
    "ParsedAdapterSpec",
    "build_default_registrations",
    "build_execution_profile_record",
    "build_registered_adapter",
    "require_profiled_adapter",
]


class AdapterResolutionError(ValueError):
    """adapter spec 或 capability 不符合宣告。"""


@dataclass(frozen=True)
class ParsedAdapterSpec:
    """由一個 adapter registration 解析出的模型與非機密建構選項。"""

    model_id: str
    options: Mapping[str, object] = field(default_factory=dict)
    profile_model_id: str | None = None
    model_revision: str = "unknown"


@dataclass(frozen=True)
class AdapterCapabilities:
    """一個 adapter family 的原生執行能力與 profile wire descriptor 欄位。"""

    adapter_id: str
    protocol_id: str
    protocol_version: str
    runtime_version: str
    effort_grammar: Mapping[str, object]
    default_effort: object | None
    tool_modes: Mapping[str, tuple[Mapping[str, object], ...]]
    default_tool_mode: str
    sandbox: Mapping[str, object]
    permissions: tuple[Mapping[str, object], ...]
    toolchain: Mapping[str, object]
    model_parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "effort_grammar", _freeze(self.effort_grammar))
        object.__setattr__(
            self,
            "tool_modes",
            MappingProxyType(
                {
                    name: tuple(_freeze(ref) for ref in refs)
                    for name, refs in self.tool_modes.items()
                }
            ),
        )
        object.__setattr__(self, "sandbox", _freeze(self.sandbox))
        object.__setattr__(
            self, "permissions", tuple(_freeze(ref) for ref in self.permissions)
        )
        object.__setattr__(self, "toolchain", _freeze(self.toolchain))
        object.__setattr__(self, "model_parameters", _freeze(self.model_parameters))

    def descriptor_for(
        self,
        model_id: str,
        *,
        descriptor_id: str,
        model_revision: str = "unknown",
    ) -> ExecutionProfileDescriptor:
        """產生並驗證與 Cortex execution-profile v1 相同的 descriptor。"""
        parameter_bytes = json.dumps(
            _thaw(self.model_parameters),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        parameter_digest = hashlib.sha256(parameter_bytes).hexdigest()
        return parse_descriptor(
            {
                "schema_version": 1,
                "id": descriptor_id,
                "adapter": {
                    "id": self.adapter_id,
                    "protocol_id": self.protocol_id,
                    "protocol_version": self.protocol_version,
                    "runtime_version": (
                        f"{self.runtime_version};parameters-sha256:{parameter_digest}"
                    ),
                },
                "model": {"id": model_id, "revision": model_revision},
                "effort_grammar": _thaw(self.effort_grammar),
                "provenance": [
                    {"kind": "descriptor-source", "ref": "patchmud:adapter-profile-v1"}
                ],
                "metadata": {
                    "discovery": {"model_parameters": _thaw(self.model_parameters)}
                },
            }
        )


TargetParser = Callable[[str], ParsedAdapterSpec]
AdapterFactory = Callable[[str, object | None, dict[str, object]], ModelAdapter]


@dataclass(frozen=True)
class AdapterRegistration:
    """adapter spec scheme、descriptor 與 concrete adapter factory 的註冊。"""

    scheme: str
    capabilities: AdapterCapabilities
    factory: AdapterFactory
    parse_target: TargetParser | None = None
    unavailable_message: str = "adapter runtime 不可用"
    runtime_executable: str | None = None


class ProfiledAdapter(Protocol):
    """ModelAdapter 加上 PatchMUD profile descriptor 的結構式 conformance。"""

    execution_profile_descriptor: ExecutionProfileDescriptor
    execution_profile_capabilities: AdapterCapabilities
    execution_profile_requested_effort: object | None
    execution_profile_resolved_effort: object | None
    execution_profile_requested_tool_mode: str | None
    execution_profile_resolved_tool_mode: str
    execution_profile_toolchain: Mapping[str, object] | None

    def complete(self, messages: list[dict]) -> AdapterResponse: ...


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _parse_plain_target(target: str) -> ParsedAdapterSpec:
    if not target:
        raise AdapterResolutionError("model spec 缺 model id")
    return ParsedAdapterSpec(model_id=target)


def _parse_claude_target(target: str) -> ParsedAdapterSpec:
    return ParsedAdapterSpec(model_id=target or "claude-sonnet-5")


def _parse_openai_target(target: str) -> ParsedAdapterSpec:
    model_id, separator, base_url = target.partition("@")
    if not model_id:
        raise AdapterResolutionError("openai spec 缺 model id：openai:<model>[@<base_url>]")
    options: dict[str, object] = {}
    if separator:
        if not base_url:
            raise AdapterResolutionError("openai base_url 不可為空")
        endpoint_hash = hashlib.sha256(base_url.encode("utf-8")).hexdigest()
        options["base_url"] = base_url
        revision = f"endpoint-sha256:{endpoint_hash}"
    else:
        revision = "unknown"
    profile_model_id = (
        f"{model_id}@endpoint-sha256-{revision.removeprefix('endpoint-sha256:')}"
        if separator
        else model_id
    )
    return ParsedAdapterSpec(
        model_id=model_id,
        options=options,
        profile_model_id=profile_model_id,
        model_revision="unknown",
    )


def _parse_scripted_target(target: str) -> ParsedAdapterSpec:
    if not target:
        raise AdapterResolutionError("scripted spec 缺劇本檔：scripted:<file>")
    return ParsedAdapterSpec(
        model_id="offline-scripted",
        options={"script_path": Path(target)},
        profile_model_id="offline-scripted",
        model_revision="patchmud-script-v1",
    )


def _patchmud_version() -> str:
    try:
        return importlib.metadata.version("paulsha-patchmud")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _capabilities(
    adapter_id: str,
    *,
    effort_grammar: Mapping[str, object] | None = None,
    default_effort: object | None = None,
    sandbox_id: str,
    permissions: tuple[Mapping[str, object], ...] = (),
    model_parameters: Mapping[str, object] | None = None,
    source_modules: tuple[str, ...] = (),
) -> AdapterCapabilities:
    version = _patchmud_version()
    source_digest = _adapter_source_digest(source_modules)
    return AdapterCapabilities(
        adapter_id=adapter_id,
        protocol_id="patchmud.complete",
        protocol_version="1",
        runtime_version=(
            f"patchmud-{version};adapter-sha256:{source_digest};config-v1"
        ),
        effort_grammar=effort_grammar or {"type": "none"},
        default_effort=default_effort,
        tool_modes={
            "none": ({"id": "patchmud.tool-mode.none", "version": "1"},)
        },
        default_tool_mode="none",
        sandbox={"id": sandbox_id, "version": "1"},
        permissions=permissions,
        toolchain={"id": "patchmud", "version": version},
        model_parameters=model_parameters or {},
    )


def _adapter_source_digest(source_modules: tuple[str, ...]) -> str:
    """以 source bytes 固定目前 resolver 與 adapter 實作 revision。"""
    digest = hashlib.sha256()
    module_names = sorted(set(("patchmud.adapters.profile", *source_modules)))
    for module_name in module_names:
        spec = importlib.util.find_spec(module_name)
        if spec is None or spec.origin is None:
            raise AdapterResolutionError(f"找不到 adapter source：{module_name}")
        source = Path(spec.origin).read_bytes()
        encoded_name = module_name.encode("ascii")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(source).to_bytes(8, "big"))
        digest.update(source)
    return digest.hexdigest()


def build_default_registrations() -> tuple[AdapterRegistration, ...]:
    """建立內建 adapter registry；model 與 effort 不在 resolver 硬編。"""
    from patchmud.adapters.agy_cli import AgyCliAdapter
    from patchmud.adapters.anthropic import AnthropicAdapter
    from patchmud.adapters.claude_cli import ClaudeCliAdapter
    from patchmud.adapters.codex_cli import CodexCliAdapter
    from patchmud.adapters.openai_compat import OpenAICompatAdapter
    from patchmud.adapters.scripted import ScriptedAdapter

    no_tools = ("patchmud.permissions.no-tools", "1")

    def no_effort(adapter_id: str, sandbox: str) -> AdapterCapabilities:
        adapter_modules = {
            "patchmud.scripted": ("patchmud.adapters.scripted",),
            "patchmud.claude-cli": (
                "patchmud.adapters.claude_cli",
                "patchmud.adapters.cli_base",
            ),
            "patchmud.anthropic-api": ("patchmud.adapters.anthropic",),
            "patchmud.openai-compatible-api": ("patchmud.adapters.openai_compat",),
        }
        return _capabilities(
            adapter_id,
            sandbox_id=sandbox,
            permissions=({"id": no_tools[0], "version": no_tools[1]},),
            source_modules=adapter_modules[adapter_id],
        )

    codex_caps = _capabilities(
        "patchmud.codex-cli",
        effort_grammar={"type": "string", "enum": ["low", "medium", "high", "xhigh"]},
        default_effort="high",
        sandbox_id="codex.read-only",
        permissions=({"id": "workspace.read-only", "version": "1"},),
        model_parameters={"tool_access": "none", "ephemeral": True},
        source_modules=("patchmud.adapters.codex_cli", "patchmud.adapters.cli_base"),
    )
    agy_caps = _capabilities(
        "patchmud.agy-cli",
        effort_grammar={"type": "string", "enum": ["low", "medium", "high"]},
        default_effort="high",
        sandbox_id="agy.sandbox",
        permissions=({"id": "workspace.sandboxed", "version": "1"},),
        model_parameters={"tool_access": "none"},
        source_modules=("patchmud.adapters.agy_cli", "patchmud.adapters.cli_base"),
    )

    def make_scripted(model_id: str, _effort: object | None, options: dict[str, object]) -> ModelAdapter:
        path = options["script_path"]
        if not isinstance(path, Path) or not path.is_file():
            raise AdapterResolutionError(f"scripted 劇本檔不存在：{path!r}")
        text = path.read_text(encoding="utf-8")
        replies: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            if line.strip() == "-----":
                replies.append("\n".join(current))
                current = []
            else:
                current.append(line)
        replies.append("\n".join(current))
        replies = [reply for reply in replies if reply.strip()]
        if not replies:
            raise AdapterResolutionError(f"scripted 劇本檔沒有任何回覆：{path}")
        return ScriptedAdapter(replies)

    def make_claude(model_id: str, _effort: object | None, _options: dict[str, object]) -> ModelAdapter:
        return ClaudeCliAdapter(model=model_id)

    def make_codex(model_id: str, effort: object | None, _options: dict[str, object]) -> ModelAdapter:
        if not isinstance(effort, str):
            raise AdapterResolutionError("codex effort 必須是原生字串")
        return CodexCliAdapter(model=model_id, effort=effort)

    def make_agy(model_id: str, effort: object | None, _options: dict[str, object]) -> ModelAdapter:
        if not isinstance(effort, str):
            raise AdapterResolutionError("agy effort 必須是原生字串")
        return AgyCliAdapter(model=model_id, effort=effort)

    def make_anthropic(model_id: str, _effort: object | None, _options: dict[str, object]) -> ModelAdapter:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        if not api_key and not auth_token:
            raise AdapterResolutionError(
                "未設定 Anthropic 憑證：\n"
                "  1. 設 ANTHROPIC_API_KEY，或用 OAuth——`ant auth login` 後設定登入環境\n"
                "  2. 改用其他家的 CLI 登入態（同樣不需 API key）：\n"
                "     patchmud versus <關卡> --models sol,flash\n"
                "  3. 若無任何雲端登入，可用免 Key 地端模型（如 Ollama）：\n"
                "     patchmud versus <關卡> --models openai:llama3@http://localhost:11434/v1\n"
                "  4. 或用離線劇本模式：scripted:<file>"
            )
        return AnthropicAdapter(model_id, api_key, auth_token=auth_token)

    def make_openai(model_id: str, _effort: object | None, options: dict[str, object]) -> ModelAdapter:
        kwargs: dict[str, object] = {}
        if "base_url" in options:
            kwargs["base_url"] = options["base_url"]
        return OpenAICompatAdapter(model_id, os.environ.get("OPENAI_API_KEY", ""), **kwargs)

    no_effort_codex = codex_caps
    return (
        AdapterRegistration(
            "scripted",
            no_effort("patchmud.scripted", "patchmud.offline-scripted"),
            make_scripted,
            _parse_scripted_target,
        ),
        AdapterRegistration(
            "claude",
            no_effort("patchmud.claude-cli", "claude.no-tools"),
            make_claude,
            _parse_claude_target,
            unavailable_message="系統未安裝 `claude` CLI（找不到 `claude` 可執行檔）",
            runtime_executable="claude",
        ),
        AdapterRegistration(
            "codex",
            no_effort_codex,
            make_codex,
            unavailable_message=(
                "系統未安裝 `codex` CLI（找不到 `codex` 可執行檔）。\n"
                "  安裝後以 `codex login` 建立 OAuth 登入態即可，不需 OPENAI_API_KEY。"
            ),
            runtime_executable="codex",
        ),
        AdapterRegistration(
            "agy",
            agy_caps,
            make_agy,
            unavailable_message=(
                "系統未安裝 `agy` CLI（找不到 `agy` 可執行檔）。\n"
                "  安裝並登入後即可使用，不需 API key。"
            ),
            runtime_executable="agy",
        ),
        AdapterRegistration(
            "anthropic",
        _capabilities(
            "patchmud.anthropic-api",
            sandbox_id="patchmud.no-executor-sandbox",
            permissions=({"id": no_tools[0], "version": no_tools[1]},),
            model_parameters={"max_tokens": 4096, "anthropic_version": "2023-06-01"},
        ),
            make_anthropic,
        ),
        AdapterRegistration(
            "openai",
            _capabilities(
                "patchmud.openai-compatible-api",
                sandbox_id="patchmud.no-executor-sandbox",
                permissions=({"id": no_tools[0], "version": no_tools[1]},),
                model_parameters={"max_tokens": 4096},
            ),
            make_openai,
            _parse_openai_target,
        ),
    )


def _resolve_registration(
    scheme: str,
    registrations: tuple[AdapterRegistration, ...],
) -> AdapterRegistration:
    matches = [entry for entry in registrations if entry.scheme == scheme]
    if not matches:
        raise AdapterResolutionError(f"未知 model spec：{scheme}")
    if len(matches) != 1:
        raise AdapterResolutionError(f"adapter scheme 重複註冊：{scheme}")
    return matches[0]


def _runtime_toolchain(registration: AdapterRegistration) -> dict[str, str] | None:
    """以 executable bytes 識別 CLI，不啟動外部程序，也不封存本機路徑。"""
    executable = registration.runtime_executable
    if executable is None:
        return dict(registration.capabilities.toolchain)
    found = shutil.which(executable)
    if found is None:
        return None
    path = Path(found).resolve()
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    except OSError:
        return None
    return {"id": f"{executable}-cli", "version": f"sha256:{digest.hexdigest()}"}


def build_registered_adapter(
    spec: str,
    *,
    registrations: tuple[AdapterRegistration, ...] | None = None,
    effort: object | None = None,
    tool_mode: str | None = None,
    availability_checks: Mapping[str, Callable[[], bool]] | None = None,
) -> ModelAdapter:
    """驗證設定後才建 adapter；所有外部程序／API 都由 complete() 啟動。"""
    scheme, separator, target = spec.partition(":")
    if not separator:
        raise AdapterResolutionError(f"未知 model spec：{spec!r}")
    registry = registrations if registrations is not None else build_default_registrations()
    registration = _resolve_registration(scheme, registry)
    parsed = (registration.parse_target or _parse_plain_target)(target)
    if not parsed.model_id:
        raise AdapterResolutionError(f"{scheme} spec 缺 model id")
    resolved_tool_mode = tool_mode or registration.capabilities.default_tool_mode
    if tool_mode is not None and not tool_mode:
        raise AdapterResolutionError("tool mode 不可為空")
    if resolved_tool_mode not in registration.capabilities.tool_modes:
        raise AdapterResolutionError(f"不支援的 tool mode：{resolved_tool_mode!r}")

    profile_model_id = parsed.profile_model_id or parsed.model_id
    descriptor_id = f"{scheme}:{profile_model_id}"
    try:
        descriptor = registration.capabilities.descriptor_for(
            profile_model_id,
            descriptor_id=descriptor_id,
            model_revision=parsed.model_revision,
        )
        grammar = descriptor.effort_grammar
        if effort is not None:
            validate_effort_value(descriptor, effort)
            resolved_effort = effort
        elif grammar["type"] == "none":
            resolved_effort = None
        else:
            resolved_effort = registration.capabilities.default_effort
            if resolved_effort is None:
                raise AdapterResolutionError("effort descriptor 缺明示 default")
            validate_effort_value(descriptor, resolved_effort)
    except ExecutionProfileError as exc:
        raise AdapterResolutionError(f"profile 設定不支援：{exc.code}") from exc

    checks = availability_checks or {}
    available = checks.get(scheme)
    if available is not None and not available():
        raise AdapterResolutionError(registration.unavailable_message)
    try:
        factory_options = dict(parsed.options)
        factory_options["tool_mode"] = resolved_tool_mode
        adapter = registration.factory(parsed.model_id, resolved_effort, factory_options)
    except AdapterError as exc:
        raise AdapterResolutionError(str(exc)) from exc
    if not isinstance(adapter, ModelAdapter):
        raise AdapterResolutionError("adapter 必須實作 ModelAdapter.complete")
    adapter.execution_profile_descriptor = descriptor
    adapter.execution_profile_capabilities = registration.capabilities
    adapter.execution_profile_requested_effort = effort
    adapter.execution_profile_resolved_effort = resolved_effort
    adapter.execution_profile_requested_tool_mode = tool_mode
    adapter.execution_profile_resolved_tool_mode = resolved_tool_mode
    adapter.execution_profile_toolchain = _freeze(_runtime_toolchain(registration))
    return adapter


def require_profiled_adapter(adapter: ModelAdapter) -> ProfiledAdapter:
    """檢查新舊 adapter 已符合 profile extension，不按產品名稱分支。"""
    if not isinstance(adapter, ModelAdapter) or not callable(
        getattr(adapter, "complete", None)
    ):
        raise AdapterResolutionError("adapter 必須實作 ModelAdapter.complete")
    descriptor = getattr(adapter, "execution_profile_descriptor", None)
    capabilities = getattr(adapter, "execution_profile_capabilities", None)
    required = (
        "execution_profile_requested_effort",
        "execution_profile_resolved_effort",
        "execution_profile_requested_tool_mode",
        "execution_profile_resolved_tool_mode",
        "execution_profile_toolchain",
    )
    if not isinstance(descriptor, ExecutionProfileDescriptor) or not isinstance(
        capabilities, AdapterCapabilities
    ) or any(not hasattr(adapter, name) for name in required):
        raise AdapterResolutionError("adapter 缺 execution profile conformance 欄位")
    return cast(ProfiledAdapter, adapter)


def _known(value: object) -> dict[str, object]:
    return {"state": "known", "value": value}


def _unknown(reason: str) -> dict[str, object]:
    return {"state": "unknown", "reason": reason}


def _requirements() -> dict[str, object]:
    return {
        "role": _unknown("role is assigned by report cohort"),
        "minimum_quality": _unknown("not requested by execution CLI"),
        "pin": _unknown("not requested by execution CLI"),
        "independence": _unknown("not requested by execution CLI"),
    }


def build_execution_profile_record(adapter: ModelAdapter, loadout: str) -> dict[str, object]:
    """建 requested/resolved/observed v1 records，不推定 provider 回報值。"""
    profiled = require_profiled_adapter(adapter)
    descriptor = profiled.execution_profile_descriptor
    capabilities = profiled.execution_profile_capabilities
    requested_effort = profiled.execution_profile_requested_effort
    resolved_effort = profiled.execution_profile_resolved_effort
    requested_mode = profiled.execution_profile_requested_tool_mode
    resolved_mode = profiled.execution_profile_resolved_tool_mode
    resolved_toolchain = profiled.execution_profile_toolchain
    grammar = descriptor.effort_grammar

    def effort_value(value: object | None, *, omitted: bool = False) -> dict[str, object]:
        if grammar["type"] == "none":
            return {"state": "not_applicable"}
        if omitted:
            return _unknown("not supplied by CLI")
        if value is None:
            return _unknown("provider did not confirm effective effort")
        return _known(value)

    def toolset_value(mode: str | None, *, omitted: bool = False) -> dict[str, object]:
        if omitted:
            return _unknown("not supplied by CLI")
        refs = capabilities.tool_modes[mode or capabilities.default_tool_mode]
        return _known([dict(ref) for ref in refs])

    base_conditions = {
        "adapter": _known(descriptor.to_dict()["adapter"]),
        "model": _known(descriptor.to_dict()["model"]),
        "loadout": _known({"id": loadout, "version": "patchmud-loadout-v1"}),
        "sandbox": _known(dict(capabilities.sandbox)),
        "permissions": _known([dict(ref) for ref in capabilities.permissions]),
    }

    def profile(plane: str, effort_condition: dict[str, object], toolset: dict[str, object]):
        if plane == "requested":
            toolchain_condition = _unknown("runtime toolchain is resolved after the request")
        elif resolved_toolchain is None:
            toolchain_condition = _unknown("runtime executable identity is unavailable")
        else:
            toolchain_condition = _known(_thaw(resolved_toolchain))
        conditions = {
            **base_conditions,
            "effort": effort_condition,
            "toolset": toolset,
            "toolchain": toolchain_condition,
        }
        if plane == "observed":
            conditions["model"] = _unknown("provider response did not identify effective model revision")
            conditions["effort"] = effort_value(None)
        return parse_profile(
            {
                "schema_version": 1,
                "plane": plane,
                "conditions": conditions,
                "requirements": _requirements(),
                "provenance": [
                    {"kind": "profile-source", "ref": "patchmud:adapter-resolver-v1"}
                ],
                "metadata": {},
            },
            descriptor,
        )

    requested = profile(
        "requested",
        effort_value(requested_effort, omitted=requested_effort is None),
        toolset_value(requested_mode, omitted=requested_mode is None),
    )
    resolved = profile(
        "resolved",
        effort_value(resolved_effort),
        toolset_value(resolved_mode),
    )
    observed = profile(
        "observed",
        effort_value(None),
        toolset_value(resolved_mode),
    )
    return {
        "schema_version": 1,
        "descriptor": descriptor.to_dict(),
        "profile_id": profile_key(resolved),
        "requested": requested.to_dict(),
        "requested_key": profile_key(requested),
        "resolved": resolved.to_dict(),
        "resolved_key": profile_key(resolved),
        "observed": observed.to_dict(),
        "observed_key": profile_key(observed),
        "actual_condition_key": actual_condition_key(observed),
    }
