"""Controlled model execution for the JEV scoring profile.

The legacy PatchMUD loop is intentionally left untouched.  Scoring uses a
small, public action protocol so a coding model can describe work while the
engine owns every filesystem mutation and every candidate-code execution.
Model adapters only complete text; :class:`IsolationRunner` is the sole seam
through which candidate code is run.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from patchmud.adapters.agy_cli import AgyCliAdapter
from patchmud.adapters.base import AdapterError, AdapterResponse, ModelAdapter
from patchmud.adapters.codex_cli import CodexCliAdapter
from patchmud.deck.materialize import materialize_repo
from patchmud.sandbox.isolate import Execution, IsolationRunner
from patchmud.sandbox.workspace import HARNESS_CONFIG_NAMES, Workspace, WorkspaceError

__all__ = [
    "build_scoring_adapter",
    "execute_case",
    "resolve_profile",
]


PROFILE_PROTOCOL_VERSION = "controlled-engineering-v1"
CONTROLLED_EXECUTION_MODE = "completion-only"
_SUPPORTED_HARNESSES = frozenset({"codex", "agy"})
_SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "ultra"})
_AGY_EFFORT_SUFFIXES = tuple(f"-{effort}" for effort in _SUPPORTED_EFFORTS)
_CODEX_CACHE = Path(
    os.environ.get("PATCHMUD_CODEX_MODELS_CACHE", "~/.codex/models_cache.json")
).expanduser()

_DEPTH_BUDGETS: dict[int, tuple[int, int]] = {
    1: (8, 600),
    2: (16, 1200),
    3: (24, 1800),
}

# The scoring profiles are deliberately a closed, versioned selection.  The
# Codex cache is consulted when present, but tests and installed offline runs
# do not depend on a user's home directory.  A caller may pass a catalog to
# ``resolve_profile`` for a provider descriptor captured by its own harness.
_BUILTIN_CATALOG: dict[str, dict[str, set[str]]] = {
    "codex": {
        "gpt-5.6-luna": {"low", "medium", "high", "xhigh", "max"},
        "gpt-5.6-sol": {"low", "medium", "high", "xhigh", "max", "ultra"},
        "gpt-5.6-terra": {"low", "medium", "high", "xhigh", "max", "ultra"},
    },
    "agy": {
        "gemini-3.8-flash": {"high"},
        "gemini-3.6-flash": {"high"},
        "gemini-3.1-pro": {"high"},
    },
}


def resolve_profile(
    harness: str,
    model: str,
    effort: str,
    *,
    catalog: Mapping[str, Any] | None = None,
) -> dict:
    """Resolve an explicit provider profile without contacting a model service.

    ``requested`` is exactly what the caller supplied.  ``resolved`` is the
    provider invocation identity (agy encodes native effort in its model id),
    while ``observed`` starts unknown and is filled only by a real provider
    integration.  Keeping those mappings separate prevents a requested value
    from being reported as an observed provider fact.
    """

    requested_harness = str(harness).strip().lower()
    requested_model = str(model).strip()
    requested_effort = str(effort).strip().lower()
    if requested_harness not in _SUPPORTED_HARNESSES:
        raise ValueError(f"unsupported scoring harness: {harness!r}")
    if not requested_model:
        raise ValueError("scoring model must be non-empty")
    if requested_effort not in _SUPPORTED_EFFORTS:
        raise ValueError(f"unsupported scoring effort: {effort!r}")

    available = _available_catalog(catalog)
    base_model = _base_model_id(requested_harness, requested_model)
    supported = available.get(requested_harness, {}).get(base_model)
    if supported is None:
        raise ValueError(
            f"{requested_harness} model is not in the verified scoring catalog: {requested_model!r}"
        )
    if requested_effort not in supported:
        raise ValueError(
            f"{requested_harness} model {base_model!r} does not support effort {requested_effort!r}"
        )

    if requested_harness == "codex":
        resolved_model = requested_model
        harness_name = "codex-cli-controlled-v1"
    else:
        resolved_model = _resolve_agy_model(requested_model, requested_effort)
        harness_name = "agy-cli-controlled-v1"

    identity = _executable_identity(requested_harness, shutil.which(requested_harness))
    catalog_source, model_verified = _catalog_confirmation(
        catalog,
        requested_harness,
        base_model,
        requested_effort,
        runtime_identity_known=identity["known"],
    )
    controlled_supported = _controlled_descriptor(
        catalog, requested_harness, base_model
    )
    runtime_version = identity["version"] or "unknown"
    harness_version = f"{harness_name}:{runtime_version}"

    requested = {
        "harness": requested_harness,
        "model": requested_model,
        "effort": requested_effort,
    }
    resolved = {
        "harness": requested_harness,
        "model": resolved_model,
        # Keep this explicit even when the native model id contains a suffix:
        # the CLI receives the requested effort separately as well.
        "effort": requested_effort,
    }
    observed = {"harness": requested_harness, "model": None, "effort": None}
    return {
        "requested": requested,
        "resolved": resolved,
        "observed": observed,
        "harness_version": harness_version,
        "execution_mode": CONTROLLED_EXECUTION_MODE,
        "protocol_version": PROFILE_PROTOCOL_VERSION,
        "capability": {
            "catalog": catalog_source,
            "model_verified": model_verified,
            # ``executable`` is the actual provider runtime.  Codex's PATH
            # entry is a JavaScript launcher; its native binary is pinned when
            # the launcher package exposes it.
            "executable": identity["runtime_executable"],
            "launcher": identity["launcher"],
            "executable_sha256": identity["sha256"],
            "executable_version": identity["version"],
            "runtime_identity_known": identity["known"],
            # Neither installed CLI exposes a strict native-tools-off switch.
            # Keep this explicit so the root preflight refuses live scoring
            # until an approved external isolation policy is selected.
            "controlled_supported": controlled_supported,
            "native_tools_disabled": False,
            # A profile without a pinned runtime, confirmed model descriptor,
            # and approved controlled mode cannot populate a reusable baseline.
            "cacheable": bool(
                identity["known"] and model_verified and controlled_supported
            ),
        },
    }


def _executable_identity(harness: str, executable: str | None) -> dict[str, Any]:
    launcher = str(Path(executable).resolve()) if executable else None
    runtime = _runtime_executable(harness, launcher)
    digest = _hash_executable(runtime)
    version = _runtime_version(runtime)
    return {
        "launcher": launcher,
        "runtime_executable": runtime,
        "sha256": digest,
        "version": version,
        "known": bool(runtime and digest and version),
    }


def _runtime_executable(harness: str, launcher: str | None) -> str | None:
    if launcher is None:
        return None
    path = Path(launcher)
    if harness != "codex" or path.suffix.lower() != ".js":
        return launcher if path.is_file() else None
    # The npm Codex entrypoint delegates to the platform package's native
    # binary.  Hash that binary, rather than the stable JavaScript launcher.
    package_root = path.parent.parent
    vendor_root = package_root / "node_modules" / "@openai"
    candidates = sorted(vendor_root.glob("codex-*/vendor/*/bin/codex"))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def _runtime_version(executable: str | None) -> str | None:
    if executable is None:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="patchmud-version-") as cwd:
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            }
            completed = subprocess.run(
                [executable, "--version"],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    line = (completed.stdout or completed.stderr).strip().splitlines()
    return re.sub(r"\s+", " ", line[0])[:120] if line else None


def _hash_executable(executable: str | None) -> str | None:
    if not executable:
        return None
    try:
        digest = hashlib.sha256()
        with Path(executable).resolve().open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _catalog_confirmation(
    catalog: Mapping[str, Any] | None,
    harness: str,
    model: str,
    effort: str,
    *,
    runtime_identity_known: bool,
) -> tuple[str, bool]:
    if catalog is not None:
        return "injected", True
    if not runtime_identity_known:
        return "unverified-runtime", False
    if harness == "codex" and _codex_cache_confirms(model, effort):
        return "local-cache", True
    return "bundled-unverified", False


def _codex_cache_confirms(model: str, effort: str) -> bool:
    try:
        payload = json.loads(_CODEX_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    records = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(records, list):
        return False
    for record in records:
        if not isinstance(record, Mapping) or record.get("slug") != model:
            continue
        levels = record.get("supported_reasoning_levels")
        if not isinstance(levels, list):
            continue
        return any(
            isinstance(level, Mapping) and level.get("effort") == effort
            for level in levels
        )
    return False


def _controlled_descriptor(
    catalog: Mapping[str, Any] | None, harness: str, model: str
) -> bool:
    if catalog is None:
        return False
    records = catalog.get(harness)
    if not isinstance(records, Mapping):
        return False
    descriptor = records.get(model)
    return isinstance(descriptor, Mapping) and descriptor.get("controlled_supported") is True


def _base_model_id(harness: str, model: str) -> str:
    if harness != "agy":
        return model
    for suffix in _AGY_EFFORT_SUFFIXES:
        if model.endswith(suffix):
            return model[: -len(suffix)]
    return model


def _available_catalog(catalog: Mapping[str, Any] | None) -> dict[str, dict[str, set[str]]]:
    if catalog is None:
        merged: dict[str, dict[str, set[str]]] = {
            harness: {model: set(efforts) for model, efforts in records.items()}
            for harness, records in _BUILTIN_CATALOG.items()
        }
        _merge_codex_cache(merged)
        return merged
    normalized: dict[str, dict[str, set[str]]] = {"codex": {}, "agy": {}}
    for harness, records in catalog.items():
        if harness not in normalized or not isinstance(records, Mapping):
            continue
        for model, descriptor in records.items():
            efforts: Any = descriptor
            if isinstance(descriptor, Mapping):
                efforts = descriptor.get("efforts", descriptor.get("supported_reasoning_levels", ()))
            if isinstance(efforts, str):
                efforts = [efforts]
            if isinstance(efforts, Sequence):
                values = {
                    item.get("effort") if isinstance(item, Mapping) else item
                    for item in efforts
                }
                normalized[harness][str(model)] = {value for value in values if isinstance(value, str)}
    return normalized


def _merge_codex_cache(catalog: dict[str, dict[str, set[str]]]) -> None:
    try:
        payload = json.loads(_CODEX_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return
    models = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(models, list):
        return
    for item in models:
        if not isinstance(item, Mapping) or not isinstance(item.get("slug"), str):
            continue
        levels = item.get("supported_reasoning_levels")
        if not isinstance(levels, list):
            continue
        efforts = {
            level.get("effort")
            for level in levels
            if isinstance(level, Mapping) and isinstance(level.get("effort"), str)
        }
        if efforts:
            catalog["codex"][item["slug"]] = efforts


def _resolve_agy_model(model: str, effort: str) -> str:
    """Return agy's effort-specific id without stacking duplicate suffixes."""

    base = model
    for suffix in _AGY_EFFORT_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if not base:
        raise ValueError(f"invalid agy model id: {model!r}")
    return f"{base}-{effort}"


