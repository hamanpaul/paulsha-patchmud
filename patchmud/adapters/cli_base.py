"""CLI-based adapter 的共同骨架（spec §2 adapters、§10.1）。

`codex` / `agy` 這類 coding-agent CLI 自帶 OAuth 登入態，不需要在環境變數
放 API key；PatchMUD 只把它們當**純補全**用：

- 工具寫入能力一律關閉、在臨時空目錄執行——執行 candidate code 的唯一 seam
  仍是 :class:`~patchmud.sandbox.isolate.IsolationRunner`（spec §2）。若讓
  agent CLI 直接在 encounter workspace 讀寫，等於繞過隔離層，且 ``hidden/``
  不再有防洩漏保證。
- 對局歷史攤平成單一 prompt 字串：每次呼叫都是獨立 session，CLI 端不保留
  對話狀態。
- ``usage_raw`` 原樣透傳 CLI 揭露的 usage 物件；欄位差異吸收在 ledger 的
  per-provider mapper（``map_usage("codex"/"agy", …)``），adapter 不拆、
  不清洗、不解讀（§10.1）。
- 子行程執行抽成注入的 runner callable：unit tests 注入 fake，不啟真 CLI、
  不打真 API（plan invariant 3）。

**計量特性**：CLI 自帶 system prompt 與 skill 目錄，每回合有數千至兩萬
tokens 的固定 input overhead，且每次呼叫都重新計入。跨 provider 比較
economy 維度時，這層 overhead 屬於 provider 的既有成本結構，不做扣除——
但要知道它存在（同樣適用於既有的 ``ClaudeCliAdapter``）。
"""

from __future__ import annotations

import json
from contextlib import nullcontext
import os
import signal
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable

from patchmud.adapters.base import AdapterError, AdapterResponse, ModelAdapter

__all__ = [
    "DEFAULT_CLI_TIMEOUT_S",
    "CliModelAdapter",
    "CliRunner",
    "build_subprocess_runner",
    "flatten_messages",
]

DEFAULT_CLI_TIMEOUT_S = 600.0

#: runner callable：送出 argv、回傳子行程 stdout。
#:
#: The optional timeout and prompt arguments are supported by the runner
#: returned from :func:`build_subprocess_runner`.  Existing tests and callers
#: can continue to inject a one-argument callable.
CliRunner = Callable[..., str]

_ROLE_LABELS = {
    "system": "System",
    "user": "User",
    "assistant": "Assistant",
}


def flatten_messages(messages: list[dict]) -> str:
    """把 ``{"role", "content"}`` 對話攤平成單一 prompt 字串。

    CLI 每次呼叫都是獨立 session，沒有伺服器端對話狀態，因此 system 與歷史
    回合一併寫進 prompt 本體。角色以標記行區隔，逐字保留 content。
    """
    blocks: list[str] = []
    for message in messages:
        role = str(message.get("role", ""))
        label = _ROLE_LABELS.get(role, role or "Unknown")
        blocks.append(f"[{label}]\n{message.get('content', '')}")
    return "\n\n".join(blocks)


def build_subprocess_runner(
    timeout_s: float = DEFAULT_CLI_TIMEOUT_S, *, controlled: bool = False
) -> CliRunner:
    """真子行程執行；非零 exit、逾時或 OS 層失敗 → ``AdapterError``。

    Legacy invocations keep ``stdin=DEVNULL``.  Controlled invocations use a pipe
    so the flattened prompt can be supplied without putting a deep prompt in the
    command-line argument vector.
    """

    def _runner(
        argv: list[str],
        timeout_override_s: float | None = None,
        input_text: str | None = None,
    ) -> str:
        effective_timeout = timeout_s if timeout_override_s is None else timeout_override_s
        process: subprocess.Popen[str] | None = None
        # Controlled scoring invocations start in a fresh empty cwd and use a
        # scrubbed child environment.  Legacy callers retain their historical
        # cwd/environment contract; process-group cleanup and timeout support
        # are safe universal improvements.
        cwd_context = (
            tempfile.TemporaryDirectory(prefix="patchmud-cli-guard-")
            if controlled
            else nullcontext(None)
        )
        with cwd_context as clean_cwd:
            try:
                process = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    stdin=subprocess.PIPE if controlled else subprocess.DEVNULL,
                    cwd=clean_cwd,
                    env=_controlled_cli_env() if controlled else None,
                    start_new_session=True,
                )
                if controlled:
                    stdout, stderr = process.communicate(
                        input=input_text, timeout=effective_timeout
                    )
                else:
                    # Preserve the legacy fake/process contract: no input
                    # keyword is sent when stdin is DEVNULL.
                    stdout, stderr = process.communicate(timeout=effective_timeout)
            except subprocess.TimeoutExpired as exc:
                if process is not None:
                    _kill_process_group(process.pid)
                    stdout, stderr = process.communicate()
                error = AdapterError(f"{argv[0]} 逾時（{effective_timeout}s）")
                error.timed_out = True  # type: ignore[attr-defined]
                error.stdout = stdout if process is not None else ""  # type: ignore[attr-defined]
                error.stderr = stderr if process is not None else ""  # type: ignore[attr-defined]
                raise error from exc
            except OSError as exc:
                raise AdapterError(f"{argv[0]} 無法執行：{exc}") from exc
            except BaseException:
                # Cancellation/interrupts must not orphan a provider CLI or
                # one of its descendants.  ``start_new_session`` gives us a
                # private process group for this exact cleanup boundary.
                if process is not None:
                    _kill_process_group(process.pid)
                    if process.poll() is None:
                        process.communicate()
                raise
        if process is None:  # pragma: no cover - Popen either succeeds or raises
            raise AdapterError(f"{argv[0]} 未建立子行程")
        if process.returncode != 0:
            detail = (stderr or stdout or "").strip()[:500]
            raise AdapterError(
                f"{argv[0]} 以 exit={process.returncode} 結束：{detail}"
            )
        return stdout

    _runner.supports_timeout = True  # type: ignore[attr-defined]
    _runner.supports_input = controlled  # type: ignore[attr-defined]
    return _runner


