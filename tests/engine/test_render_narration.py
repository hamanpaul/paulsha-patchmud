"""Part A：白話戰報推導純函式（format_power / narrate_round / verdict / briefing）。"""

from __future__ import annotations

from types import SimpleNamespace

from patchmud.engine.narration import (
    encounter_briefing,
    format_power,
    narrate_round,
    verdict,
)
from patchmud.engine.versus import VersusEntry


class TestFormatPower:
    def test_rounds_to_one_decimal(self):
        assert format_power(27.714285714285715) == "27.7"
        assert format_power(97.0) == "97.0"

    def test_non_number_is_na(self):
        assert format_power("NA") == "NA"
        assert format_power(None) == "NA"
        assert format_power(True) == "NA"  # bool 不是分數


class TestEncounterBriefing:
    def test_uses_briefing_when_present(self):
        card = SimpleNamespace(briefing="值裡有 = 會被切爛", public_requirements=())
        assert encounter_briefing(card) == "值裡有 = 會被切爛"

    def test_falls_back_to_first_requirement(self):
        req = SimpleNamespace(text="只在第一個 = 切分")
        card = SimpleNamespace(briefing=None, public_requirements=(req,))
        assert encounter_briefing(card) == "只在第一個 = 切分"


class TestNarrateRound:
    def test_baseline(self):
        assert "待辦 1" in narrate_round({"baseline": True, "backlog_after": 1})

    def test_resolved(self):
        s = narrate_round(
            {
                "baseline": False,
                "action": "PATCH",
                "outcome": "executed",
                "resolved": ["MAIN-1"],
                "backlog_before": 1,
                "backlog_after": 0,
            }
        )
        assert "修好了" in s and "MAIN-1" in s

    def test_patch_noop(self):
        s = narrate_round(
            {
                "baseline": False,
                "action": "PATCH",
                "outcome": "executed",
                "resolved": [],
                "backlog_before": 1,
                "backlog_after": 1,
            }
        )
        assert "沒解決" in s

    def test_patch_failed(self):
        s = narrate_round(
            {
                "baseline": False,
                "action": "PATCH",
                "outcome": "error",
                "resolved": [],
                "backlog_before": 1,
                "backlog_after": 1,
            }
        )
        assert "打空" in s

    def test_commit(self):
        s = narrate_round(
            {
                "baseline": False,
                "action": "COMMIT",
                "outcome": "executed",
                "resolved": [],
                "backlog_before": 0,
                "backlog_after": 0,
            }
        )
        assert "COMMIT" in s

    def test_parse_error(self):
        s = narrate_round(
            {
                "baseline": False,
                "action": None,
                "outcome": "parse_error",
                "resolved": [],
                "backlog_before": 1,
                "backlog_after": 1,
            }
        )
        assert "讀不懂" in s


def _entry(model, clear, turns, power, main_green=True, crit=True):
    return VersusEntry(
        model=model,
        events=[],
        result={
            "clear": clear,
            "turns": turns,
            "power": {"total": power},
            "main_public_green": main_green,
            "gates": {"critical_pass": crit},
        },
    )


class TestVerdict:
    def test_split_reason_critical(self):
        s = verdict(
            [
                _entry("a", 1, 2, 97.0),
                _entry("b", 0, 2, 27.7, main_green=True, crit=False),
            ]
        )
        assert "隱藏" in s

    def test_split_reason_public(self):
        s = verdict(
            [
                _entry("a", 1, 2, 97.0),
                _entry("b", 0, 2, 20.0, main_green=False, crit=False),
            ]
        )
        assert "公開測試" in s

    def test_all_clear_names_fastest(self):
        s = verdict([_entry("slow", 1, 3, 97.0), _entry("fast", 1, 2, 97.0)])
        assert "fast" in s and "2" in s

    def test_none_clear(self):
        assert "無人通關" in verdict([_entry("a", 0, 2, 10.0)])
