"""Task 10 RED：回合協定 parser（spec §5.1–5.2、報告 §9.3、plan Task 10）。

鎖定：
- 報告 §9.3 格式（``ACTION:`` / ``TARGET_ISSUES:`` / ``FILES:`` / ``CLAIM:`` /
  ``PATCH:`` 區塊）的合法回覆逐命令解析成 `Action` dataclass 家族。
- 缺 ``ACTION:`` / 未知動作 / PATCH 無 diff 區塊 → `ParseFailure`，
  錯誤提示為 zh-TW（出自 render pack，非 protocol.py 硬編）。
- diff payload 逐 byte 保留（whitespace 敏感）；code fence 包裹時剝殼。
"""

from __future__ import annotations

from patchmud.engine.protocol import (
    Action,
    Commit,
    Inspect,
    Look,
    ParseFailure,
    Patch,
    PlayPlan,
    Rollback,
    RunTest,
    SummonReviewer,
    Triage,
    WriteTest,
    parse_reply,
)

DIFF = (
    "--- a/src/example.py\n"
    "+++ b/src/example.py\n"
    "@@ -1,2 +1,3 @@\n"
    " def f():\n"
    "-    return 1\n"
    "+    # fixed\n"
    "+    return 2\n"
)


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


# ---------------------------------------------------------------------------
# 合法回覆逐命令解析（報告 §9.3 格式）
# ---------------------------------------------------------------------------


class TestParseEachCommand:
    def test_look_minimal(self) -> None:
        action = parse_reply("ACTION: LOOK\n")
        assert isinstance(action, Look)
        assert action.target_issues == ()
        assert action.files == ()
        assert action.claim is None

    def test_inspect_carries_path(self) -> None:
        action = parse_reply("ACTION: INSPECT src/inventory.py\n")
        assert isinstance(action, Inspect)
        assert action.path == "src/inventory.py"

    def test_play_plan_carries_plan_block(self) -> None:
        reply = (
            "ACTION: PLAY PLAN\n"
            "PLAN:\n"
            "requirements: [MAIN-1]\n"
            "invariants: [no negative stock]\n"
        )
        action = parse_reply(reply)
        assert isinstance(action, PlayPlan)
        assert "requirements: [MAIN-1]" in action.payload
        assert "invariants: [no negative stock]" in action.payload

    def test_write_test_carries_diff(self) -> None:
        reply = "ACTION: WRITE_TEST\nFILES: tests/agent/test_new.py\nPATCH:\n" + DIFF
        action = parse_reply(reply)
        assert isinstance(action, WriteTest)
        assert action.files == ("tests/agent/test_new.py",)
        assert action.payload == DIFF

    def test_patch_full_report_9_3_example(self) -> None:
        reply = (
            "ACTION: PATCH\n"
            "TARGET_ISSUES: MAIN-1, REG-2\n"
            "FILES: src/example.py, tests/test_example.py\n"
            "CLAIM: 修復 recovery 後的重複事件\n"
            "PATCH:\n" + DIFF
        )
        action = parse_reply(reply)
        assert isinstance(action, Patch)
        assert action.target_issues == ("MAIN-1", "REG-2")
        assert action.files == ("src/example.py", "tests/test_example.py")
        assert action.claim == "修復 recovery 後的重複事件"
        # diff payload 逐 byte 保留（whitespace 敏感）
        assert action.payload == DIFF

    def test_run_test_without_target(self) -> None:
        action = parse_reply("ACTION: RUN_TEST\n")
        assert isinstance(action, RunTest)
        assert action.target is None

    def test_run_test_with_target(self) -> None:
        action = parse_reply("ACTION: RUN_TEST tests/agent/test_new.py\n")
        assert isinstance(action, RunTest)
        assert action.target == "tests/agent/test_new.py"

    def test_summon_reviewer(self) -> None:
        assert isinstance(parse_reply("ACTION: SUMMON REVIEWER\n"), SummonReviewer)

    def test_triage(self) -> None:
        action = parse_reply("ACTION: TRIAGE\nTARGET_ISSUES: DUP-1\n")
        assert isinstance(action, Triage)
        assert action.target_issues == ("DUP-1",)

    def test_rollback(self) -> None:
        assert isinstance(parse_reply("ACTION: ROLLBACK\n"), Rollback)

    def test_commit(self) -> None:
        action = parse_reply("ACTION: COMMIT\nCLAIM: 完成修復\n")
        assert isinstance(action, Commit)
        assert action.claim == "完成修復"

    def test_every_action_is_action_subclass(self) -> None:
        for reply in ("ACTION: LOOK", "ACTION: COMMIT", "ACTION: PATCH\nPATCH:\n" + DIFF):
            assert isinstance(parse_reply(reply), Action)