def build_scoring_adapter(profile: Mapping[str, Any], *, timeout_s: float = 600) -> ModelAdapter:
    """Build a completion-only CLI adapter from a resolved profile.

    Codex is pinned to read-only, ephemeral, user-config-free completion with
    plugins/memories/goals/hooks disabled.  Agy is pinned to print mode with
    slash commands disabled and its terminal sandbox enabled.  Neither adapter
    receives the candidate workspace; candidate execution remains owned by the
    scoring runner and ``IsolationRunner``.
    """

    try:
        requested = profile["requested"]
        resolved = profile["resolved"]
        capability = profile.get("capability", {})
        harness = str(resolved["harness"])
        model = str(resolved["model"])
        # Passing requested effort is deliberate: it remains visible in both
        # the profile and the provider argv even when agy also suffixes model.
        effort = str(requested["effort"])
    except (KeyError, TypeError) as exc:
        raise ValueError("invalid scoring profile") from exc
    if harness not in _SUPPORTED_HARNESSES:
        raise ValueError(f"unsupported scoring harness: {harness!r}")
    if timeout_s <= 0:
        raise ValueError("adapter timeout must be positive")
    if isinstance(capability, Mapping) and capability.get("controlled_supported") is False:
        raise AdapterError(
            f"{harness} cannot prove native-tools-off completion for controlled scoring"
        )

    runtime_executable = (
        capability.get("executable") if isinstance(capability, Mapping) else None
    )
    binary_kwargs = {}
    if isinstance(runtime_executable, str) and runtime_executable:
        binary_kwargs["codex_binary" if harness == "codex" else "agy_binary"] = (
            runtime_executable
        )

    if harness == "codex":
        return CodexCliAdapter(
            model=model,
            effort=effort,
            timeout_s=float(timeout_s),
            controlled=True,
            **binary_kwargs,
        )
    return AgyCliAdapter(
        model=model,
        effort=effort,
        timeout_s=float(timeout_s),
        controlled=True,
        **binary_kwargs,
    )


