"""versus 戰報白話推導：純函式，所有文案經 render pack（禁硬編中文）。

把「事件＋結果」翻成人話——一句 bug 說明、每回合做了什麼、收尾誰贏在哪——
不觸碰 benchmark 語意與隔離，純呈現層。
"""

from __future__ import annotations

from patchmud.engine import render_zh_tw as zh

__all__ = ["format_power", "encounter_briefing", "narrate_round", "verdict"]


def format_power(value: object) -> str:
    """Power 分數顯示：四捨五入到 1 位小數；非數值（含 bool）→ na。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return zh.text("na")
    return f"{round(float(value), 1):.1f}"


def encounter_briefing(card: object) -> str:
    """一句話 bug 說明：有 card.briefing 用它，否則退回第一條 public 需求。"""
    briefing = getattr(card, "briefing", None)
    if briefing:
        return str(briefing)
    reqs = getattr(card, "public_requirements", ()) or ()
    return reqs[0].text if reqs else zh.text("na")


def narrate_round(rd: dict) -> str:
    """單一回合 → 一句人話。rd 鍵見 versus._timeline。"""
    after = rd.get("backlog_after")
    if rd.get("baseline"):
        return zh.text("versus.round.baseline", after=after)
    action = rd.get("action") or zh.text("na")
    outcome = rd.get("outcome")
    resolved = rd.get("resolved") or []
    claim = rd.get("claim")
    claim_text = f"（意圖：「{claim}」）" if claim else ""
    if outcome == "parse_error":
        return zh.text("versus.round.parse_error", after=after) + claim_text
    if outcome == "illegal":
        return zh.text("versus.round.illegal", action=action, after=after) + claim_text
    if resolved:
        return (
            zh.text(
                "versus.round.resolved",
                action=action,
                ids="、".join(resolved),
                before=rd.get("backlog_before"),
                after=after,
            )
            + claim_text
        )
    if action == "PATCH":
        key = (
            "versus.round.patch_failed"
            if outcome == "error"
            else "versus.round.patch_noop"
        )
        return zh.text(key, after=after) + claim_text
    if action == "COMMIT":
        return zh.text("versus.round.commit", after=after) + claim_text
    return zh.text("versus.round.observed", action=action, after=after) + claim_text


def _power_num(entry: object) -> float:
    power = entry.result.get("power")
    if isinstance(power, dict) and isinstance(power.get("total"), (int, float)):
        return float(power["total"])
    return 0.0


def _turns(entry: object) -> int:
    turns = entry.result.get("turns")
    return turns if isinstance(turns, int) else 0


def _reason(result: dict) -> str:
    if not result.get("main_public_green", False):
        return zh.text("versus.reason.public_red")
    gates = result.get("gates") or {}
    if not gates.get("critical_pass", False):
        return zh.text("versus.reason.critical_red")
    return zh.text("versus.reason.other")


def _short(model: str) -> str:
    from patchmud.engine.versus import _short as short

    return short(model)


def verdict(entries: list) -> str:
    """收尾判詞：誰贏在哪。"""
    if not entries:
        return zh.text("versus.verdict.none_clear")
    cleared = [e for e in entries if e.result.get("clear") == 1]
    if not cleared:
        return zh.text("versus.verdict.none_clear")
    if len(cleared) == len(entries):
        fastest = min(entries, key=lambda e: (_turns(e), -_power_num(e)))
        return zh.text(
            "versus.verdict.all_clear",
            fastest=_short(fastest.model),
            turns=_turns(fastest),
        )
    losers = [e for e in entries if e.result.get("clear") != 1]
    return zh.text(
        "versus.verdict.split",
        winners="、".join(_short(e.model) for e in cleared),
        losers="、".join(_short(e.model) for e in losers),
        reason=_reason(losers[0].result),
    )