# ---------------------------------------------------------------------------
# 容錯：preamble、code fence、header 空欄位
# ---------------------------------------------------------------------------


class TestParseTolerance:
    def test_preamble_before_action_ignored(self) -> None:
        reply = "我先觀察一下戰場。\n\nACTION: LOOK\n"
        assert isinstance(parse_reply(reply), Look)

    def test_code_fenced_diff_unwrapped(self) -> None:
        reply = "ACTION: PATCH\nPATCH:\n```diff\n" + DIFF + "```\n"
        action = parse_reply(reply)
        assert isinstance(action, Patch)
        assert action.payload == DIFF

    def test_empty_header_fields_normalized(self) -> None:
        reply = "ACTION: PATCH\nTARGET_ISSUES:\nFILES:\nCLAIM:\nPATCH:\n" + DIFF
        action = parse_reply(reply)
        assert isinstance(action, Patch)
        assert action.target_issues == ()
        assert action.files == ()
        assert action.claim is None


# ---------------------------------------------------------------------------
# ParseFailure：缺 ACTION / 未知動作 / PATCH 無 diff 區塊（提示 zh-TW）
# ---------------------------------------------------------------------------


class TestParseFailure:
    def test_missing_action_line(self) -> None:
        failure = parse_reply("我想先修 MAIN-1，請稍等。\n")
        assert isinstance(failure, ParseFailure)
        assert "ACTION" in failure.hint
        assert _has_cjk(failure.hint)

    def test_empty_action_keyword(self) -> None:
        failure = parse_reply("ACTION:\n")
        assert isinstance(failure, ParseFailure)
        assert _has_cjk(failure.hint)

    def test_unknown_action(self) -> None:
        failure = parse_reply("ACTION: DANCE\n")
        assert isinstance(failure, ParseFailure)
        assert "DANCE" in failure.hint
        assert _has_cjk(failure.hint)

    def test_play_tdd_not_in_mvp_command_set(self) -> None:
        # spec §5.1 的 MVP 命令集不含 PLAY TDD（報告 §9.2 有、spec 已收斂）。
        failure = parse_reply("ACTION: PLAY TDD\n")
        assert isinstance(failure, ParseFailure)
        assert _has_cjk(failure.hint)

    def test_patch_without_diff_block(self) -> None:
        failure = parse_reply("ACTION: PATCH\nCLAIM: 修好了\n")
        assert isinstance(failure, ParseFailure)
        assert "PATCH" in failure.hint
        assert _has_cjk(failure.hint)

    def test_patch_with_empty_diff_block(self) -> None:
        failure = parse_reply("ACTION: PATCH\nPATCH:\n\n")
        assert isinstance(failure, ParseFailure)
        assert _has_cjk(failure.hint)

    def test_write_test_without_diff_block(self) -> None:
        failure = parse_reply("ACTION: WRITE_TEST\n")
        assert isinstance(failure, ParseFailure)
        assert "WRITE_TEST" in failure.hint
        assert _has_cjk(failure.hint)

    def test_inspect_without_path(self) -> None:
        failure = parse_reply("ACTION: INSPECT\n")
        assert isinstance(failure, ParseFailure)
        assert "INSPECT" in failure.hint
        assert _has_cjk(failure.hint)

    def test_parse_failure_is_not_an_action(self) -> None:
        failure = parse_reply("nonsense")
        assert isinstance(failure, ParseFailure)
        assert not isinstance(failure, Action)
