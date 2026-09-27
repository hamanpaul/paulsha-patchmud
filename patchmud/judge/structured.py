"""Structured-judge 契約：typed request（Choice／Noul／Score）→ typed answer。

這是 JEV P1 實驗（issue #42）的 adapter seam，刻意與 engine 的
``ModelAdapter.complete(messages)`` 分開：judge 不產生文字，只回答預先定義
好答案空間的問題。JEV 原生支援這個形狀；LLM judge 以純補全模擬同一介面
（見 ``patchmud.judge.llm_cli``），兩邊吃同一份 ``StructuredRequest``。

原則：
- request 以 canonical JSON 序列化並取 sha256，證明各 provider 收到同一份
  輸入；answer 一律經 ``parse_answers`` 驗證，不合法 fail-closed。
- ``JudgeError.kind`` 區分技術失敗（``transport``）、判決輸出不合法
  （``invalid_output``）與設定錯誤（``config``）。只有技術失敗可以重試或
  fallback；判決本身不因結果不合意而換 judge 重判（v3 原則 7）。
- 成本一律附 ``basis``，標明是 provider 回報、牌價換算或估算。
"""

from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Union

__all__ = [
    "ChoiceAnswer",
    "ChoiceQuestion",
    "JudgeCost",
    "JudgeError",
    "NoulAnswer",
    "NoulQuestion",
    "ScoreAnswer",
    "ScoreQuestion",
    "StructuredJudgeAdapter",
    "StructuredRequest",
    "StructuredResponse",
    "canonical_json",
    "parse_answers",
    "sha256_json",
]

#: 兩位小數序列化時，每個選項的機率最多偏 0.005；總和容忍 n × 0.005。
_PER_OPTION_ROUNDING = 0.005
_EPSILON = 1e-9


def canonical_json(value: Any) -> str:
    """穩定序列化：key 排序、無多餘空白、保留非 ASCII。"""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class JudgeError(RuntimeError):
    """judge 呼叫失敗；``kind`` 決定能否重試。"""

    KINDS = ("transport", "invalid_output", "config")

    def __init__(
        self, kind: str, message: str, *, status: int | None = None, retryable: bool = False
    ) -> None:
        if kind not in self.KINDS:
            raise ValueError(f"未知的 JudgeError kind：{kind}")
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True)
class ChoiceQuestion:
    """從 ``criteria`` 的選項中挑一個；criteria 值是該選項的 rubric。"""

    instructions: Any
    criteria: Mapping[str, Any]
    type: str = field(default="choice", init=False)

    def __post_init__(self) -> None:
        if not self.criteria:
            raise ValueError("Choice question 至少要有一個選項")
        if len(self.criteria) > 255:
            raise ValueError("Choice question 最多 255 個選項")

    def to_payload(self) -> dict:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True)
class NoulQuestion:
    """是非題；答案是「是」的機率。"""

    instructions: Any
    criteria_true: Any = None
    criteria_false: Any = None
    type: str = field(default="noul", init=False)

    def to_payload(self) -> dict:
        payload: dict = {"type": "noul", "instructions": self.instructions}
        criteria = {}
        if self.criteria_true is not None:
            criteria["true"] = self.criteria_true
        if self.criteria_false is not None:
            criteria["false"] = self.criteria_false
        if criteria:
            payload["criteria"] = criteria
        return payload


@dataclass(frozen=True)
class ScoreQuestion:
    """依有序 levels 評分；答案是 0..len(levels)-1 之間的機率加權值。"""

    instructions: Any
    levels: tuple
    type: str = field(default="score", init=False)

    def __post_init__(self) -> None:
        if not 2 <= len(self.levels) <= 10:
            raise ValueError("Score question 需要 2 到 10 個 levels")

    def to_payload(self) -> dict:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.levels),
        }


Question = Union[ChoiceQuestion, NoulQuestion, ScoreQuestion]


@dataclass(frozen=True)
class StructuredRequest:
    """一次 judge 呼叫：共用的 ``state`` 加上一組具名問題。"""

    state: Any
    questions: Mapping[str, Question]

    def __post_init__(self) -> None:
        if not self.questions:
            raise ValueError("StructuredRequest 至少要有一個問題")

    def to_payload(self) -> dict:
        return {
            "state": self.state,
            "questions": {qid: q.to_payload() for qid, q in self.questions.items()},
        }

    def sha256(self) -> str:
        return sha256_json(self.to_payload())


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float | None = None
    probabilities: dict | None = None
    type: str = field(default="choice", init=False)


@dataclass(frozen=True)
class NoulAnswer:
    noul: float
    type: str = field(default="noul", init=False)


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float | None = None
    probabilities: dict | None = None
    type: str = field(default="score", init=False)


Answer = Union[ChoiceAnswer, NoulAnswer, ScoreAnswer]


@dataclass(frozen=True)
class JudgeCost:
    """一次呼叫的成本；``usd`` 為 ``Decimal`` 或 None（未知），``basis`` 必填。

    金額全程 ``decimal.Decimal``，落盤時序列化為字串，避免 float 誤差。
    """

    usd: Decimal | None
    basis: str
    units: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.usd is not None and not isinstance(self.usd, Decimal):
            raise TypeError("JudgeCost.usd 必須是 Decimal 或 None")

    def to_dict(self) -> dict:
        return {
            "usd": None if self.usd is None else str(self.usd),
            "basis": self.basis,
            "units": dict(self.units),
        }