def _controlled_cli_env() -> dict[str, str]:
    """Keep provider authentication while removing ambient execution state."""

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    # OAuth CLIs read these stores themselves.  Codex also has explicit
    # ``--ignore-user-config``; agy's plan/sandbox mode is paired with the
    # empty cwd above.  Do not pass generic XDG/plugin/skill variables.
    for key in ("HOME", "CODEX_HOME", "ANTIGRAVITY_HOME"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _kill_process_group(pid: int) -> None:
    """Terminate a timed-out CLI and every descendant it may have spawned."""

    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def iter_json_objects(stdout: str) -> list[dict]:
    """逐行解析 stdout 中的 JSON object，忽略非 JSON 的雜訊行。

    CLI 會在 stdout 混入進度提示（如 ``Fetching available models...``），
    這些行不是契約的一部分，跳過即可；真正的缺漏由呼叫端 fail-closed。
    """
    objects: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            objects.append(parsed)
    return objects


class CliModelAdapter(ModelAdapter):
    """headless CLI adapter 的共同骨架：計時、子行程執行、prompt 攤平。"""

    #: CLI 可執行檔名（子類覆寫）。
    binary_name: str = ""

    def __init__(
        self,
        model: str,
        *,
        effort: str = "high",
        binary: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        runner: CliRunner | None = None,
        timeout_s: float = DEFAULT_CLI_TIMEOUT_S,
        controlled: bool = False,
    ) -> None:
        if not model:
            raise AdapterError(f"{self.binary_name} adapter 需要 model id")
        self.model = model
        self.effort = effort
        self._binary = binary or shutil.which(self.binary_name) or self.binary_name
        self._clock = clock
        self.controlled = bool(controlled)
        # Concrete controlled adapters switch their prompt argument shape while
        # ``_build_argv`` runs.  The flag is transient so direct argv-building
        # and injected one-argument fakes retain the historical contract.
        self._stdin_transport = False
        self._runner = (
            runner
            if runner is not None
            else build_subprocess_runner(timeout_s, controlled=self.controlled)
        )

    def complete(self, messages: list[dict]) -> AdapterResponse:
        return self._complete(messages, timeout_s=None)

    def complete_with_timeout(
        self, messages: list[dict], timeout_s: float
    ) -> AdapterResponse:
        """Complete with a per-call deadline when the subprocess runner supports it.

        The method is additive to ``ModelAdapter`` so legacy adapters and injected
        one-argument fake runners retain their existing contract.  Scoring uses it
        to clamp every model call to the case's remaining wall budget.
        """

        return self._complete(messages, timeout_s=max(0.001, float(timeout_s)))

    def _complete(
        self, messages: list[dict], *, timeout_s: float | None
    ) -> AdapterResponse:
        prompt = flatten_messages(list(messages))
        use_stdin = self.controlled and getattr(self._runner, "supports_input", False)
        self._stdin_transport = use_stdin
        try:
            argv = self._build_argv(prompt)
            started = self._clock()
            if use_stdin:
                # The built-in controlled runner accepts the third argument.  A
                # one-argument injected fake does not advertise this capability,
                # so it continues to receive the complete argv as before.
                stdout = self._runner(argv, timeout_s, prompt)
            elif timeout_s is not None and getattr(self._runner, "supports_timeout", False):
                stdout = self._runner(argv, timeout_s)
            else:
                stdout = self._runner(argv)
        finally:
            self._stdin_transport = False
        wall_ms = round((self._clock() - started) * 1000)
        text, usage_raw = self._parse(stdout)
        return AdapterResponse(text=text, usage_raw=usage_raw, wall_ms=wall_ms)

    def _build_argv(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def _parse(self, stdout: str) -> tuple[str, dict]:
        """回傳 ``(text, usage_raw)``；任一缺漏 fail-closed。"""
        raise NotImplementedError
