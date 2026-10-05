"""builder lane 的 execution profile：由**實際發射契約**推導，算法比照 Cortex ``resolve_profile``。

Cortex Manager 在派工時以 ``make_launcher_profile(launcher, identity, persona,
requirements=...)`` 從已特化的 launcher 推導 profile；這裡以 builder lane 的
:class:`DispatchLaunchContract`（同一份契約也用來組 codex argv 與外層沙箱）走同一條
推導，因此 requested／resolved plane 與 Cortex 逐欄相同、key 相同（CI 以真 Cortex
程式比對）。

observed plane 只採兩個受信任來源，任一條件觀測不到就維持 unknown：

- ``codex-app-server:thread/read``：provider 持久化的 model／reasoningEffort（#44）。
- ``patchmud:dispatch-launch-v1``：**實際執行過**的 codex argv 與外層 bwrap 規格
  反推出的 adapter／loadout／toolset／sandbox／permissions／toolchain，不讀設定值。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from patchmud.adapters.cortex_catalog import CortexCatalogError, cortex_adapter_identity
from patchmud.adapters.observation import RuntimeObservation
from patchmud.cortex_dispatch.target import DispatchTarget
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
    "DISPATCH_PROFILE_SOURCE",
    "LAUNCH_OBSERVER",
    "DispatchLaunchContract",
    "DispatchProfileError",
    "LaunchObservation",
    "build_dispatch_profile_record",
    "resolve_launch_contract",
]

DISPATCH_PROFILE_SOURCE = "patchmud:cortex-dispatch-v1"
LAUNCH_OBSERVER = "patchmud:dispatch-launch-v1"
#: Cortex ``resolve_profile`` 預設 descriptor 的 model revision 字面值。
MODEL_REVISION = "unreported"
#: Cortex ``_launcher_conditions`` 的 workspace-write 契約（mode 與 permission 同名）。
WORKSPACE_WRITE = "workspace-write"

_CONDITIONS = (
    "adapter",
    "model",
    "effort",
    "loadout",
    "toolset",
    "sandbox",
    "permissions",
    "toolchain",
)


class DispatchProfileError(ValueError):
    """發射契約無法對應到一個可宣告的 Cortex profile（fail-closed）。"""


@dataclass(frozen=True)
class DispatchLaunchContract:
    """builder lane 的發射契約：argv、外層沙箱與 profile 共用這一份。"""

    executor: str
    model_id: str
    persona: str
    requested_effort: str | None
    effort: str
    adapter_fields: Mapping[str, str]
    efforts: tuple[str, ...]
    toolchain_version: str
    adapter_source_ref: str
    adapter_catalog_sha256: str
    commit_required: bool = True
    sandbox_mode: str = WORKSPACE_WRITE
    loadout_version: str = "1"
    sandbox_version: str = "1"
    #: launcher 的 effective tools（codex 沒有）＋ commit-required 追加的 git-commit。
    tools: tuple[str, ...] = field(default=("git-commit",))

    def toolset(self) -> list[dict[str, str]]:
        """逐字比照 Cortex ``_safe_tool_ids``：只投影成 capability 名稱。"""
        safe: set[str] = set()
        for value in self.tools:
            text = str(value)
            if text.startswith("write("):
                safe.add("write-scoped")
            elif text.startswith("shell("):
                safe.add("shell-scoped")
            elif text in {"Bash", "Read", "Edit", "Write", "Glob", "Grep"}:
                safe.add(text.lower())
            elif text and "/" not in text and "\\" not in text:
                safe.add(text.lower())
        return [{"id": value, "version": "1"} for value in sorted(safe)]

    def permissions(self) -> list[dict[str, str]]:
        if self.sandbox_mode in {"read-only", "review-only", "write-forbidden"}:
            names = {self.sandbox_mode}
        else:
            names = {WORKSPACE_WRITE}
        return [{"id": name, "version": "1"} for name in sorted(names)]

    def resolved_conditions(self) -> dict[str, object]:
        return {
            "loadout": {"id": self.persona, "version": self.loadout_version},
            "toolset": self.toolset(),
            "sandbox": {"id": self.sandbox_mode, "version": self.sandbox_version},
            "permissions": self.permissions(),
            "toolchain": {"id": self.executor, "version": self.toolchain_version},
        }


@dataclass(frozen=True)
class LaunchObservation:
    """實際執行過的發射事實，由 lane 在 codex 結束後彙總（不含路徑原文以外的 payload）。"""

    #: codex 真的啟動（收到唯一的 ``thread.started``）。
    started: bool
    #: 執行過的 codex argv 是否逐 token 等於 cortex-adapter-v1 的 commit-required builder 列。
    argv_conforms: bool
    #: 外層邊界：可寫只有 workspace＋codex 狀態目錄＋tmpfs，且無 host 網路。
    workspace_write_boundary: bool
    #: prompt 前言逐字是 Cortex builder persona 契約。
    persona_contract_rendered: bool
    #: workspace 的 ``.git`` 在可寫邊界內且 git 可執行（commit-required）。
    git_commit_available: bool
    #: 稽核用 digest／計數（只進 metadata，不進 key）。
    facts: Mapping[str, object] = field(default_factory=dict)


def resolve_launch_contract(target: DispatchTarget) -> DispatchLaunchContract:
    """target → 發射契約；effort 比照 Cortex：launcher 明示值優先，否則 catalog 預設。"""
    try:
        identity = cortex_adapter_identity(target.identity.executor)
    except CortexCatalogError as exc:
        raise DispatchProfileError(str(exc)) from exc
    model_id = target.identity.model_id
    effort = target.launcher_effort or identity.default_effort_for(model_id)
    if effort is None:
        raise DispatchProfileError(
            "Cortex catalog 沒有這個 model 的 effort 預設，target 也未明示；"
            "effort 無法確定時不宣告 profile"
        )
    if effort not in identity.efforts:
        raise DispatchProfileError(f"effort 不在 Cortex catalog 值域內：{effort!r}")
    return DispatchLaunchContract(
        executor=target.identity.executor,
        model_id=model_id,
        persona=target.persona,
        requested_effort=target.launcher_effort,
        effort=effort,
        adapter_fields=dict(identity.descriptor_fields),
        efforts=identity.efforts,
        toolchain_version=identity.descriptor_fields["runtime_version"],
        adapter_source_ref=identity.source_ref,
        adapter_catalog_sha256=identity.sha256,
        commit_required=target.card.commit_policy == "required",
    )


def _known(value: object) -> dict[str, object]:
    return {"state": "known", "value": value}


def _unknown(reason: str) -> dict[str, object]:
    return {"state": "unknown", "reason": reason}


def _descriptor(contract: DispatchLaunchContract, target: DispatchTarget) -> ExecutionProfileDescriptor:
    return parse_descriptor(
        {
            "schema_version": 1,
            "id": f"{contract.adapter_fields['id']}:{contract.model_id}",
            "adapter": dict(contract.adapter_fields),
            "model": {"id": contract.model_id, "revision": MODEL_REVISION},
            "effort_grammar": {"type": "string", "enum": list(contract.efforts)},
            "provenance": [
                {"kind": "descriptor-source", "ref": DISPATCH_PROFILE_SOURCE},
                {"kind": "adapter-identity-source", "ref": contract.adapter_source_ref},
            ],
            "metadata": {
                "discovery": {
                    "adapter_catalog_sha256": contract.adapter_catalog_sha256,
                    "dispatch_target": target.name,
                    "dispatch_target_sha256": target.sha256,
                }
            },
        }
    )


def _requirements(target: DispatchTarget, *, plane: str) -> dict[str, object]:
    """Cortex ``resolve_profile`` 的 requirements（requested／resolved 同形）。"""
    supplied = target.requirements()
    return {
        "role": _known(target.role),
        "minimum_quality": (
            _known(supplied["minimum_quality"])
            if "minimum_quality" in supplied
            else _unknown("quality-not-specified")
        ),
        "pin": _known(supplied.get("pin")),
        "independence": _known(supplied["independence"]),
    }


def _observed_conditions(
    contract: DispatchLaunchContract,
    descriptor: ExecutionProfileDescriptor,
    launch: LaunchObservation | None,
    thread: RuntimeObservation | None,
) -> dict[str, dict[str, object]]:
    if launch is None and thread is None:
        unknown = _unknown("no-trusted-runtime-observation")
        return {name: dict(unknown) for name in _CONDITIONS}
    resolved = contract.resolved_conditions()
    conditions: dict[str, dict[str, object]] = {}
    started = launch is not None and launch.started
    if started and launch.argv_conforms:
        conditions["adapter"] = _known(dict(contract.adapter_fields))
        conditions["toolchain"] = _known(resolved["toolchain"])
    else:
        reason = "codex-not-started" if not started else "launch-argv-not-cortex-adapter-v1"
        conditions["adapter"] = _unknown(reason)
        conditions["toolchain"] = _unknown(reason)
    conditions["loadout"] = (
        _known(resolved["loadout"])
        if started and launch.persona_contract_rendered
        else _unknown("persona-contract-not-observed")
    )
    conditions["toolset"] = (
        _known(resolved["toolset"])
        if started and launch.git_commit_available
        else _unknown("git-commit-not-available")
    )
    if started and launch.workspace_write_boundary:
        conditions["sandbox"] = _known(resolved["sandbox"])
        conditions["permissions"] = _known(resolved["permissions"])
    else:
        conditions["sandbox"] = _unknown("workspace-write-boundary-not-observed")
        conditions["permissions"] = _unknown("workspace-write-boundary-not-observed")

    model_id = descriptor.to_dict()["model"]["id"]
    if thread is None:
        conditions["model"] = _unknown("provider did not confirm effective model")
        conditions["effort"] = _unknown("provider did not confirm effective effort")
    else:
        if thread.model_id is None:
            conditions["model"] = _unknown(
                thread.model_reason or "provider did not confirm effective model"
            )
        elif thread.model_id != model_id:
            conditions["model"] = _unknown("provider-reported model differs from descriptor model")
        else:
            conditions["model"] = _known(descriptor.to_dict()["model"])
        if thread.effort is None:
            conditions["effort"] = _unknown(
                thread.effort_reason or "provider did not confirm effective effort"
            )
        else:
            try:
                validate_effort_value(descriptor, thread.effort)
            except ExecutionProfileError:
                conditions["effort"] = _unknown(
                    "provider-reported effort is outside descriptor grammar"
                )
            else:
                conditions["effort"] = _known(thread.effort)
    return conditions


def build_dispatch_profile_record(
    target: DispatchTarget,
    contract: DispatchLaunchContract,
    *,
    launch: LaunchObservation | None = None,
    thread: RuntimeObservation | None = None,
) -> dict[str, object]:
    """組 requested／resolved／observed 三個 plane（record 形狀同 ``adapters.profile``）。"""
    descriptor = _descriptor(contract, target)
    descriptor_wire = descriptor.to_dict()
    pinned = target.pin is not None
    requested_conditions = {
        "adapter": (
            _known(descriptor_wire["adapter"])
            if pinned
            else _unknown("adapter-preference-not-pinned")
        ),
        "model": (
            _known(descriptor_wire["model"]) if pinned else _unknown("model-preference-not-pinned")
        ),
        "effort": (
            _known(contract.requested_effort)
            if contract.requested_effort is not None
            else _unknown("native-effort-not-observed")
        ),
        "loadout": _unknown("loadout-not-observed"),
        "toolset": _unknown("toolset-not-observed"),
        "sandbox": _unknown("sandbox-not-observed"),
        "permissions": _unknown("permissions-not-observed"),
        "toolchain": _unknown("toolchain-not-observed"),
    }
    resolved_values = contract.resolved_conditions()
    resolved_conditions = {
        "adapter": _known(descriptor_wire["adapter"]),
        "model": _known(descriptor_wire["model"]),
        "effort": _known(contract.effort),
        **{name: _known(value) for name, value in resolved_values.items()},
    }
    source = [{"kind": "profile-source", "ref": DISPATCH_PROFILE_SOURCE}]
    requirements = _requirements(target, plane="resolved")

    observed_provenance = list(source)
    observed_metadata: dict[str, object] = {}
    if thread is not None:
        observed_provenance.append({"kind": "observer", "ref": thread.source})
        observed_metadata["evidence_refs"] = [dict(item) for item in thread.evidence]
    if launch is not None:
        observed_provenance.append({"kind": "observer", "ref": LAUNCH_OBSERVER})
        # metadata 只允許 schema 列舉的欄位；發射事實放在 discovery 下（不進任何 key）。
        observed_metadata["discovery"] = {"launch": dict(launch.facts)}

    def profile(plane, conditions, *, provenance, metadata):
        return parse_profile(
            {
                "schema_version": 1,
                "plane": plane,
                "conditions": conditions,
                "requirements": requirements,
                "provenance": provenance,
                "metadata": metadata,
            },
            descriptor,
        )

    try:
        requested = profile(
            "requested",
            requested_conditions,
            provenance=source,
            metadata={},
        )
        resolved = profile("resolved", resolved_conditions, provenance=source, metadata={})
        observed = profile(
            "observed",
            _observed_conditions(contract, descriptor, launch, thread),
            provenance=observed_provenance,
            metadata=observed_metadata,
        )
    except ExecutionProfileError as exc:
        raise DispatchProfileError(f"dispatch profile 無法組成：{exc.code}") from exc
    return {
        "schema_version": 1,
        "descriptor": descriptor_wire,
        "profile_id": profile_key(resolved),
        "requested": requested.to_dict(),
        "requested_key": profile_key(requested),
        "resolved": resolved.to_dict(),
        "resolved_key": profile_key(resolved),
        "observed": observed.to_dict(),
        "observed_key": profile_key(observed),
        "actual_condition_key": actual_condition_key(observed),
    }