@dataclass(frozen=True)
class StructuredResponse:
    answers: dict
    provider: str
    model: str
    wall_ms: int
    response_sha256: str
    cost: JudgeCost
    usage: dict = field(default_factory=dict)
    api_ms: int | None = None
    attempts: int = 1


class StructuredJudgeAdapter(ABC):
    """structured judge 的唯一 seam。"""

    #: 紀錄用的 provider 名稱（``jev``／``claude``／``copilot``）。
    provider: str = ""

    @abstractmethod
    def judge(self, request: StructuredRequest) -> StructuredResponse:
        """回答 ``request`` 的全部問題；失敗 raise ``JudgeError``。"""


def _unit_float(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JudgeError("invalid_output", f"{what} 不是數字：{value!r}")
    number = float(value)
    if not math.isfinite(number) or number < -_EPSILON or number > 1 + _EPSILON:
        raise JudgeError("invalid_output", f"{what} 超出 [0, 1]：{value!r}")
    return min(max(number, 0.0), 1.0)


def _probabilities(raw: Any, keys: list[str], what: str) -> dict:
    if not isinstance(raw, Mapping):
        raise JudgeError("invalid_output", f"{what} probabilities 不是 object")
    if set(raw) != set(keys):
        raise JudgeError(
            "invalid_output",
            f"{what} probabilities 的 key {sorted(raw)} 與選項 {sorted(keys)} 不一致",
        )
    values = {key: _unit_float(raw[key], f"{what} probabilities[{key}]") for key in keys}
    tolerance = _PER_OPTION_ROUNDING * len(keys) + _EPSILON
    if abs(sum(values.values()) - 1.0) > tolerance:
        raise JudgeError("invalid_output", f"{what} probabilities 總和偏離 1 超過 {tolerance}")
    return values


def _optional_confidence(raw: Mapping, what: str) -> float | None:
    if "confidence" not in raw or raw["confidence"] is None:
        return None
    return _unit_float(raw["confidence"], f"{what} confidence")


def _parse_choice(question: ChoiceQuestion, raw: Mapping, what: str) -> ChoiceAnswer:
    options = list(question.criteria)
    choice = raw.get("choice")
    if choice not in options:
        raise JudgeError("invalid_output", f"{what} choice {choice!r} 不在選項 {options} 內")
    probabilities = None
    if raw.get("probabilities") is not None:
        probabilities = _probabilities(raw["probabilities"], options, what)
        tolerance = _PER_OPTION_ROUNDING * 2 + _EPSILON
        if probabilities[choice] < max(probabilities.values()) - tolerance:
            raise JudgeError("invalid_output", f"{what} choice 不是最高機率選項")
    return ChoiceAnswer(
        choice=choice,
        confidence=_optional_confidence(raw, what),
        probabilities=probabilities,
    )


def _parse_noul(raw: Mapping, what: str) -> NoulAnswer:
    if "noul" not in raw:
        raise JudgeError("invalid_output", f"{what} 缺 noul")
    return NoulAnswer(noul=_unit_float(raw["noul"], f"{what} noul"))


def _parse_score(question: ScoreQuestion, raw: Mapping, what: str) -> ScoreAnswer:
    top = len(question.levels) - 1
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise JudgeError("invalid_output", f"{what} score 不是數字：{score!r}")
    if not math.isfinite(score) or not -_EPSILON <= score <= top + _EPSILON:
        raise JudgeError("invalid_output", f"{what} score 超出 [0, {top}]：{score!r}")
    probabilities = None
    if raw.get("probabilities") is not None:
        probabilities = _probabilities(
            raw["probabilities"], [str(i) for i in range(top + 1)], what
        )
    return ScoreAnswer(
        score=float(score),
        confidence=_optional_confidence(raw, what),
        probabilities=probabilities,
    )


def parse_answers(request: StructuredRequest, raw_answers: Any) -> dict:
    """驗證 provider 回傳的 answers map；缺題、多題、型別不符皆 fail-closed。"""
    if not isinstance(raw_answers, Mapping):
        raise JudgeError("invalid_output", "answers 不是 object")
    expected = set(request.questions)
    if set(raw_answers) != expected:
        raise JudgeError(
            "invalid_output",
            f"answers 的題目 {sorted(raw_answers)} 與 request {sorted(expected)} 不一致",
        )
    parsed: dict = {}
    for qid, question in request.questions.items():
        raw = raw_answers[qid]
        what = f"answers[{qid}]"
        if not isinstance(raw, Mapping):
            raise JudgeError("invalid_output", f"{what} 不是 object")
        declared = raw.get("type")
        if declared is not None and declared != question.type:
            raise JudgeError("invalid_output", f"{what} type {declared!r} ≠ {question.type!r}")
        if isinstance(question, ChoiceQuestion):
            parsed[qid] = _parse_choice(question, raw, what)
        elif isinstance(question, NoulQuestion):
            parsed[qid] = _parse_noul(raw, what)
        else:
            parsed[qid] = _parse_score(question, raw, what)
    return parsed