@dataclass(frozen=True)
class _Action:
    keyword: str
    target: str | None = None
    payload: str = ""
    report: str | None = None


class _ProtocolError(ValueError):
    pass


def _parse_controlled_reply(text: str) -> _Action:
    """Parse scoring's local multiline action/report protocol.

    This parser deliberately lives outside ``patchmud.engine.protocol``: the
    scoring profile accepts final multiline ``REPORT`` content and does not
    alter the frozen pilot protocol.
    """

    if not isinstance(text, str):
        raise _ProtocolError("model response must be text")
    lines = text.splitlines()
    action_index = next(
        (index for index, line in enumerate(lines) if line.strip().startswith("ACTION:")),
        None,
    )
    if action_index is None:
        raise _ProtocolError("missing ACTION header")
    action_spec = lines[action_index].strip()[len("ACTION:") :].strip()
    if not action_spec:
        raise _ProtocolError("empty ACTION header")

    marker_index: int | None = None
    marker_name: str | None = None
    marker_value = ""
    for index in range(action_index + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("PATCH:"):
            marker_index, marker_name, marker_value = index, "PATCH", stripped[6:].strip()
            break
        if stripped.startswith("REPORT:"):
            marker_index, marker_name, marker_value = index, "REPORT", stripped[7:].strip()
            break
    body = lines[marker_index + 1 :] if marker_index is not None else []
    if marker_value:
        body = [marker_value, *body]
    body = _strip_fence(body)
    payload = "\n".join(body)
    if marker_name == "PATCH" and payload and not payload.endswith("\n"):
        payload += "\n"
    report = payload if marker_name == "REPORT" else None

    if action_spec == "LOOK":
        return _Action("LOOK")
    if action_spec == "INSPECT" or action_spec.startswith("INSPECT "):
        target = action_spec[len("INSPECT") :].strip()
        if not target:
            raise _ProtocolError("INSPECT requires a path")
        return _Action("INSPECT", target=target)
    if action_spec == "PATCH":
        if marker_name != "PATCH" or not payload.strip():
            raise _ProtocolError("PATCH requires a non-empty diff")
        return _Action("PATCH", payload=payload)
    if action_spec == "WRITE_TEST":
        if marker_name != "PATCH" or not payload.strip():
            raise _ProtocolError("WRITE_TEST requires a non-empty diff")
        return _Action("WRITE_TEST", payload=payload)
    if action_spec == "RUN_TEST" or action_spec.startswith("RUN_TEST "):
        target = action_spec[len("RUN_TEST") :].strip() or None
        if marker_name is not None:
            raise _ProtocolError("RUN_TEST does not accept a payload")
        return _Action("RUN_TEST", target=target)
    if action_spec == "ROLLBACK":
        if marker_name is not None:
            raise _ProtocolError("ROLLBACK does not accept a payload")
        return _Action("ROLLBACK")
    if action_spec == "COMMIT":
        if marker_name == "PATCH":
            raise _ProtocolError("COMMIT cannot contain a patch payload")
        return _Action("COMMIT", report=report)
    if action_spec == "REPORT":
        if marker_name != "REPORT" or not report or not report.strip():
            raise _ProtocolError("REPORT requires multiline report content")
        return _Action("REPORT", report=report)
    raise _ProtocolError(f"unknown ACTION: {action_spec}")


def _strip_fence(lines: list[str]) -> list[str]:
    result = list(lines)
    while result and not result[0].strip():
        result.pop(0)
    while result and not result[-1].strip():
        result.pop()
    if len(result) >= 2 and result[0].strip().startswith("```") and result[-1].strip() == "```":
        result = result[1:-1]
        while result and not result[0].strip():
            result.pop(0)
        while result and not result[-1].strip():
            result.pop()
    return result


def execute_case(
    case: Mapping[str, Any],
    adapter: ModelAdapter,
    *,
    runner_factory: Callable[[Path], Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
    artifact_dir: Path | None = None,
) -> dict:
    """Execute one public case through the controlled action surface.

    The returned dictionary is intentionally JSON-compatible.  Errors are
    infrastructure outcomes and carry no score; model protocol failures and
    wall/turn budget exhaustion remain judgeable execution outcomes.
    """

    case_id = str(case.get("id", "unknown"))
    started = clock()
    max_turns, wall_seconds = _case_budget(case)
    result = _empty_result(case_id)
    workspace: Workspace | None = None
    runner: Any = None
    messages: list[dict] = []
    stage_cursor = 0
    stages = _normalise_stages(case.get("stages", ()))
    final_status: str | None = None
    end_reason: str | None = None
    temp_root_path: Path | None = None

    try:
        fixture_dir = Path(str(case["fixture_dir"])).resolve()
        temp_root = tempfile.mkdtemp(prefix="patchmud-scoring-")
        temp_root_path = Path(temp_root)
        frozen = materialize_repo(fixture_dir, temp_root_path / "worktree")
        workspace = Workspace(
            frozen=frozen,
            encounter_dir=fixture_dir,
            shadow_dir=temp_root_path / "shadow",
        )
        runner = (
            runner_factory(workspace.worktree)
            if runner_factory is not None
            else _build_isolation_runner(workspace.worktree)
        )
        if runner_factory is None:
            capabilities = runner.capabilities()
            if not (
                capabilities.mount_ns
                and capabilities.net_ns
                and capabilities.pid_ns
            ):
                raise WorkspaceError(
                    "IsolationRunner namespace capabilities unavailable"
                )

        messages.extend(_initial_messages(case))
        stage_cursor = _append_due_stages(
            stages, stage_cursor, after_turn=0, messages=messages, result=result
        )

        while final_status is None:
            elapsed = max(0.0, clock() - started)
            remaining = wall_seconds - elapsed
            if remaining <= 0:
                final_status, end_reason = "budget_exhausted", "wall_clock"
                break
            if result["turns"] >= max_turns:
                final_status, end_reason = "budget_exhausted", "max_turns"
                break

            turn = int(result["turns"]) + 1
            try:
                response = _complete_with_remaining(adapter, messages, remaining)
                _record_usage(result, turn, response)
            except Exception as exc:  # adapter boundary is fail-closed
                _record_failed_usage(result, turn, exc)
                result["turns"] = turn
                if _is_timeout_error(exc, clock(), started, wall_seconds):
                    final_status, end_reason = "budget_exhausted", "wall_clock"
                else:
                    final_status, end_reason = "error", "model_adapter"
                    result["error"] = _sanitize_error(exc, fixture_dir)
                break
            except BaseException as exc:
                # Preserve a provider's partial usage metadata when cancellation
                # interrupts a completion.  The outer handler turns this into
                # an archivable interrupted result and stops further calls.
                _record_failed_usage(result, turn, exc)
                result["turns"] = turn
                raise

            result["turns"] = turn
            reply = response.text
            messages.append({"role": "assistant", "content": reply, "turn": turn})
            remaining_after_call = _remaining(clock, started, wall_seconds)
            if remaining_after_call <= 0:
                _emit_event(
                    result,
                    messages,
                    turn=turn,
                    action="BUDGET",
                    kind="budget",
                    status="error",
                    output={"reason": "wall budget exhausted during model completion"},
                )
                final_status, end_reason = "budget_exhausted", "wall_clock"
                break
            # Parsing and public transcript bookkeeping also consume wall
            # time.  Re-clamp before any action can invoke a candidate
            # process, rather than handing it the pre-parse remainder.
            remaining_for_action = _remaining(clock, started, wall_seconds)
            if remaining_for_action <= 0:
                _emit_event(
                    result,
                    messages,
                    turn=turn,
                    action="BUDGET",
                    kind="budget",
                    status="error",
                    output={"reason": "wall budget exhausted before controlled action"},
                )
                final_status, end_reason = "budget_exhausted", "wall_clock"
                break
            try:
                action = _parse_controlled_reply(reply)
            except _ProtocolError as exc:
                event = _emit_event(
                    result,
                    messages,
                    turn=turn,
                    action="PROTOCOL",
                    kind="protocol_error",
                    status="error",
                    output=str(exc),
                )
                result["transcript"] = messages
                result.setdefault("_invalid_streak", 0)
                result["_invalid_streak"] += 1
                stage_cursor = _append_due_stages(
                    stages,
                    stage_cursor,
                    after_turn=turn,
                    messages=messages,
                    result=result,
                )
                if result["_invalid_streak"] >= 3:
                    final_status, end_reason = "protocol_failed", "protocol"
                continue

            result.pop("_invalid_streak", None)
            try:
                terminal = _perform_action(
                    action,
                    case=case,
                    workspace=workspace,
                    runner=runner,
                    remaining=remaining_for_action,
                    turn=turn,
                    result=result,
                    messages=messages,
                )
            except Exception as exc:
                final_status, end_reason = "error", "execution"
                result["error"] = _sanitize_error(exc, fixture_dir)
                _emit_event(
                    result,
                    messages,
                    turn=turn,
                    action=action.keyword,
                    kind="execution_error",
                    status="error",
                    output=result["error"],
                )
                break

            if action.report is not None:
                result["final_report"] = action.report.strip()
            if terminal:
                final_status = "completed"
                end_reason = "commit" if action.keyword == "COMMIT" else "report"
            stage_cursor = _append_due_stages(
                stages,
                stage_cursor,
                after_turn=turn,
                messages=messages,
                result=result,
            )

        # A terminal COMMIT/REPORT gets one final objective test when a
        # case provides a command and wall budget remains.  A previous
        # RUN_TEST result is preserved; this final call captures the
        # state represented by final_diff.
        if final_status == "completed" and workspace is not None and runner is not None:
            if case.get("test_argv") and _remaining(clock, started, wall_seconds) > 0:
                _run_test(
                    case,
                    workspace,
                    runner,
                    target=None,
                    remaining=_remaining(clock, started, wall_seconds),
                    turn=int(result["turns"]),
                    result=result,
                    messages=messages,
                    final=True,
                )

        if final_status is not None and stage_cursor < len(stages):
            # A terminal action or exhausted budget can legitimately end
            # before a later public milestone.  Record that fact without
            # pretending the model observed the stage message.
            while stage_cursor < len(stages):
                stage = stages[stage_cursor]
                _emit_event(
                    result,
                    messages,
                    turn=int(result["turns"]),
                    action="STAGE",
                    kind="stage_evidence",
                    status="unavailable",
                    output={"after_turn": stage["after_turn"], "available": False},
                )
                stage_cursor += 1

        if workspace is not None:
            _clean_runtime_files(workspace.worktree)
            result["final_diff"] = workspace.cumulative_diff()
    except Exception as exc:
        was_terminal = final_status is not None
        final_status = "error"
        end_reason = "execution" if was_terminal else (end_reason or "setup")
        result["error"] = _sanitize_error(exc, Path(str(case.get("fixture_dir", ""))))
    except BaseException:
        # Preserve a partial public record on cancellation so the outer
        # orchestrator can archive it and stop the remaining matrix.  This is
        # intentionally an error outcome: no judge may turn interruption into
        # a fabricated zero or a completed score.
        final_status = "error"
        end_reason = "interrupted"
        result["interrupted"] = True
        result["error"] = "interrupted"
        if workspace is not None:
            try:
                _clean_runtime_files(workspace.worktree)
                result["final_diff"] = workspace.cumulative_diff()
            except Exception:
                pass
    finally:
        result["status"] = final_status or "error"
        result["end_reason"] = end_reason or "error"
        result["wall_ms"] = round(max(0.0, clock() - started) * 1000)
        result["transcript"] = messages
        result.pop("_invalid_streak", None)
        if artifact_dir is not None:
            _write_artifact(Path(artifact_dir), case_id, result)
        if temp_root_path is not None:
            shutil.rmtree(temp_root_path, ignore_errors=True)
    return result


def _empty_result(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "status": "error",
        "end_reason": "error",
        "error": None,
        "turns": 0,
        "wall_ms": 0,
        "transcript": [],
        "events": [],
        "final_report": None,
        "final_diff": "",
        "test_results": [],
        "usage": [],
    }


def _case_budget(case: Mapping[str, Any]) -> tuple[int, float]:
    depth = int(case.get("depth", 1))
    default_turns, default_seconds = _DEPTH_BUDGETS.get(depth, _DEPTH_BUDGETS[1])
    try:
        max_turns = int(case.get("max_turns", default_turns))
        wall_seconds = float(case.get("wall_seconds", default_seconds))
    except (TypeError, ValueError) as exc:
        raise ValueError("case budget must be numeric") from exc
    if max_turns <= 0 or wall_seconds <= 0:
        raise ValueError("case budget must be positive")
    return max_turns, wall_seconds


def _initial_messages(case: Mapping[str, Any]) -> list[dict]:
    requirements = case.get("requirements", ())
    requirement_lines = "\n".join(f"- {item}" for item in requirements)
    prompt = str(case.get("prompt", ""))
    allowed_paths = case.get("allowed_paths", ())
    public_contract = {
        "allowed_paths": list(allowed_paths)
        if isinstance(allowed_paths, Sequence) and not isinstance(allowed_paths, (str, bytes))
        else [],
        "max_turns": case.get("max_turns"),
        "wall_seconds": case.get("wall_seconds"),
        "test_argv": list(case.get("test_argv", ()))
        if isinstance(case.get("test_argv"), Sequence)
        and not isinstance(case.get("test_argv"), (str, bytes))
        else None,
        "rubric": case.get("rubric", {}),
    }
    user = (
        f"CASE PROMPT:\n{prompt}\n\n"
        f"PUBLIC REQUIREMENTS:\n{requirement_lines}\n\n"
        "PUBLIC CONTROL CONTRACT:\n"
        f"{json.dumps(public_contract, ensure_ascii=False, sort_keys=True)}\n\n"
        "Use only the controlled actions. The engine applies patches, runs tests, "
        "and returns public evidence. Original fixture tests and harness config "
        "files are immutable; WRITE_TEST may create or edit only tests/agent/**, "
        "and every patch path must match allowed_paths."
    )
    system = (
        "You are a completion-only PatchMUD model. Native tools, shell commands, "
        "file writes, network access, skills, memories, and ambient user state are "
        "unavailable. Reply with exactly one controlled action per turn using these "
        "literal forms:\n"
        "ACTION: LOOK\n"
        "ACTION: INSPECT <path>\n"
        "ACTION: WRITE_TEST\nPATCH:\n<unified diff>\n"
        "ACTION: PATCH\nPATCH:\n<unified diff>\n"
        "ACTION: RUN_TEST [tests/... target]\n"
        "ACTION: ROLLBACK\n"
        "ACTION: COMMIT\nREPORT:\n<multiline public report>\n"
        "A REPORT action may use ACTION: REPORT followed by REPORT:. Do not include "
        "private anchors, hidden asset content, or private reasoning in REPORT."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _normalise_stages(raw: Any) -> list[dict]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    stages: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            continue
        try:
            after_turn = int(item.get("after_turn", 0))
        except (TypeError, ValueError):
            continue
        message = item.get("message")
        if isinstance(message, str) and message:
            stages.append({"after_turn": max(0, after_turn), "message": message, "index": index})
    return sorted(stages, key=lambda item: (item["after_turn"], item["index"]))


def _append_due_stages(
    stages: list[dict],
    cursor: int,
    *,
    after_turn: int,
    messages: list[dict],
    result: dict,
) -> int:
    while cursor < len(stages) and stages[cursor]["after_turn"] <= after_turn:
        stage = stages[cursor]
        _emit_event(
            result,
            messages,
            turn=after_turn,
            action="STAGE",
            kind="stage_evidence",
            status="passed",
            output=stage["message"],
        )
        cursor += 1
    return cursor


def _complete_with_remaining(
    adapter: ModelAdapter, messages: list[dict], remaining: float
) -> AdapterResponse:
    method = getattr(adapter, "complete_with_timeout", None)
    if callable(method):
        response = method(list(messages), max(0.001, remaining))
    else:
        response = adapter.complete(list(messages))
    if not isinstance(response, AdapterResponse):
        raise TypeError("adapter returned an invalid response")
    if not isinstance(response.text, str):
        raise TypeError("adapter response text must be a string")
    if not isinstance(response.usage_raw, dict):
        raise TypeError("adapter usage_raw must be a mapping")
    return response


def _record_usage(result: dict, turn: int, response: AdapterResponse) -> None:
    result["usage"].append(
        {"turn": turn, "status": "completed", "wall_ms": response.wall_ms, "raw": response.usage_raw}
    )


def _record_failed_usage(result: dict, turn: int, exc: BaseException) -> None:
    entry: dict[str, Any] = {"turn": turn, "status": "error", "raw": None}
    raw = getattr(exc, "usage_raw", None)
    if isinstance(raw, dict):
        entry["raw"] = raw
    result["usage"].append(entry)


def _perform_action(
    action: _Action,
    *,
    case: Mapping[str, Any],
    workspace: Workspace,
    runner: Any,
    remaining: float,
    turn: int,
    result: dict,
    messages: list[dict],
) -> bool:
    keyword = action.keyword
    if keyword == "LOOK":
        _emit_event(
            result,
            messages,
            turn=turn,
            action=keyword,
            kind="snapshot",
            status="passed",
            output=_tree_view(workspace.worktree),
        )
        return False
    if keyword == "INSPECT":
        output, status = _inspect(workspace.worktree, action.target or "")
        _emit_event(result, messages, turn=turn, action=keyword, kind="inspect", status=status, output=output)
        return False
    if keyword in {"PATCH", "WRITE_TEST"}:
        apply_kind = "test" if keyword == "WRITE_TEST" else "production"
        illegal = _validate_patch_scope(action.payload, case.get("allowed_paths", ()), apply_kind)
        if illegal is not None:
            _emit_event(
                result,
                messages,
                turn=turn,
                action=keyword,
                kind="diff",
                status="failed",
                output={"applied": False, "reason": illegal},
            )
            return False
        applied = workspace.apply_patch(action.payload, kind=apply_kind)
        status = "passed" if applied.applied else "failed"
        output = {"applied": applied.applied, "reason": applied.reason}
        _emit_event(result, messages, turn=turn, action=keyword, kind="diff", status=status, output=output)
        workspace.checkpoint()
        return False
    if keyword == "RUN_TEST":
        _run_test(
            case,
            workspace,
            runner,
            target=action.target,
            remaining=remaining,
            turn=turn,
            result=result,
            messages=messages,
        )
        return False
    if keyword == "ROLLBACK":
        rolled_back = workspace.rollback()
        status = "passed" if rolled_back else "failed"
        _emit_event(
            result,
            messages,
            turn=turn,
            action=keyword,
            kind="tool",
            status=status,
            output={"rolled_back": rolled_back},
        )
        workspace.checkpoint()
        return False
    if keyword in {"COMMIT", "REPORT"}:
        _emit_event(
            result,
            messages,
            turn=turn,
            action=keyword,
            kind="tool",
            status="passed",
            output={"terminal": True},
        )
        return True
    raise _ProtocolError(f"unsupported controlled action: {keyword}")


def _run_test(
    case: Mapping[str, Any],
    workspace: Workspace,
    runner: Any,
    *,
    target: str | None,
    remaining: float,
    turn: int,
    result: dict,
    messages: list[dict],
    final: bool = False,
) -> None:
    argv = _test_argv(case, target)
    if argv is None:
        output = {"status": "error", "reason": "case has no test_argv"}
        event = _emit_event(result, messages, turn=turn, action="RUN_TEST", kind="test", status="error", output=output)
        result["test_results"].append({"evidence_id": event["evidence_id"], **output})
        return
    if remaining <= 0:
        output = {"status": "error", "reason": "wall budget exhausted"}
        event = _emit_event(result, messages, turn=turn, action="RUN_TEST", kind="test", status="error", output=output)
        result["test_results"].append({"evidence_id": event["evidence_id"], **output})
        return
    _ensure_agent_test_mount(workspace.worktree)
    try:
        execution: Execution = runner.run(argv, cwd=workspace.worktree, timeout_s=remaining)
    except Exception as exc:
        # This is an infrastructure failure, unlike a test process returning
        # non-zero.  Keep it explicit so the judge can refuse a score.
        raise WorkspaceError(f"IsolationRunner failed: {exc}") from exc
    status, reason = _classify_test_execution(execution)
    output: dict[str, Any] = {
        "status": status,
        "target": target,
        "argv": list(argv),
        "exit_code": execution.exit_code,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
        "wall_ms": execution.wall_ms,
        "cpu_ms": execution.cpu_ms,
        "timed_out": execution.timed_out,
        "reason": reason,
        "final": final,
    }
    event = _emit_event(
        result,
        messages,
        turn=turn,
        action="RUN_TEST",
        kind="test",
        status=status,
        output=output,
    )
    result["test_results"].append({"evidence_id": event["evidence_id"], **output})


def _ensure_agent_test_mount(worktree: Path) -> None:
    """Recreate the writable test exception after Workspace rollback/clean."""

    tests_dir = worktree / "tests"
    if tests_dir.is_dir():
        (tests_dir / "agent").mkdir(parents=True, exist_ok=True)


def _test_argv(case: Mapping[str, Any], target: str | None) -> list[str] | None:
    raw = case.get("test_argv")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return None
    argv = [str(value) for value in raw]
    if not argv:
        return None
    if target is None:
        return argv
    pure = PurePosixPath(target)
    if pure.is_absolute() or ".." in pure.parts or not target.startswith("tests/"):
        return None
    return [*argv, target]


def _validate_patch_scope(diff: str, allowed_paths: Any, kind: str) -> str | None:
    """Apply case-owned path policy before ``Workspace`` touches the tree."""

    paths = _diff_paths(diff)
    if not paths:
        return "patch has no file paths"
    patterns = (
        [str(value) for value in allowed_paths]
        if isinstance(allowed_paths, Sequence) and not isinstance(allowed_paths, (str, bytes))
        else []
    )
    for path in paths:
        pure = PurePosixPath(path)
        if pure.is_absolute() or ".." in pure.parts or path.startswith(".git/"):
            return f"illegal patch path: {path}"
        if kind == "test" and not path.startswith("tests/agent/"):
            return f"WRITE_TEST may only touch tests/agent/**: {path}"
        # Existing tests and harness files are immutable in scoring, even if
        # a malformed/overbroad case allowed_paths entry says otherwise.
        if path.startswith("tests/") and not path.startswith("tests/agent/"):
            return f"fixture test path is immutable: {path}"
        if path.rsplit("/", 1)[-1] in {
            "conftest.py",
            "pytest.ini",
            "tox.ini",
            "setup.cfg",
            "pyproject.toml",
            "sitecustomize.py",
            "usercustomize.py",
        }:
            return f"harness file is immutable: {path}"
        if not patterns or not any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
            return f"path outside case allowed_paths: {path}"
    return None


def _diff_paths(diff: str) -> list[str]:
    paths: list[str] = []
    for line in diff.splitlines():
        if not (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        raw = line[4:].strip().split("\t", 1)[0]
        if raw == "/dev/null":
            continue
        path = raw[2:] if raw.startswith(("a/", "b/")) else raw
        if path not in paths:
            paths.append(path)
    return paths


def _classify_test_execution(execution: Execution) -> tuple[str, str | None]:
    if execution.timed_out:
        return "error", "timeout"
    if execution.exit_code == 0:
        return "passed", None
    combined = f"{execution.stdout}\n{execution.stderr}".lower()
    # pytest uses exit 1 for ordinary assertion failures.  Collection,
    # import, syntax, usage and internal errors are infrastructure/error
    # evidence and must not be silently downgraded to failed.
    error_markers = (
        "error collecting",
        "importerror",
        "modulenotfounderror",
        "syntaxerror",
        "internal error",
        "usage: pytest",
        "no tests ran",
    )
    if execution.exit_code < 0 or execution.exit_code in {2, 3, 4, 5} or any(
        marker in combined for marker in error_markers
    ):
        return "error", f"exit {execution.exit_code}"
    return "failed", f"exit {execution.exit_code}"


def _emit_event(
    result: dict,
    messages: list[dict],
    *,
    turn: int,
    action: str,
    kind: str,
    status: str,
    output: Any,
) -> dict:
    evidence_id = f"evidence-{len(result['events']) + 1:04d}"
    public_output = output if isinstance(output, (dict, list, str, int, float, bool)) or output is None else str(output)
    event = {
        "evidence_id": evidence_id,
        "turn": turn,
        "action": action,
        "kind": kind,
        "status": status,
        "output": public_output,
    }
    result["events"].append(event)
    if isinstance(public_output, str):
        content = public_output
    else:
        content = json.dumps(public_output, ensure_ascii=False, sort_keys=True)
    messages.append(
        {
            "role": "tool",
            "name": f"patchmud.{action.lower()}",
            "content": content,
            "turn": turn,
            "evidence_id": evidence_id,
        }
    )
    return event


def _tree_view(worktree: Path) -> str:
    entries: list[str] = []
    for path in sorted(worktree.rglob("*")):
        relative = path.relative_to(worktree)
        if ".git" in relative.parts or any(part.startswith(".") and part != "." for part in relative.parts):
            continue
        if len(relative.parts) > 3:
            continue
        entries.append(f"{relative}{'/' if path.is_dir() else ''}")
    return "\n".join(entries) if entries else "(empty repository)"


def _inspect(worktree: Path, raw_path: str) -> tuple[str, str]:
    pure = PurePosixPath(raw_path)
    if (
        pure.is_absolute()
        or not raw_path
        or ".." in pure.parts
        or ".git" in pure.parts
        or any(part.startswith(".") for part in pure.parts)
    ):
        return "inspect denied", "failed"
    target = worktree.joinpath(*pure.parts)
    try:
        resolved = target.resolve()
        if not resolved.is_relative_to(worktree.resolve()) or not resolved.is_file():
            return "inspect denied", "failed"
        content = resolved.read_bytes()
    except OSError:
        return "inspect denied", "failed"
    truncated = len(content) > 64 * 1024
    text = content[: 64 * 1024].decode("utf-8", errors="replace")
    if truncated:
        text += "\n[truncated at 65536 bytes]"
    return text, "passed"


def _toolchain_paths() -> tuple[Path, ...]:
    # Candidate argv is executed inside the bubblewrap image, where the
    # stable interpreter is /usr/bin/python3.  The controller's sysconfig may
    # describe a host venv and must never become an implicit bind allowlist.
    return (Path("/usr"),)


def _build_isolation_runner(worktree: Path) -> IsolationRunner:
    """Build the candidate runner with engine, Git, and fixture protections."""

    protected: list[Path] = []
    git_dir = worktree / ".git"
    if git_dir.exists():
        protected.append(git_dir)

    tests_dir = worktree / "tests"
    writable: list[Path] = []
    if tests_dir.is_dir():
        protected.append(tests_dir)
        agent_tests = tests_dir / "agent"
        agent_tests.mkdir(parents=True, exist_ok=True)
        writable.append(agent_tests)

    for path in worktree.rglob("*"):
        if path.is_file() and path.name in HARNESS_CONFIG_NAMES:
            protected.append(path)

    # Preserve order while avoiding duplicate nested mounts (notably a
    # root-level harness file already covered by a protected directory).
    unique_protected = tuple(dict.fromkeys(path.resolve() for path in protected))
    unique_writable = tuple(dict.fromkeys(path.resolve() for path in writable))
    return IsolationRunner(
        worktree,
        _toolchain_paths(),
        masked_paths=(Path(__file__).resolve().parents[1],),
        protected_paths=unique_protected,
        writable_paths=unique_writable,
    )


def _clean_runtime_files(worktree: Path) -> None:
    for path in list(worktree.rglob("__pycache__")) + list(worktree.rglob(".pytest_cache")):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
    for path in worktree.rglob("*.pyc"):
        try:
            path.unlink()
        except OSError:
            pass


def _remaining(clock: Callable[[], float], started: float, wall_seconds: float) -> float:
    return max(0.0, wall_seconds - max(0.0, clock() - started))


def _is_timeout_error(exc: Exception, now: float, started: float, wall_seconds: float) -> bool:
    return bool(getattr(exc, "timed_out", False)) or now - started >= wall_seconds


def _sanitize_error(exc: Exception, fixture_dir: Path) -> str:
    text = str(exc).strip() or type(exc).__name__
    if fixture_dir:
        text = text.replace(str(fixture_dir.resolve()), "<fixture>")
    return re.sub(r"\s+", " ", text)[:500]


def _write_artifact(artifact_dir: Path, case_id: str, result: dict) -> None:
    try:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", case_id)
        path = artifact_dir / f"{safe_id}.json"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError:
        # The execution record remains available to the caller.  Artifact
        # persistence is owned by the root store and must not hide evidence.
        return
