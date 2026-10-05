"""Cortex 目標派工（dispatch target）：Manager 派工當下的輸入，PatchMUD 的唯一外部輸入。

一份 target 描述 Cortex Manager 在 ``manager._bind_workflow_execution_profile(run, step,
identity, launcher)`` 收到的那組輸入：被選中的 identity、persona／卡片契約、launcher 的
effort，以及 run 的 sizing band、builder pin 與先前的 build steps。PatchMUD 只在
**實際量測條件確實符合**時才以它組 profile：

- 只支援 ``builder`` persona、``phase=build``、``commit_policy=required`` 的卡片——
  builder lane 的候選只取 commit 出來的 HEAD，其他 commit 契約沒有對應的量測方式，
  一律拒收（不猜）。
- requirements 的推導規則逐字比照 ``_bind_workflow_execution_profile``：
  ``independence = {selected_domain, builder_domains}``，``builder_domains`` 取先前
  ``phase=build``、``gate_result=passed``、``commit_policy != forbidden`` 且 domain
  非空的 steps；``pin = model_chain_override[persona]``；``minimum_quality =
  {sizing_band}``。role 不從輸入取，由 builder lane 固定為 ``build``。
- target 檔可附 ``cortex_resolved_key``（Cortex 真實程式算出的 key，供稽核），
  PatchMUD **不讀它產 key**；key 一律由發射契約重算。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

__all__ = [
    "SIZING_BANDS",
    "TARGET_SCHEMA",
    "DispatchCard",
    "DispatchIdentity",
    "DispatchTarget",
    "DispatchTargetError",
    "PriorStep",
    "load_dispatch_target",
    "parse_dispatch_target",
]

TARGET_SCHEMA = "patchmud.cortex-dispatch-target/v1"
#: Cortex ``claim.BAND_LEVELS``（sizing band 值域）。
SIZING_BANDS = ("green", "yellow", "red")
#: builder lane 唯一支援的 persona 與其 Cortex runtime role。
BUILDER_PERSONA = "builder"
BUILD_ROLE = "build"

_TOP_KEYS = frozenset(
    {
        "schema",
        "name",
        "description",
        "cortex_source_revision",
        "cortex_resolved_key",
        "cortex_request_key",
        "identity",
        "persona",
        "combo",
        "card",
        "launcher",
        "run",
    }
)
_REQUIRED_TOP = frozenset({"schema", "name", "identity", "persona", "card", "launcher", "run"})
_IDENTITY_KEYS = frozenset({"executor", "model_id", "independence_domain", "capabilities"})
_CARD_KEYS = frozenset(
    {"id", "phase", "commit_policy", "test_policy", "declared_outputs", "action"}
)
_LAUNCHER_KEYS = frozenset({"allow_unsafe", "effort"})
_RUN_KEYS = frozenset({"sizing_band", "model_chain_override", "prior_steps"})
_STEP_KEYS = frozenset({"card", "phase", "gate_result", "commit_policy", "domain"})
_PIN_KEYS = frozenset({"executor", "model_id"})


class DispatchTargetError(ValueError):
    """dispatch target 不合法，或描述的派工不是 builder lane 能如實量測的條件。"""


@dataclass(frozen=True)
class DispatchIdentity:
    executor: str
    model_id: str
    independence_domain: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class DispatchCard:
    id: str
    phase: str
    commit_policy: str
    test_policy: str | None
    declared_outputs: tuple[str, ...]
    action: str


@dataclass(frozen=True)
class PriorStep:
    card: str
    phase: str
    gate_result: str | None
    commit_policy: str | None
    domain: str | None


@dataclass(frozen=True)
class DispatchTarget:
    """已驗證的目標派工。``sha256`` 是 target 檔 canonical JSON 的 digest（稽核用）。"""

    name: str
    identity: DispatchIdentity
    persona: str
    combo: str | None
    card: DispatchCard
    launcher_effort: str | None
    sizing_band: str
    pin: Mapping[str, str] | None
    prior_steps: tuple[PriorStep, ...]
    cortex_source_revision: str | None
    cortex_resolved_key: str | None
    cortex_request_key: str | None
    sha256: str

    @property
    def role(self) -> str:
        return BUILD_ROLE

    @property
    def builder_domains(self) -> tuple[str, ...]:
        """逐字比照 ``_bind_workflow_execution_profile`` 的 ``builder_domains``。"""
        return tuple(
            sorted(
                {
                    step.domain
                    for step in self.prior_steps
                    if step.phase == "build"
                    and step.gate_result == "passed"
                    and step.commit_policy != "forbidden"
                    and step.domain is not None
                }
            )
        )

    def requirements(self) -> dict[str, object]:
        """Manager 送進 ``make_launcher_profile(requirements=...)`` 的那一份。"""
        requirements: dict[str, object] = {
            "independence": {
                "selected_domain": self.identity.independence_domain,
                "builder_domains": list(self.builder_domains),
            }
        }
        if self.pin is not None:
            requirements["pin"] = dict(self.pin)
        requirements["minimum_quality"] = {"sizing_band": self.sizing_band}
        return requirements

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "cortex_source_revision": self.cortex_source_revision,
            "cortex_resolved_key": self.cortex_resolved_key,
            "identity": {
                "executor": self.identity.executor,
                "model_id": self.identity.model_id,
                "independence_domain": self.identity.independence_domain,
                "capabilities": list(self.identity.capabilities),
            },
            "persona": self.persona,
            "combo": self.combo,
            "card": {
                "id": self.card.id,
                "phase": self.card.phase,
                "commit_policy": self.card.commit_policy,
                "test_policy": self.card.test_policy,
            },
            "launcher_effort": self.launcher_effort,
            "sizing_band": self.sizing_band,
            "pin": dict(self.pin) if self.pin is not None else None,
            "builder_domains": list(self.builder_domains),
        }


def _text(value: object, locator: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise DispatchTargetError(f"dispatch target {locator} 必須是非空字串")
    return value


def _optional_text(value: object, locator: str) -> str | None:
    if value is None:
        return None
    return _text(value, locator)


def _mapping(value: object, locator: str, *, required: frozenset[str], allowed: frozenset[str]) -> Mapping:
    if not isinstance(value, Mapping):
        raise DispatchTargetError(f"dispatch target {locator} 必須是 object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DispatchTargetError(f"dispatch target {locator} 有未知欄位：{unknown}")
    missing = sorted(required - set(value))
    if missing:
        raise DispatchTargetError(f"dispatch target {locator} 缺欄位：{missing}")
    return value


def _canonical_sha256(payload: object) -> str:
    data = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def parse_dispatch_target(payload: object) -> DispatchTarget:
    """嚴格解析 target；不是 builder lane 能如實量測的派工一律拒收。"""
    top = _mapping(payload, "document", required=_REQUIRED_TOP, allowed=_TOP_KEYS)
    if top["schema"] != TARGET_SCHEMA:
        raise DispatchTargetError(f"dispatch target schema 不支援：{top['schema']!r}")
    name = _text(top["name"], "name")

    raw_identity = _mapping(
        top["identity"], "identity", required=_IDENTITY_KEYS, allowed=_IDENTITY_KEYS
    )
    capabilities = raw_identity["capabilities"]
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) and item for item in capabilities
    ):
        raise DispatchTargetError("dispatch target identity.capabilities 必須是字串清單")
    identity = DispatchIdentity(
        executor=_text(raw_identity["executor"], "identity.executor"),
        model_id=_text(raw_identity["model_id"], "identity.model_id"),
        independence_domain=_text(
            raw_identity["independence_domain"], "identity.independence_domain"
        ),
        capabilities=tuple(capabilities),
    )
    if identity.executor != "codex":
        # builder lane 只有 codex 有 Cortex argv 移植與 provider 觀測來源。
        raise DispatchTargetError(
            f"builder lane 目前只支援 codex executor：{identity.executor!r}"
        )
    if BUILD_ROLE not in identity.capabilities:
        raise DispatchTargetError("identity 缺 build capability，Cortex 會拒絕這次派工")

    persona = _text(top["persona"], "persona")
    if persona != BUILDER_PERSONA:
        raise DispatchTargetError(f"builder lane 只量測 builder persona：{persona!r}")

    raw_card = _mapping(
        top["card"],
        "card",
        required=frozenset({"id", "phase", "commit_policy", "action"}),
        allowed=_CARD_KEYS,
    )
    outputs = raw_card.get("declared_outputs", [])
    if not isinstance(outputs, list) or not all(isinstance(item, str) for item in outputs):
        raise DispatchTargetError("dispatch target card.declared_outputs 必須是字串清單")
    card = DispatchCard(
        id=_text(raw_card["id"], "card.id"),
        phase=_text(raw_card["phase"], "card.phase"),
        commit_policy=_text(raw_card["commit_policy"], "card.commit_policy"),
        test_policy=_optional_text(raw_card.get("test_policy"), "card.test_policy"),
        declared_outputs=tuple(outputs),
        action=_text(raw_card["action"], "card.action"),
    )
    if card.phase != "build":
        raise DispatchTargetError(f"builder lane 只支援 build phase 卡片：{card.phase!r}")
    if card.commit_policy != "required":
        raise DispatchTargetError(
            "builder lane 只支援 commit_policy=required 的卡片（候選只取 commit 出來的 HEAD）："
            f"{card.commit_policy!r}"
        )

    raw_launcher = _mapping(
        top["launcher"], "launcher", required=_LAUNCHER_KEYS, allowed=_LAUNCHER_KEYS
    )
    if raw_launcher["allow_unsafe"] is not False:
        raise DispatchTargetError("builder lane 不支援 allow_unsafe 派工（沙箱旁路沒有對應的量測條件）")
    launcher_effort = _optional_text(raw_launcher["effort"], "launcher.effort")

    raw_run = _mapping(top["run"], "run", required=_RUN_KEYS, allowed=_RUN_KEYS)
    sizing_band = raw_run["sizing_band"]
    if sizing_band not in SIZING_BANDS:
        # sizing_band 為 None 時 Cortex 根本不查 qualification；不給預設值。
        raise DispatchTargetError(
            f"dispatch target run.sizing_band 必須明示為 {list(SIZING_BANDS)} 之一：{sizing_band!r}"
        )
    overrides = raw_run["model_chain_override"]
    if not isinstance(overrides, Mapping):
        raise DispatchTargetError("dispatch target run.model_chain_override 必須是 object")
    raw_pin = overrides.get(persona)
    pin: Mapping[str, str] | None = None
    if raw_pin is not None:
        pin_map = _mapping(raw_pin, f"run.model_chain_override.{persona}", required=_PIN_KEYS, allowed=_PIN_KEYS)
        pin = MappingProxyType(
            {
                "executor": _text(pin_map["executor"], "pin.executor"),
                "model_id": _text(pin_map["model_id"], "pin.model_id"),
            }
        )
        if (pin["executor"], pin["model_id"]) != (identity.executor, identity.model_id):
            raise DispatchTargetError(
                "builder pin 與實際量測的 executor／model 不符（Cortex 會以 explicit model pin 拒絕）"
            )

    raw_steps = raw_run["prior_steps"]
    if not isinstance(raw_steps, list):
        raise DispatchTargetError("dispatch target run.prior_steps 必須是清單")
    prior_steps = []
    for index, raw_step in enumerate(raw_steps):
        step = _mapping(raw_step, f"run.prior_steps[{index}]", required=_STEP_KEYS, allowed=_STEP_KEYS)
        prior_steps.append(
            PriorStep(
                card=_text(step["card"], f"run.prior_steps[{index}].card"),
                phase=_text(step["phase"], f"run.prior_steps[{index}].phase"),
                gate_result=_optional_text(step["gate_result"], f"run.prior_steps[{index}].gate_result"),
                commit_policy=_optional_text(
                    step["commit_policy"], f"run.prior_steps[{index}].commit_policy"
                ),
                domain=_optional_text(step["domain"], f"run.prior_steps[{index}].domain"),
            )
        )

    return DispatchTarget(
        name=name,
        identity=identity,
        persona=persona,
        combo=_optional_text(top.get("combo"), "combo"),
        card=card,
        launcher_effort=launcher_effort,
        sizing_band=sizing_band,
        pin=pin,
        prior_steps=tuple(prior_steps),
        cortex_source_revision=_optional_text(
            top.get("cortex_source_revision"), "cortex_source_revision"
        ),
        cortex_resolved_key=_optional_text(top.get("cortex_resolved_key"), "cortex_resolved_key"),
        cortex_request_key=_optional_text(top.get("cortex_request_key"), "cortex_request_key"),
        sha256=_canonical_sha256(payload),
    )


def load_dispatch_target(path: str | Path) -> DispatchTarget:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DispatchTargetError(f"dispatch target 無法讀取：{exc}") from exc
    return parse_dispatch_target(payload)
