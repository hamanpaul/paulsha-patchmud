"""以 coding-agent CLI 模擬 structured judge（Claude CLI、Copilot CLI）。

LLM judge 沒有原生 typed 介面，因此把 ``StructuredRequest`` 攤成固定的
prompt（``SYSTEM_PROMPT``＋``render_user_prompt``），要求只回一個 JSON
object，再用與 JEV 相同的 ``parse_answers`` 驗證。prompt 文字屬於凍結的
judge spec，改動會改變 ``judge_spec_digest``。

執行邊界（沿用 ``patchmud.adapters.cli_base`` 的純補全原則）：
- 關閉全部工具與 MCP，於每次呼叫新建的空暫存目錄執行，不讀 repo 的
  agent 指示檔；子行程 environment 移除 ``TYPESAFE_API_KEY``。
- Claude CLI 以 ``--system-prompt`` 取代內建 system prompt；Copilot CLI 無法
  取代內建 prompt，只能把同一段 system 文字放在 prompt 開頭——兩者的
  固定 overhead 屬於 provider 既有成本結構，照實計入。
- 成本：Claude 取 CLI 回報的 ``total_cost_usd``（API 牌價等值）；Copilot 取
  ``--usage-output-file`` 的 premium request 用量，乘以每單位 0.04 美元估算，
  ``basis`` 標明為估算。
- 子行程 runner 可注入：unit tests 不啟真 CLI、不打真 API。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

from patchmud.judge.structured import (
    JudgeCost,
    JudgeError,
    StructuredJudgeAdapter,
    StructuredRequest,
    StructuredResponse,
    parse_answers,
)

__all__ = [
    "COPILOT_USD_PER_PREMIUM_UNIT",
    "OUTPUT_CONTRACT",
    "SYSTEM_PROMPT",
    "ClaudeCliJudge",
    "CliJudgeRunner",
    "CopilotCliJudge",
    "build_cli_judge_runner",
    "copilot_user_mcp_servers",
    "extract_json_object",
    "render_user_prompt",
]

SYSTEM_PROMPT = (
    "You are a structured judgment function invoked by a program, not a chat "
    "assistant. You receive a JSON `state` and typed questions about it. Judge "
    "only from `state`; you have no tools. Reply with exactly one JSON object and "
    "nothing else: no Markdown, no code fences, no commentary."
)

OUTPUT_CONTRACT = (
    'Answer every question in QUESTIONS. Output {"answers": {<question id>: <answer>}}. '
    'For a choice question the answer is {"choice": <exactly one key of its criteria>, '
    '"confidence": <number from 0 to 1>}. For a noul question the answer is '
    '{"noul": <probability from 0 to 1 that the answer is yes>}. For a score question '
    'the answer is {"score": <number from 0 to the last level index, where criteria[0] '
    'is level 0>, "confidence": <number from 0 to 1>}.'
)

#: GitHub Copilot 額外 premium request 的牌價（美元／單位），用於估算。
COPILOT_USD_PER_PREMIUM_UNIT = Decimal("0.04")
_DEFAULT_TIMEOUT_S = 300.0
_SECRET_ENV = ("TYPESAFE_API_KEY",)

#: runner callable：在 ``cwd`` 執行 argv，回傳 stdout；失敗 raise ``JudgeError``。
CliJudgeRunner = Callable[[list, Path], str]


def render_user_prompt(request: StructuredRequest) -> str:
    payload = request.to_payload()
    return (
        "STATE:\n"
        + json.dumps(payload["state"], ensure_ascii=False, indent=2)
        + "\n\nQUESTIONS:\n"
        + json.dumps(payload["questions"], ensure_ascii=False, indent=2)
        + "\n\n"
        + OUTPUT_CONTRACT
    )


def extract_json_object(text: str) -> tuple[dict, bool]:
    """取出回覆中的 JSON object；第二個值表示回覆是否「只有」該 object。"""
    stripped = text.strip()
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value, True
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    if start >= 0:
        try:
            value, _end = json.JSONDecoder().raw_decode(stripped[start:])
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            return value, False
    raise JudgeError("invalid_output", f"回覆不含 JSON object：{stripped[:200]!r}")


def _decimal_or_none(value) -> Decimal | None:
    """provider 回報的數字轉 ``Decimal``（經 ``str`` 保留十進位表示）；非數字回 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _child_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _SECRET_ENV}


