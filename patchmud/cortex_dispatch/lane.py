"""Cortex dispatch builder lane：在 Cortex builder 派工條件下打一場 encounter 並評分。

流程（設計見 PR 說明）：

1. target → 發射契約（:func:`resolve_launch_contract`）→ 開局 profile record（observed
   unknown）寫進 ``run.yaml``；loadout 記 ``builder``（Cortex loadout id）。
2. 物化 encounter ``repo/`` 為 agent workspace（hidden 不物化），設 repo-local git
   identity 與 ``.git/info/exclude``（快取檔），切到候選分支。
3. 另物化一份評分用 checkout，跑 baseline public probes（baseline event）。
4. prompt＝Cortex builder persona 契約（逐字）＋卡片執行契約＋encounter 公開任務。
5. 在外層 workspace-write 沙箱＋egress allowlist 下執行 Cortex 形狀的 codex argv。
6. ``thread/read``（同一個 ``CODEX_HOME``、同一套邊界）回讀 provider 身分。
7. 候選＝commit 出來的 HEAD：以**沙箱內**的 git 讀 HEAD／status／diff（agent 控制的
   repo config 不在 host 執行）。沒有 commit、worktree 不乾淨、codex 失敗 →
   ``failed:protocol``；逾時 → ``wall_clock``。
8. 評分與 ``score-diff`` 同構：Workspace 嚴格模式套候選 diff（保護區／harness
   設定檔拒收 → ``failed:protocol``）→ public probes → hidden evaluator →
   ``compute_clear``。
9. turn／final events、``result.yaml``、post-run ``execution_profile.json``（observed
   由發射事實與 provider 觀測補值）。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from patchmud.cortex_dispatch.codex_argv import build_cortex_builder_codex_argv
from patchmud.cortex_dispatch.persona import load_persona_contract, render_contract_prompt
from patchmud.cortex_dispatch.profile import (
    LaunchObservation,
    build_dispatch_profile_record,
    resolve_launch_contract,
)
from patchmud.cortex_dispatch.sandbox import (
    SANDBOX_CODEX_HOME,
    SANDBOX_LAST_MESSAGE,
    SANDBOX_WORKSPACE,
    AgentOutcome,
    AgentSandboxSpec,
)
from patchmud.cortex_dispatch.session import ThreadReader, observe_thread, parse_session
from patchmud.cortex_dispatch.target import DispatchTarget
from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.deck.model import IssueCard
from patchmud.engine.loop import END_COMMIT, END_PROTOCOL, END_WALL_CLOCK
from patchmud.engine.queue import IssueQueue
from patchmud.evaluator.evaluate import FinalEvaluation, evaluate_final
from patchmud.evaluator.gates import compute_clear
from patchmud.ledger.tokens import aggregate_billed_totals, aggregate_work_tokens
from patchmud.report_provenance import build_run_report_metadata
from patchmud.sandbox.isolate import IsolationRunner
from patchmud.sandbox.probes import ProbeResults, ProbeSuite
from patchmud.sandbox.workspace import (
    HARNESS_CONFIG_NAMES,
    PROTECTED_PREFIXES,
    Workspace,
)
from patchmud.store.run_store import RunStore
from patchmud.usage_provenance import map_usage_with_provenance

__all__ = [
    "DISPATCH_PROMPT_VERSION",
    "AgentLaunch",
    "DispatchLaneConfig",
    "DispatchRunError",
    "DispatchRunResult",
    "build_builder_prompt",
    "run_dispatch_lane",
]

#: builder lane prompt 模板版本（任務區塊措辭改動時 bump；persona 前言另以 digest 稽核）。
DISPATCH_PROMPT_VERSION = "cortex-dispatch-prompt-1.0.0"
_LEDGER_FILE = "ledger.jsonl"
_LEDGER_SCHEMA_VERSION = 1
_SESSION_FILE = "codex-session.jsonl"
_CANDIDATE_BRANCH = "patchmud/candidate"
_GIT_IDENTITY = ("PatchMUD Builder", "builder@patchmud.invalid")
_CACHE_EXCLUDES = ("__pycache__/", "*.pyc", ".pytest_cache/")
_GIT_SAFE_FLAGS: tuple[str, ...] = (
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "diff.noprefix=false",
    "-c", "diff.mnemonicPrefix=false",
    "-c", "diff.relative=false",
    "-c", "core.quotePath=true",
)
_INSPECT_TIMEOUT_S = 60.0
_NA = "NA"


class DispatchRunError(RuntimeError):
    """builder lane 無法完成一場可評分的 run（fail-closed）。"""


@dataclass(frozen=True)
class AgentLaunch:
    """交給 agent runner 的一次 builder session。"""

    spec: AgentSandboxSpec
    codex_argv: tuple[str, ...]
    timeout_s: float


AgentRunner = Callable[[AgentLaunch], AgentOutcome]
ThreadReaderFactory = Callable[[AgentLaunch], ThreadReader | None]


@dataclass(frozen=True)
class DispatchLaneConfig:
    """builder lane 的執行面佈線（真佈線見 cli ``dispatch-run``；tests 注入 fake）。"""

    toolchain: tuple[Path, ...]
    pytest_argv: tuple[str, ...]
    ruff_argv: tuple[str, ...]
    agent_runner: AgentRunner
    thread_reader_factory: ThreadReaderFactory
    codex_invoke: str
    runtime_ro: tuple[Path, ...]
    path_dirs: tuple[str, ...]
    auth_file: Path
    pythonpath: tuple[str, ...] = ()
    bwrap_path: str = "/usr/bin/bwrap"
    timeout_s: float = 1800.0
    egress_allowlist: tuple[str, ...] = ("chatgpt.com", "openai.com")
    facts: Mapping[str, object] | None = None


@dataclass(frozen=True)
class DispatchRunResult:
    run_dir: Path
    clear: int
    end_reason: str
    protocol_failed: bool
    power_total: float
    profile_id: str
    actual_condition_key: str | None
    failure: str | None
    wall_ms: int
    usage: Mapping[str, object] | None


def build_builder_prompt(
    persona_text: str, target: DispatchTarget, card: IssueCard
) -> str:
    """Cortex builder persona 契約（逐字）＋卡片執行契約＋encounter 公開任務。"""
    protected = "、".join(f"{prefix}**" for prefix in PROTECTED_PREFIXES)
    harness = "、".join(sorted(HARNESS_CONFIG_NAMES))
    lines = [
        persona_text,
        "",
        f"[TASK] {card.issue_id}：{card.briefing}",
        f"- archetype：{card.archetype}／難度：{card.difficulty}",
        f"[CARD: {target.card.id}] {target.card.action}",
        f"- commit_policy: {target.card.commit_policy}"
        "（交付物＝本分支 commit 出來的 HEAD；未 commit 的變更不算交付）",
        f"- test_policy: {target.card.test_policy or 'none'}",
        "",
        "[AUTHORITATIVE WORKTREE ROOT — JSON string]",
        json.dumps(SANDBOX_WORKSPACE, ensure_ascii=False),
        "所有 repository 檔案的讀取、寫入與命令都必須留在此根目錄內；命令 cwd 設為此目錄，"
        "repo 內目標使用相對路徑。",
        "",
        "[ACCEPTANCE — public requirements]",
    ]
    lines.extend(
        f"- {req.id}：{req.text}（public probe：{req.probe}）"
        for req in card.public_requirements
    )
    lines += [
        "",
        "[SCOPE]",
        f"- 允許修改路徑：{', '.join(card.allowed_paths)}",
        f"- 預期修改路徑：{', '.join(card.expected_paths)}",
        f"- 唯讀保護區（任何修改都會使候選被拒收）：{protected}，以及任何名為 {harness} 的檔案",
        "- 交付後另有你看不到的隱藏驗收；只讓公開測試通過不代表修好。",
        "",
        "[GATE] 本卡的 deterministic gate：在 worktree 根目錄執行 `python3 -m pytest -q tests/public`。",
        "完成前 MUST 以 git add 加入本次全部變更並 git commit；worktree 不乾淨即視為未完成。",
    ]
    return "\n".join(lines)


def _git_host(repo: Path, *args: str) -> str:
    """host 端 git：只用在 PatchMUD 自己物化、agent 尚未碰過的 repo。"""
    import subprocess

    env = {
        "PATH": "/usr/bin:/bin",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C.UTF-8",
    }
    result = subprocess.run(
        ["git", "-C", str(repo), *args], env=env, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise DispatchRunError(f"git {' '.join(args)} 失敗：{result.stderr.strip()[:200]}")
    return result.stdout.strip()


def _prepare_agent_workspace(encounter_dir: Path, dest: Path) -> str:
    frozen = materialize_repo(encounter_dir, dest)
    _git_host(dest, "config", "user.name", _GIT_IDENTITY[0])
    _git_host(dest, "config", "user.email", _GIT_IDENTITY[1])
    _git_host(dest, "checkout", "--quiet", "-b", _CANDIDATE_BRANCH)
    exclude = dest / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("\n".join(_CACHE_EXCLUDES) + "\n", encoding="utf-8")
    return frozen.sha


@dataclass(frozen=True)
class _Candidate:
    head: str | None
    commits: int
    dirty_paths: tuple[str, ...]
    diff: str
    error: str | None


def _inspect_candidate(workspace: Path, frozen_sha: str, toolchain: tuple[Path, ...], bwrap_path: str) -> _Candidate:
    """在沙箱內讀候選：agent 控制的 repo config 不得在 host 執行任何東西。"""
    runner = IsolationRunner(workspace, toolchain, bwrap_path=bwrap_path)

    def git(*args: str) -> tuple[int, str]:
        execution = runner.run(
            ["git", *_GIT_SAFE_FLAGS, *args], cwd=workspace, timeout_s=_INSPECT_TIMEOUT_S
        )
        if execution.timed_out:
            return 124, ""
        return execution.exit_code, execution.stdout

    code, head = git("rev-parse", "--verify", "HEAD")
    if code != 0:
        return _Candidate(None, 0, (), "", "候選 HEAD 無法讀取")
    head = head.strip()
    code, count = git("rev-list", "--count", f"{frozen_sha}..{head}")
    commits = int(count.strip()) if code == 0 and count.strip().isdigit() else 0
    code, status = git("status", "--porcelain=v1", "--untracked-files=all")
    if code != 0:
        return _Candidate(head, commits, (), "", "worktree status 無法讀取")
    dirty = tuple(line[3:] for line in status.splitlines() if line.strip())
    code, diff = git(
        "diff", "--no-color", "--no-ext-diff", "--no-textconv",
        "--src-prefix=a/", "--dst-prefix=b/", frozen_sha, head,
    )
    if code != 0:
        return _Candidate(head, commits, dirty, "", "候選 diff 無法產生")
    return _Candidate(head, commits, dirty, diff, None)


_BOUNDARY_FLAGS = ("--unshare-net", "--unshare-user", "--unshare-pid", "--clearenv", "--die-with-parent")


def _boundary_is_workspace_write(
    sandbox_argv: Sequence[str],
    spec: AgentSandboxSpec,
    *,
    forbidden: Sequence[Path] = (),
) -> bool:
    """由**實際執行過**的 bwrap argv 判定 workspace-write。

    可寫只能是契約內的掛載（workspace、codex 狀態目錄、egress socket 目錄）與
    ``/tmp`` tmpfs；任何掛載來源（含唯讀）都不得是 ``forbidden``（deck／hidden、
    runs store）本身或其祖先目錄。
    """
    if not sandbox_argv or any(flag not in sandbox_argv for flag in _BOUNDARY_FLAGS):
        return False
    rw: list[tuple[str, str]] = []
    sources: list[str] = []
    tmpfs: list[str] = []
    index = 0
    while index < len(sandbox_argv):
        token = sandbox_argv[index]
        if token == "--":
            break
        if token in ("--bind", "--bind-try", "--dev-bind", "--dev-bind-try"):
            rw.append((sandbox_argv[index + 1], sandbox_argv[index + 2]))
            sources.append(sandbox_argv[index + 1])
            index += 3
            continue
        if token in ("--ro-bind", "--ro-bind-try"):
            sources.append(sandbox_argv[index + 1])
            index += 3
            continue
        if token == "--tmpfs":
            tmpfs.append(sandbox_argv[index + 1])
            index += 2
            continue
        index += 1
    allowed = set(spec.rw_mounts())
    egress = [item for item in rw if item[1] == "/run/patchmud-egress"]
    others = [item for item in rw if item[1] != "/run/patchmud-egress"]
    if set(others) != allowed or len(others) != len(allowed) or len(egress) > 1:
        return False
    if tmpfs != ["/tmp"]:
        return False
    for source in sources:
        mounted = Path(source)
        for item in forbidden:
            protected = Path(item).resolve()
            if protected == mounted or protected.is_relative_to(mounted):
                return False
    return True


def _usage_ledger(store: RunStore, usage: Mapping[str, object], *, wall_ms: int, tool_calls: int, prompt: str, message: str | None, adapter_version: str):
    entry, evidence = map_usage_with_provenance(
        "codex",
        dict(usage),
        adapter_version=adapter_version,
        turn=1,
        role="author",
        tool_calls=tool_calls,
        wall_clock_ms=wall_ms,
        prompt_bytes=len(prompt.encode("utf-8")),
        generated_bytes=len((message or "").encode("utf-8")),
    )
    record = {"schema_version": _LEDGER_SCHEMA_VERSION, **asdict(entry)}
    with (store.run_dir / _LEDGER_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    store.append_usage_evidence(evidence)
    return entry


def _statuses(results: ProbeResults) -> dict[str, str]:
    return {probe_id: results[probe_id].status for probe_id in results}


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_dispatch_lane(
    encounter_dir: Path,
    target: DispatchTarget,
    runs_root: Path,
    config: DispatchLaneConfig,
    *,
    run_id: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DispatchRunResult:
    """打完一場 builder lane run；回傳摘要，完整落盤在 run 目錄。"""
    from patchmud import cli as _cli  # 延遲匯入：result 形狀與 score-diff 共用 helper

    encounter_dir = Path(encounter_dir).resolve()
    card = load_card(encounter_dir / "card.yaml")
    contract = resolve_launch_contract(target)
    persona = load_persona_contract(target.persona)
    persona_text = render_contract_prompt(persona)
    prompt = build_builder_prompt(persona_text, target, card)
    pre_run = build_dispatch_profile_record(target, contract)
    if run_id is None:
        run_id = f"dispatch-{card.issue_id}-{time.strftime('%Y%m%d%H%M%S')}"

    runs_root = Path(runs_root)
    with tempfile.TemporaryDirectory(prefix="patchmud-dispatch-") as tmp:
        tmp_path = Path(tmp)
        agent_ws = tmp_path / "agent" / "repo"
        frozen_sha = _prepare_agent_workspace(encounter_dir, agent_ws)
        score_frozen = materialize_repo(encounter_dir, tmp_path / "score" / "worktree")
        if score_frozen.sha != frozen_sha:
            raise DispatchRunError("評分 checkout 與 agent workspace 的 frozen SHA 不一致")
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        (codex_home / "auth.json").write_text("", encoding="utf-8")  # bind 掛載點

        store = RunStore.create(
            {
                "run_id": run_id,
                "frozen_sha": frozen_sha,
                "pricing_hash": _NA,
                "harness_prompt_version": DISPATCH_PROMPT_VERSION,
                "schedule_ref": _NA,
                "encounter_dir": str(encounter_dir),
                "measured_at": _cli._report_timestamp(),
                "loadout": contract.persona,
                "model": f"{contract.executor}:{contract.model_id}",
                "execution_profile": pre_run,
                "dispatch_target": target.to_dict(),
                "dispatch_lane": {
                    "timeout_s": config.timeout_s,
                    "egress_allowlist": list(config.egress_allowlist),
                    "sandbox_workspace": SANDBOX_WORKSPACE,
                    "sandbox_codex_home": SANDBOX_CODEX_HOME,
                    "prompt_sha256": _sha256_text(prompt),
                    "persona_contract_sha256": _sha256_text(persona_text),
                    "persona_contract_source": persona.source_ref,
                },
                **build_run_report_metadata(
                    encounter_dir, role="builder", benchmark_type="issue-resolution"
                ),
            },
            runs_root,
        )

        score_ws = Workspace(
            frozen=score_frozen,
            encounter_dir=encounter_dir,
            shadow_dir=store.run_dir / "checkpoints",
        )
        public_runner = IsolationRunner(score_frozen.path, config.toolchain, bwrap_path=config.bwrap_path)
        suite = ProbeSuite.from_card(card, public_runner, pytest_argv=config.pytest_argv)
        baseline = suite.run(score_ws)
        queue = IssueQueue.from_card(card, baseline)
        store.append_event(
            {
                "type": "baseline",
                "probes": _statuses(baseline),
                "queue": queue.snapshot(),
                "checkpoint": score_ws.checkpoint(),
            }
        )

        codex_argv = build_cortex_builder_codex_argv(
            codex=config.codex_invoke,
            prompt=prompt,
            model=contract.model_id,
            effort=contract.effort,
            last_message_path=SANDBOX_LAST_MESSAGE,
            worktree=SANDBOX_WORKSPACE,
            host_worktree=agent_ws,
        )
        spec = AgentSandboxSpec(
            workspace=agent_ws,
            codex_home=codex_home,
            auth_file=config.auth_file,
            runtime_ro=config.runtime_ro,
            toolchain_ro=tuple(path for path in config.toolchain if path != Path("/usr")),
            path_dirs=config.path_dirs,
            pythonpath=config.pythonpath,
        )
        launch = AgentLaunch(spec=spec, codex_argv=tuple(codex_argv), timeout_s=config.timeout_s)
        outcome = config.agent_runner(launch)
        (store.run_dir / _SESSION_FILE).write_text(outcome.stdout, encoding="utf-8")
        transcript = parse_session(outcome.stdout)
        thread = observe_thread(transcript.thread_id, config.thread_reader_factory(launch))

        candidate = _inspect_candidate(agent_ws, frozen_sha, config.toolchain, config.bwrap_path)

        failure: str | None = None
        if outcome.timed_out:
            end_reason = END_WALL_CLOCK
            failure = "builder-session-timeout"
        elif outcome.exit_code != 0:
            end_reason = END_PROTOCOL
            failure = f"codex-exit-{outcome.exit_code}"
        elif transcript.failures:
            end_reason = END_PROTOCOL
            failure = "codex-session-error"
        elif candidate.error is not None:
            end_reason = END_PROTOCOL
            failure = candidate.error
        elif candidate.commits == 0 or not candidate.diff.strip():
            end_reason = END_PROTOCOL
            failure = "no-committed-candidate"
        elif candidate.dirty_paths:
            end_reason = END_PROTOCOL
            failure = "candidate-worktree-dirty"
        else:
            end_reason = END_COMMIT

        applied_reason: str | None = None
        if end_reason == END_COMMIT:
            applied = score_ws.apply_patch(candidate.diff, kind="production")
            if applied.rejected:
                end_reason = END_PROTOCOL
                failure = "candidate-rejected"
                applied_reason = applied.reason
        protocol_failed = end_reason == END_PROTOCOL

        final_public = suite.run(score_ws)
        final_diff = score_ws.cumulative_diff()
        geometry = _cli_diff_geometry(final_diff, score_ws.diff_stats().reverted_loc)
        queue.update(final_public, geometry, None)
        checkpoint = score_ws.checkpoint()

        launch_observation = LaunchObservation(
            started=transcript.thread_id is not None,
            argv_conforms=list(launch.codex_argv[1:])
            == build_cortex_builder_codex_argv(
                codex=config.codex_invoke,
                prompt=prompt,
                model=contract.model_id,
                effort=contract.effort,
                last_message_path=SANDBOX_LAST_MESSAGE,
                worktree=SANDBOX_WORKSPACE,
            )[1:],
            workspace_write_boundary=_boundary_is_workspace_write(
                outcome.sandbox_argv,
                spec,
                forbidden=(encounter_dir, runs_root.resolve()),
            ),
            persona_contract_rendered=prompt.startswith(persona_text + "\n"),
            git_commit_available=(agent_ws / ".git").is_dir()
            and shutil.which("git", path="/usr/bin") is not None,
            facts={
                "codex_argv_sha256": _sha256_text("\0".join(launch.codex_argv[1:])),
                "sandbox_argv_sha256": _sha256_text("\0".join(outcome.sandbox_argv)),
                "persona_contract_sha256": _sha256_text(persona_text),
                "prompt_sha256": _sha256_text(prompt),
                "session_stdout_sha256": "sha256:" + transcript.stdout_sha256,
                "egress_allowed": dict(outcome.egress_allowed),
                "egress_denied": dict(outcome.egress_denied),
                **dict(config.facts or {}),
            },
        )

        if transcript.usage is None:
            store.append_event(
                {
                    "type": "dispatch_failure",
                    "reason": "codex 事件串缺唯一的 turn.completed.usage，ledger 無從計費",
                    "exit_code": outcome.exit_code,
                    "timed_out": outcome.timed_out,
                }
            )
            raise DispatchRunError(
                "codex 事件串缺唯一的 turn.completed.usage（ledger 無從計費）；"
                f"run 目錄保留為未完成：{store.run_dir}"
            )
        entry = _usage_ledger(
            store,
            transcript.usage,
            wall_ms=outcome.wall_ms,
            tool_calls=transcript.command_count,
            prompt=prompt,
            message=transcript.last_message,
            adapter_version=contract.toolchain_version,
        )
        store.append_event(
            {
                "type": "turn",
                "turn": 1,
                "action": "BUILDER_SESSION",
                "claim": None,
                "outcome": "executed" if end_reason == END_COMMIT else "error",
                "detail": failure,
                "checkpoint": checkpoint,
                "probes": _statuses(final_public),
                "queue": queue.snapshot(),
                "ledger": {"author": asdict(entry), "reviewer": None},
                "reviewer_subcall": None,
                "builder_session": {
                    "exit_code": outcome.exit_code,
                    "timed_out": outcome.timed_out,
                    "wall_ms": outcome.wall_ms,
                    "thread_observed": thread.model_id is not None,
                    "commands": transcript.command_count,
                    "command_preview": list(transcript.commands[:50]),
                    "session_errors": list(transcript.failures),
                    "egress_allowed": dict(outcome.egress_allowed),
                    "egress_denied": dict(outcome.egress_denied),
                    "stderr_tail": outcome.stderr_tail[-500:],
                },
                "candidate": {
                    "head": candidate.head,
                    "commits": candidate.commits,
                    "dirty_paths": list(candidate.dirty_paths[:50]),
                    "diff_sha256": _sha256_text(candidate.diff),
                    "rejected_reason": applied_reason,
                },
            }
        )

        evaluation: FinalEvaluation = evaluate_final(
            card,
            score_frozen,
            final_diff,
            lambda checkout: IsolationRunner(checkout, config.toolchain, bwrap_path=config.bwrap_path),
            encounter_dir=encounter_dir,
            pytest_argv=config.pytest_argv,
            ruff_argv=config.ruff_argv,
        )
        main_public_green = all(
            _cli._is_green(final_public, req.probe) for req in card.public_requirements
        )
        clear = compute_clear(
            critical_pass=evaluation.gates.critical_pass,
            main_public_green=main_public_green,
            protocol_failed=protocol_failed,
        )
        store.append_event(
            {
                "type": "final",
                "end_reason": end_reason,
                "turns": 1,
                "probes": _statuses(final_public),
                "queue": queue.snapshot(),
                "clear": clear,
            }
        )
        work_tokens = aggregate_work_tokens([entry])
        billed_input, billed_output = aggregate_billed_totals([entry])
        store.write_result(
            {
                "run_id": run_id,
                "mode": "run",
                "human": False,
                "loadout": contract.persona,
                "clear": clear,
                "end_reason": end_reason,
                "protocol_failed": protocol_failed,
                "turns": 1,
                "main_public_green": main_public_green,
                "gates": {
                    "critical_pass": evaluation.gates.critical_pass,
                    "power_cap": evaluation.gates.power_cap,
                    "run_invalid": evaluation.gates.run_invalid,
                },
                "power": _cli._power_dict(evaluation.power),
                "probes": {
                    "public": _cli._probes_dict(final_public),
                    "evaluator": _cli._probes_dict(evaluation.probe_outcomes),
                },
                "ledger": {
                    "entries": 1,
                    "billed_input_total": billed_input if billed_input is not None else _NA,
                    "billed_output_total": billed_output if billed_output is not None else _NA,
                    "work_tokens": work_tokens if work_tokens is not None else _NA,
                    "reviewer_calls": 0,
                },
                "economy": _NA,
                "economy_reason": "run 無 pricing snapshot 或 reference_cost，Economy 不適用",
                "dispatch": {
                    "target": target.name,
                    "target_sha256": target.sha256,
                    "failure": failure,
                    "candidate_rejected_reason": applied_reason,
                    "candidate_commits": candidate.commits,
                    "candidate_diff_sha256": _sha256_text(candidate.diff),
                },
            }
        )
        post_run = build_dispatch_profile_record(
            target, contract, launch=launch_observation, thread=thread
        )
        for key in ("descriptor", "requested", "resolved", "profile_id", "resolved_key"):
            if post_run[key] != pre_run[key]:
                raise DispatchRunError(f"post-run profile 的 {key} 與開局不一致")
        store.write_execution_profile(post_run)

    return DispatchRunResult(
        run_dir=store.run_dir,
        clear=clear,
        end_reason=end_reason,
        protocol_failed=protocol_failed,
        power_total=float(evaluation.power.total),
        profile_id=str(post_run["profile_id"]),
        actual_condition_key=post_run["actual_condition_key"],
        failure=failure,
        wall_ms=outcome.wall_ms,
        usage=dict(transcript.usage),
    )


def _cli_diff_geometry(diff_text: str, reverted_loc: int):
    from patchmud.engine.loop import _diff_geometry

    return _diff_geometry(diff_text, reverted_loc)
