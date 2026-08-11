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
import shutil
import subprocess
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
CliRunner = Callable[[list[str]], str]

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


def build_subprocess_runner(timeout_s: float = DEFAULT_CLI_TIMEOUT_S) -> CliRunner:
    """真子行程執行；非零 exit、逾時或 OS 層失敗 → ``AdapterError``。

    ``stdin`` 一律導向 ``DEVNULL``：CLI 偵測到 stdin 是 pipe 時會等待補充輸入
    而卡住。
    """

    def _runner(argv: list[str]) -> str:
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=True,
                timeout=timeout_s,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()[:500]
            raise AdapterError(
                f"{argv[0]} 以 exit={exc.returncode} 結束：{detail}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"{argv[0]} 逾時（{timeout_s}s）") from exc
        except OSError as exc:
            raise AdapterError(f"{argv[0]} 無法執行：{exc}") from exc
        return completed.stdout

    return _runner


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
    ) -> None:
        if not model:
            raise AdapterError(f"{self.binary_name} adapter 需要 model id")
        self.model = model
        self.effort = effort
        self._binary = binary or shutil.which(self.binary_name) or self.binary_name
        self._clock = clock
        self._runner = runner if runner is not None else build_subprocess_runner(timeout_s)

    def complete(self, messages: list[dict]) -> AdapterResponse:
        prompt = flatten_messages(list(messages))
        argv = self._build_argv(prompt)
        started = self._clock()
        stdout = self._runner(argv)
        wall_ms = round((self._clock() - started) * 1000)
        text, usage_raw = self._parse(stdout)
        return AdapterResponse(text=text, usage_raw=usage_raw, wall_ms=wall_ms)

    def _build_argv(self, prompt: str) -> list[str]:
        raise NotImplementedError

    def _parse(self, stdout: str) -> tuple[str, dict]:
        """回傳 ``(text, usage_raw)``；任一缺漏 fail-closed。"""
        raise NotImplementedError
