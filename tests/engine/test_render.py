"""Task 10 RED：zh-TW 狀態 renderer 與 render pack（spec §5.3–5.4、plan Task 10）。

鎖定：
- `render_state(run_state)` 對固定 fixture 輸出**穩定 zh-TW golden 字串**
  （版本化；含「回合」「戰場」「洪水壓力」等敘事詞；命令關鍵字／issue ID／
  flood 狀態 label 維持英文）。
- flood 文案門檻（報告 §7.5：0–1 Stable、2–3 Noisy、4–5 Flooded、6+ Meltdown）
  只影響 render 文案，不進任何分數。
- 文案全部經 `render_zh_tw.py` 查表：`render.py` / `protocol.py` / `prompts.py`
  原始碼內不得出現任何硬編中文字串。
- render pack fail-closed：未知 key / 缺 format 參數 → `RenderError`。
- `HARNESS_PROMPT_VERSION` 含 render pack 版本（spec §5.4）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from patchmud.deck.loader import load_card
from patchmud.engine import prompts, protocol, render
from patchmud.engine import render_zh_tw as zh
from patchmud.engine.prompts import HARNESS_PROMPT_VERSION, build_system_prompt
from patchmud.engine.render import IssueView, RunState, flood_state_key, render_state

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _state(**overrides) -> RunState:
    base = dict(
        turn=2,
        max_turns=8,
        encounter_id="mini-inventory-v1",
        open_issues=(
            IssueView(
                item_id="MAIN-1",
                type="MAIN",
                summary="removing a missing item must raise KeyError",
            ),
            IssueView(
                item_id="REG-2",
                type="REGRESSION",
                summary="真實刪除不再被偵測（tests/starter/test_delete.py 轉紅）",
            ),
        ),
        seconds_left=415,
        tokens_spent=1234,
    )
    base.update(overrides)
    return RunState(**base)


# ---------------------------------------------------------------------------
# golden render（版本化：文案改動必須同步 bump RENDER_PACK_VERSION）
# ---------------------------------------------------------------------------


class TestGoldenRender:
    def test_render_pack_version_pinned_for_golden(self) -> None:
        # golden 字串對應 render pack 1.6.0（play.banner 改寫白話規則，
        # 既有 golden 文案不變）；改文案必須 bump 版本並更新 golden。
        assert zh.RENDER_LANGUAGE == "zh-TW"
        assert zh.RENDER_PACK_VERSION == "1.6.0"

    def test_fixed_run_state_golden(self) -> None:
        golden = (
            "=== 回合 2／8 ===\n"
            "【戰場】encounter mini-inventory-v1\n"
            "【議題佇列】開放 2 項\n"
            "- MAIN-1 [MAIN] removing a missing item must raise KeyError\n"
            "- REG-2 [REGRESSION] 真實刪除不再被偵測"
            "（tests/starter/test_delete.py 轉紅）\n"
            "【資源】剩餘回合 6｜剩餘時間 415 秒｜已用 tokens 1234\n"
            "【洪水壓力】backlog 2 → Noisy：context 與診斷負擔上升"
        )
        assert render_state(_state()) == golden

    def test_narrative_words_present(self) -> None:
        out = render_state(_state())
        assert "回合" in out
        assert "戰場" in out
        assert "洪水壓力" in out

    def test_empty_queue_renders_clean_battlefield(self) -> None:
        out = render_state(_state(open_issues=()))
        assert "開放 0 項" in out
        assert "戰場乾淨" in out
        assert "Stable" in out
        assert "戰場可控" in out

    def test_na_fields_render_as_na(self) -> None:
        out = render_state(_state(seconds_left=None, tokens_spent=None))
        assert "剩餘時間 NA 秒" in out
        assert "已用 tokens NA" in out


# ---------------------------------------------------------------------------
# flood 文案門檻（報告 §7.5；文案不進分數）
# ---------------------------------------------------------------------------


class TestFloodBands:
    @pytest.mark.parametrize(
        ("backlog", "key"),
        [
            (0, "stable"),
            (1, "stable"),
            (2, "noisy"),
            (3, "noisy"),
            (4, "flooded"),
            (5, "flooded"),
            (6, "meltdown"),
            (10, "meltdown"),
        ],
    )
    def test_thresholds(self, backlog: int, key: str) -> None:
        assert flood_state_key(backlog) == key

    def test_meltdown_render(self) -> None:
        issues = tuple(
            IssueView(item_id=f"REG-{i}", type="REGRESSION", summary=f"probe {i} 轉紅")
            for i in range(6)
        )
        out = render_state(_state(open_issues=issues))
        assert "Meltdown" in out
        assert "幾乎無法在剩餘時間收斂" in out


# ---------------------------------------------------------------------------
# 文案單一來源：render pack 查表、fail-closed
# ---------------------------------------------------------------------------


def _literal_strings(module) -> list[str]:
    """收集模組內所有非 docstring 的 string literal（硬編文案偵測用）。"""
    tree = ast.parse(inspect.getsource(module))
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_ids.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_ids
    ]


class TestRenderPack:
    @pytest.mark.parametrize("module", [render, protocol, prompts])
    def test_no_hardcoded_chinese_outside_pack(self, module) -> None:
        offenders = [s for s in _literal_strings(module) if _has_cjk(s)]
        assert not offenders, (
            f"{module.__name__} 內有硬編中文字串 {offenders!r}；"
            "文案必須進 render_zh_tw.py 查表"
        )

    def test_pack_itself_is_the_zh_source(self) -> None:
        assert any(_has_cjk(s) for s in _literal_strings(zh))

    def test_unknown_key_fails_closed(self) -> None:
        with pytest.raises(zh.RenderError):
            zh.text("state.no_such_key")

    def test_missing_format_kwarg_fails_closed(self) -> None:
        with pytest.raises(zh.RenderError):
            zh.text("state.turn_header")  # 缺 turn / max_turns

    def test_known_key_formats(self) -> None:
        assert zh.text("state.turn_header", turn=1, max_turns=6) == "=== 回合 1／6 ==="


# ---------------------------------------------------------------------------
# prompts：HARNESS_PROMPT_VERSION 與 system prompt 模板（spec §5.3–5.4）
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_version_includes_render_pack_version(self) -> None:
        assert zh.RENDER_PACK_VERSION in HARNESS_PROMPT_VERSION
        assert zh.RENDER_LANGUAGE in HARNESS_PROMPT_VERSION

    def test_system_prompt_keeps_english_keywords_zh_narrative(self) -> None:
        card = load_card(FIXTURES / "mini_encounter" / "card.yaml")
        prompt = build_system_prompt(card)
        # 命令關鍵字與 artifact 格式維持英文
        for keyword in (
            "LOOK",
            "INSPECT",
            "PLAY PLAN",
            "WRITE_TEST",
            "PATCH",
            "RUN_TEST",
            "SUMMON REVIEWER",
            "TRIAGE",
            "ROLLBACK",
            "COMMIT",
            "ACTION:",
            "TARGET_ISSUES:",
        ):
            assert keyword in prompt
        # 敘事 zh-TW
        assert _has_cjk(prompt)
        # issue card 公開部分
        assert card.issue_id in prompt
        assert card.public_requirements[0].id in prompt
        assert card.public_requirements[0].text in prompt

    def test_system_prompt_is_deterministic(self) -> None:
        card = load_card(FIXTURES / "mini_encounter" / "card.yaml")
        assert build_system_prompt(card) == build_system_prompt(card)