def build_cli_judge_runner(timeout_s: float = _DEFAULT_TIMEOUT_S) -> CliJudgeRunner:
    def _runner(argv: list, cwd: Path) -> str:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                stdin=subprocess.DEVNULL,
                env=_child_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise JudgeError("transport", f"{argv[0]} 逾時（{timeout_s}s）", retryable=True) from exc
        except OSError as exc:
            raise JudgeError("transport", f"{argv[0]} 無法執行：{exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:500]
            raise JudgeError(
                "transport",
                f"{argv[0]} 以 exit={completed.returncode} 結束：{detail}",
                retryable=True,
            )
        return completed.stdout

    return _runner


class _CliJudge(StructuredJudgeAdapter):
    binary_name = ""

    def __init__(
        self,
        model: str,
        *,
        binary: str | None = None,
        runner: CliJudgeRunner | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_attempts: int = 2,
    ) -> None:
        self.model = model
        self._binary = binary or shutil.which(self.binary_name) or self.binary_name
        self._runner = runner if runner is not None else build_cli_judge_runner()
        self._clock = clock
        self._max_attempts = max(1, max_attempts)

    def judge(self, request: StructuredRequest) -> StructuredResponse:
        user_prompt = render_user_prompt(request)
        started = self._clock()
        attempt = 0
        while True:
            attempt += 1
            with tempfile.TemporaryDirectory(prefix="patchmud-judge-") as tmp:
                workdir = Path(tmp)
                argv = self._build_argv(user_prompt, workdir)
                try:
                    stdout = self._runner(argv, workdir)
                    text, model, cost, usage, api_ms = self._parse(stdout, workdir)
                    break
                except JudgeError as exc:
                    if not exc.retryable or attempt >= self._max_attempts:
                        raise
        wall_ms = round((self._clock() - started) * 1000)
        body, _exact = extract_json_object(text)
        answers = parse_answers(request, body.get("answers"))
        return StructuredResponse(
            answers=answers,
            provider=self.provider,
            model=model,
            wall_ms=wall_ms,
            response_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            cost=cost,
            usage=usage,
            api_ms=api_ms,
            attempts=attempt,
        )

    def _build_argv(self, user_prompt: str, workdir: Path) -> list:
        raise NotImplementedError

    def _parse(self, stdout: str, workdir: Path) -> tuple:
        """回傳 ``(text, model, cost, usage, api_ms)``。"""
        raise NotImplementedError


class ClaudeCliJudge(_CliJudge):
    provider = "claude"
    binary_name = "claude"

    def __init__(self, model: str = "sonnet", **kwargs) -> None:
        super().__init__(model, **kwargs)

    def _build_argv(self, user_prompt: str, workdir: Path) -> list:
        return [
            self._binary,
            "--model",
            self.model,
            "--safe-mode",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--tools",
            "",
            "--no-session-persistence",
            "--system-prompt",
            SYSTEM_PROMPT,
            "--output-format",
            "json",
            "-p",
            user_prompt,
        ]

    def _parse(self, stdout: str, workdir: Path) -> tuple:
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise JudgeError("transport", f"claude 輸出非 JSON：{stdout[:200]!r}") from exc
        if not isinstance(data, dict):
            raise JudgeError("transport", "claude 輸出不是 JSON object")
        if data.get("is_error"):
            raise JudgeError(
                "transport", f"claude 回報錯誤：{str(data.get('result'))[:300]}", retryable=True
            )
        text = data.get("result")
        if not isinstance(text, str):
            raise JudgeError("invalid_output", "claude 輸出缺 result 文字")
        model_usage = data.get("modelUsage")
        model = next(iter(model_usage), self.model) if isinstance(model_usage, dict) and model_usage else self.model
        raw_usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        usage = {k: raw_usage.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
        cost = JudgeCost(
            usd=_decimal_or_none(data.get("total_cost_usd")),
            basis="claude_cli_reported_total_cost_usd",
            units=usage,
        )
        api_ms = data.get("duration_api_ms")
        return text, model, cost, usage, api_ms if isinstance(api_ms, int) else None


def copilot_user_mcp_servers(config_path: Path) -> list:
    """列出 Copilot 使用者 MCP 設定中的 server 名稱，供逐一停用。"""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return sorted(servers) if isinstance(servers, dict) else []


class CopilotCliJudge(_CliJudge):
    provider = "copilot"
    binary_name = "copilot"
    usage_file_name = "copilot-usage.json"

    def __init__(
        self, model: str = "gpt-5.4", *, disabled_mcp_servers: tuple = (), **kwargs
    ) -> None:
        super().__init__(model, **kwargs)
        self._disabled_mcp_servers = tuple(disabled_mcp_servers)

    def _build_argv(self, user_prompt: str, workdir: Path) -> list:
        argv = [
            self._binary,
            "-p",
            SYSTEM_PROMPT + "\n\n" + user_prompt,
            "--model",
            self.model,
            "--available-tools",
            "--no-custom-instructions",
            "--disable-builtin-mcps",
        ]
        for name in self._disabled_mcp_servers:
            argv += ["--disable-mcp-server", name]
        argv += [
            "--output-format",
            "json",
            "--usage-output-file",
            str(workdir / self.usage_file_name),
            "--no-auto-update",
            "--no-color",
        ]
        return argv

    def _parse(self, stdout: str, workdir: Path) -> tuple:
        text = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "assistant.message":
                content = (event.get("data") or {}).get("content")
                if isinstance(content, str) and content.strip():
                    text = content
        if text is None:
            raise JudgeError("invalid_output", "copilot 輸出沒有 assistant.message")
        model = self.model
        usage: dict = {}
        units = None
        api_ms = None
        try:
            report = json.loads((workdir / self.usage_file_name).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = None
        if isinstance(report, dict):
            metrics = report.get("modelMetrics")
            if isinstance(metrics, dict) and metrics:
                model = next(iter(metrics))
                model_usage = (metrics[model] or {}).get("usage") or {}
                usage = {
                    "input_tokens": model_usage.get("inputTokens"),
                    "output_tokens": model_usage.get("outputTokens"),
                    "reasoning_tokens": model_usage.get("reasoningTokens"),
                }
            units = report.get("totalPremiumRequestCost")
            api_ms = report.get("totalApiDurationMs")
        unit_count = _decimal_or_none(units)
        if unit_count is not None:
            cost = JudgeCost(
                usd=unit_count * COPILOT_USD_PER_PREMIUM_UNIT,
                basis="copilot_premium_request_units_x_usd_0.04_estimated",
                units={"premium_request_units": units, **usage},
            )
        else:
            cost = JudgeCost(usd=None, basis="copilot_usage_unavailable", units=usage)
        return text, model, cost, usage, api_ms if isinstance(api_ms, int) else None
