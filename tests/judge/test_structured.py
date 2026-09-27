"""structured-judge 契約：typed question 序列化、canonical hash、answer 驗證。"""

from __future__ import annotations

import pytest

from patchmud.judge.structured import (
    ChoiceAnswer,
    ChoiceQuestion,
    JudgeError,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    StructuredRequest,
    parse_answers,
)

CHOICE = ChoiceQuestion(instructions="Pick one.", criteria={"a": "first", "b": "second", "c": None})
NOUL = NoulQuestion(instructions="Is it urgent?", criteria_true="yes", criteria_false="no")
SCORE = ScoreQuestion(instructions="How severe?", levels=("low", "mid", "high"))


def _request(**questions):
    return StructuredRequest(state={"text": "hello"}, questions=questions)


def test_payload_matches_typesafe_question_shapes():
    payload = _request(pick=CHOICE, urgent=NOUL, severity=SCORE).to_payload()
    assert payload["questions"]["pick"] == {
        "type": "choice",
        "instructions": "Pick one.",
        "criteria": {"a": "first", "b": "second", "c": None},
    }
    assert payload["questions"]["urgent"] == {
        "type": "noul",
        "instructions": "Is it urgent?",
        "criteria": {"true": "yes", "false": "no"},
    }
    assert payload["questions"]["severity"] == {
        "type": "score",
        "instructions": "How severe?",
        "criteria": ["low", "mid", "high"],
    }
    assert "criteria" not in NoulQuestion(instructions="bare").to_payload()


def test_request_hash_is_order_independent_and_content_sensitive():
    a = StructuredRequest(state={"x": 1, "y": 2}, questions={"q": NOUL})
    b = StructuredRequest(state={"y": 2, "x": 1}, questions={"q": NOUL})
    c = StructuredRequest(state={"x": 1, "y": 3}, questions={"q": NOUL})
    assert a.sha256() == b.sha256()
    assert a.sha256() != c.sha256()


def test_question_bounds_are_enforced():
    with pytest.raises(ValueError):
        ScoreQuestion(instructions="x", levels=("only",))
    with pytest.raises(ValueError):
        ChoiceQuestion(instructions="x", criteria={})
    with pytest.raises(ValueError):
        StructuredRequest(state={}, questions={})


def test_parse_answers_accepts_valid_typed_answers():
    request = _request(pick=CHOICE, urgent=NOUL, severity=SCORE)
    parsed = parse_answers(
        request,
        {
            "pick": {"type": "choice", "choice": "b", "confidence": 0.8, "probabilities": {"a": 0.1, "b": 0.88, "c": 0.02}},
            "urgent": {"type": "noul", "noul": 0.95},
            "severity": {"score": 1.2, "confidence": 0.7, "probabilities": {"0": 0.0, "1": 0.8, "2": 0.2}},
        },
    )
    assert parsed["pick"] == ChoiceAnswer(choice="b", confidence=0.8, probabilities={"a": 0.1, "b": 0.88, "c": 0.02})
    assert parsed["urgent"] == NoulAnswer(noul=0.95)
    assert isinstance(parsed["severity"], ScoreAnswer) and parsed["severity"].score == 1.2


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"pick": {"choice": "z"}},
        {"pick": {"choice": "a", "confidence": 1.5}},
        {"pick": {"choice": "a", "probabilities": {"a": 0.9, "b": 0.05}}},
        {"pick": {"choice": "a", "probabilities": {"a": 0.5, "b": 0.3, "c": 0.3}}},
        {"pick": {"choice": "a", "probabilities": {"a": 0.1, "b": 0.8, "c": 0.1}}},
        {"pick": {"type": "noul", "choice": "a"}},
        {"pick": {"choice": "a"}, "extra": {"noul": 0.1}},
    ],
)
def test_parse_answers_fails_closed_on_invalid_choice(raw):
    with pytest.raises(JudgeError) as exc:
        parse_answers(_request(pick=CHOICE), raw)
    assert exc.value.kind == "invalid_output"


@pytest.mark.parametrize("raw", [{"noul": -0.2}, {"noul": "high"}, {"noul": True}, {}])
def test_parse_answers_fails_closed_on_invalid_noul(raw):
    with pytest.raises(JudgeError):
        parse_answers(_request(urgent=NOUL), {"urgent": raw})


@pytest.mark.parametrize("raw", [{"score": 3.5}, {"score": -1}, {"score": 1, "probabilities": {"0": 1.0}}])
def test_parse_answers_fails_closed_on_invalid_score(raw):
    with pytest.raises(JudgeError):
        parse_answers(_request(severity=SCORE), {"severity": raw})


def test_rounded_probabilities_within_tolerance_are_accepted():
    parsed = parse_answers(
        _request(pick=CHOICE),
        {"pick": {"choice": "a", "probabilities": {"a": 0.34, "b": 0.33, "c": 0.34}}},
    )
    assert parsed["pick"].choice == "a"


def test_judge_error_kind_is_validated():
    with pytest.raises(ValueError):
        JudgeError("bogus", "x")


def test_judge_cost_requires_decimal_and_serializes_as_string():
    from decimal import Decimal

    from patchmud.judge.structured import JudgeCost

    assert JudgeCost(usd=Decimal("0.1"), basis="b").to_dict()["usd"] == "0.1"
    assert JudgeCost(usd=None, basis="b").to_dict()["usd"] is None
    with pytest.raises(TypeError):
        JudgeCost(usd=0.1, basis="b")
