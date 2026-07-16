"""回合協定 parser：agent 回覆 → `Action` dataclass 家族（spec §5.1–5.2）。

回覆格式沿用報告 §9.3：``ACTION:`` / ``TARGET_ISSUES:`` / ``FILES:`` /
``CLAIM:`` header 行，加上 ``PATCH:``（unified diff）或 ``PLAN:``（plan YAML）
payload 區塊。命令關鍵字維持英文；解析失敗回 :class:`ParseFailure`，
錯誤提示為 zh-TW、一律出自 render pack 查表（本模組不得硬編中文字串）。

解析語意（fail 條件依 plan Task 10 pin）：
- 缺 ``ACTION:`` 行 / ``ACTION:`` 後空白 → `ParseFailure`。
- 未知動作（含報告 §9.2 有、spec §5.1 MVP 命令集已收斂掉的 ``PLAY TDD`` /
  ``STANCE SOLO``）→ `ParseFailure`。
- ``PATCH`` / ``WRITE_TEST`` 無非空 ``PATCH:`` 區塊 → `ParseFailure`；
  ``INSPECT`` 無路徑 → `ParseFailure`。
- ``PLAY PLAN`` 的 ``PLAN:`` 區塊缺席時 payload 為空字串——schema 驗證
  （§6.3，Task 12 `validate_plan`）會判 illegal，屬 illegal 而非 parse error。
- payload 逐 byte 保留（diff whitespace 敏感）；模型以 code fence 包裹時剝殼。
- ``ACTION:`` 行之前的 preamble 文字忽略（模型常見行為，容錯不懲罰）。

合法性（loadout、路徑白名單、R1 gate…）不在本層：parser 只管結構，
判 illegal 是 strategy enforcer（Task 12）與 turn loop（Task 13）的事。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from patchmud.engine import render_zh_tw as zh

__all__ = [
    "Action",
    "Commit",
    "Inspect",
    "Look",
    "ParseFailure",
    "Patch",
    "PlayPlan",
    "Rollback",
    "RunTest",
    "SummonReviewer",
    "Triage",
    "WriteTest",
    "parse_reply",
]

_ACTION_PREFIX = "ACTION:"
_HEADER_KEYS = ("TARGET_ISSUES", "FILES", "CLAIM")
#: payload 區塊標記：PATCH（unified diff）與 PLAN（plan YAML）。
_PAYLOAD_MARKERS = ("PATCH", "PLAN")


@dataclass(frozen=True)
class Action:
    """所有命令共通的 header 欄位（報告 §9.3）。"""

    keyword: ClassVar[str] = ""

    target_issues: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    claim: str | None = None


@dataclass(frozen=True)
class Look(Action):
    keyword: ClassVar[str] = "LOOK"


@dataclass(frozen=True)
class Inspect(Action):
    keyword: ClassVar[str] = "INSPECT"

    #: sandbox 內路徑；parser 保證非空（黑名單／存在性檢查是引擎的事）。
    path: str = ""


@dataclass(frozen=True)
class PlayPlan(Action):
    keyword: ClassVar[str] = "PLAY PLAN"

    #: PLAN: 區塊原文（plan YAML）；schema 驗證見 §6.3（Task 12）。
    payload: str = ""


@dataclass(frozen=True)
class WriteTest(Action):
    keyword: ClassVar[str] = "WRITE_TEST"

    #: test-only unified diff；parser 保證非空。
    payload: str = ""


@dataclass(frozen=True)
class Patch(Action):
    keyword: ClassVar[str] = "PATCH"

    #: production unified diff；parser 保證非空（apply 失敗是 error result）。
    payload: str = ""


@dataclass(frozen=True)
class RunTest(Action):
    keyword: ClassVar[str] = "RUN_TEST"

    #: 指定 target（probe id 或 tests/agent/** 路徑）；None = 全部白名單套件。
    target: str | None = None


@dataclass(frozen=True)
class SummonReviewer(Action):
    keyword: ClassVar[str] = "SUMMON REVIEWER"


@dataclass(frozen=True)
class Triage(Action):
    keyword: ClassVar[str] = "TRIAGE"


@dataclass(frozen=True)
class Rollback(Action):
    keyword: ClassVar[str] = "ROLLBACK"


@dataclass(frozen=True)
class Commit(Action):
    keyword: ClassVar[str] = "COMMIT"


@dataclass(frozen=True)
class ParseFailure:
    """結構化 parse error：hint 為 zh-TW 格式提示（spec §5.2，消耗一 turn）。"""

    hint: str


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _strip_fences(lines: list[str]) -> list[str]:
    """剝除 payload 首尾空行與包裹整塊的 code fence（內容逐 byte 保留）。"""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if (
        len(lines) >= 2
        and lines[0].strip().startswith("```")
        and lines[-1].strip() == "```"
    ):
        lines = lines[1:-1]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
    return lines


def _extract(text: str) -> tuple[str, dict[str, str], dict[str, str]] | None:
    """拆出 action spec、header 欄位與 payload 區塊；無 ACTION 行回 None。"""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip().startswith(_ACTION_PREFIX)),
        None,
    )
    if start is None:
        return None
    action_spec = lines[start].strip()[len(_ACTION_PREFIX) :].strip()

    headers: dict[str, str] = {}
    payloads: dict[str, str] = {}
    i = start + 1
    while i < len(lines):
        stripped = lines[i].strip()
        marker = next(
            (m for m in _PAYLOAD_MARKERS if stripped.startswith(m + ":")), None
        )
        if marker is not None:
            body = lines[i + 1 :]
            remainder = stripped[len(marker) + 1 :].strip()
            if remainder:
                body = [remainder, *body]
            payload_lines = _strip_fences(list(body))
            payload = "\n".join(payload_lines)
            if payload and not payload.endswith("\n"):
                payload += "\n"
            payloads[marker] = payload
            break
        header = next((h for h in _HEADER_KEYS if stripped.startswith(h + ":")), None)
        if header is not None:
            headers[header] = stripped[len(header) + 1 :].strip()
        i += 1
    return action_spec, headers, payloads


def parse_reply(text: str) -> Action | ParseFailure:
    """解析 agent 回覆為單一 `Action`；結構不合即 `ParseFailure`（zh-TW hint）。"""
    extracted = _extract(text)
    if extracted is None:
        return ParseFailure(hint=zh.text("parse.missing_action"))
    action_spec, headers, payloads = extracted
    if not action_spec:
        return ParseFailure(hint=zh.text("parse.missing_action"))

    common = {
        "target_issues": _split_csv(headers.get("TARGET_ISSUES", "")),
        "files": _split_csv(headers.get("FILES", "")),
        "claim": headers.get("CLAIM", "").strip() or None,
    }
    diff = payloads.get("PATCH", "")
    plan = payloads.get("PLAN", "")

    if action_spec == Look.keyword:
        return Look(**common)
    if action_spec == Inspect.keyword or action_spec.startswith(Inspect.keyword + " "):
        path = action_spec[len(Inspect.keyword) :].strip()
        if not path:
            return ParseFailure(hint=zh.text("parse.missing_inspect_path"))
        return Inspect(path=path, **common)
    if action_spec == PlayPlan.keyword:
        return PlayPlan(payload=plan, **common)
    if action_spec == WriteTest.keyword:
        if not diff:
            return ParseFailure(
                hint=zh.text("parse.missing_diff", action=WriteTest.keyword)
            )
        return WriteTest(payload=diff, **common)
    if action_spec == Patch.keyword:
        if not diff:
            return ParseFailure(
                hint=zh.text("parse.missing_diff", action=Patch.keyword)
            )
        return Patch(payload=diff, **common)
    if action_spec == RunTest.keyword or action_spec.startswith(RunTest.keyword + " "):
        target = action_spec[len(RunTest.keyword) :].strip() or None
        return RunTest(target=target, **common)
    if action_spec == SummonReviewer.keyword:
        return SummonReviewer(**common)
    if action_spec == Triage.keyword:
        return Triage(**common)
    if action_spec == Rollback.keyword:
        return Rollback(**common)
    if action_spec == Commit.keyword:
        return Commit(**common)
    return ParseFailure(hint=zh.text("parse.unknown_action", action=action_spec))
