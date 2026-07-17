"""Task 22 RED：`patchmud play` e2e——人類親自對局（spec §5.4；plan Task 22）。

以 scripted `input_fn` 餵完整命令序列打完 `mini_encounter`：
- run 完成且 result.yaml 有 `human: true`；
- ledger 全 token 欄位 `NA`（互斥欄位＋billed totals）、成本 `NA`；
- 互動順序正確（先看到 render 再要求輸入）；
- metrics 聚合函數（Task 15）對含 human run 的集合 raise `HumanRunExcluded`。

e2e 走真 bwrap（環境無能力時 skip，比照 test_run_e2e）；HumanRunExcluded
測試為純 unit（不需隔離）。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from patchmud.cli import play_cli
from patchmud.deck.loader import load_card
from patchmud.engine import render_zh_tw as zh
from patchmud.metrics.economy import (
    HumanRunExcluded,
    RunSample,
    cost_per_clear,
    economy_score,
)
from patchmud.metrics.efficiency import (
    RegisteredBudget,
    eutb,
    qaty,
    tokens_per_clear,
)
from patchmud.sandbox.isolate import IsolationRunner
from patchmud.store.replay import replay_l1

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mini_encounter"
CARD = load_card(FIXTURE / "card.yaml")
REFERENCE_DIFF = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")

_BWRAP = Path("/usr/bin/bwrap")

#: 互斥 token 欄位＋billed totals：human run 一律 NA（JSON null，§10.1／§5.4）。
_NA_TOKEN_FIELDS = (
    "input_uncached",
    "input_cached",
    "output_visible",
    "reasoning",
    "billed_input_total",
    "billed_output_total",
)

PATCH_REPLY = (
    "ACTION: PATCH\n"
    "TARGET_ISSUES: MAIN-1\n"
    "CLAIM: 修 remove 的缺貨與缺項防護\n"
    "PATCH:\n" + REFERENCE_DIFF
)
COMMIT_REPLY = "ACTION: COMMIT"


@pytest.fixture()
def real_capabilities():
    if not _BWRAP.exists():
        pytest.skip("環境未安裝 bwrap")
    probe = IsolationRunner(Path("/tmp"), bwrap_path=_BWRAP)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


class SpyIO:
    """scripted input_fn ＋ spy output_fn（同一事件序驗互動順序）。"""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.events: list[tuple[str, ...]] = []

    def input_fn(self) -> str:
        self.events.append(("input",))
        if not self._replies:
            raise EOFError
        return self._replies.pop(0)

    def output_fn(self, text: object) -> None:
        self.events.append(("output", str(text)))


class TestScriptedHumanPlay:
    def test_scripted_input_clears_and_marks_human(
        self, real_capabilities, tmp_path
    ) -> None:
        io = SpyIO([PATCH_REPLY, COMMIT_REPLY])
        runs_root = tmp_path / "runs"

        result_run = play_cli(
            FIXTURE,
            "P0T0R0",
            runs_root,
            run_id="e2e-play",
            input_fn=io.input_fn,
            output_fn=io.output_fn,
        )
        assert result_run.clear == 1
        assert result_run.end_reason == "commit"

        run_dir = runs_root / "e2e-play"
        result = yaml.safe_load((run_dir / "result.yaml").read_text(encoding="utf-8"))

        # run 完成且標記 human: true（永不進 ranked 資料，spec §5.4）
        assert result["human"] is True
        assert result["clear"] == 1
        assert result["end_reason"] == "commit"
        assert result["turns"] == 2
        assert result["loadout"] == "P0T0R0"

        # ledger 全 token 欄位 NA（JSON null）、成本 NA
        ledger_lines = [
            json.loads(line)
            for line in (run_dir / "ledger.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(ledger_lines) == 2
        for entry in ledger_lines:
            assert entry["role"] == "author"
            for field in _NA_TOKEN_FIELDS:
                assert entry[field] is None, field
        assert result["ledger"]["billed_input_total"] == "NA"
        assert result["ledger"]["billed_output_total"] == "NA"
        assert result["ledger"]["work_tokens"] == "NA"
        assert result["economy"] == "NA"

        # run.yaml 同步標記 human run（play 佈線寫入）
        run_record = yaml.safe_load(
            (run_dir / "run.yaml").read_text(encoding="utf-8")
        )
        assert run_record["human"] is True
        assert run_record["model"] == "human"

        # 互動順序：banner／render 先於第一次要求輸入
        first_input = io.events.index(("input",))
        outputs_before = [
            event[1] for event in io.events[:first_input] if event[0] == "output"
        ]
        assert any(zh.text("play.banner") in text for text in outputs_before)
        assert any("回合 1" in text for text in outputs_before)

        # 評分資料流同構：human run 的 L1 replay 位元一致（spec §5.4、§12.2）
        assert replay_l1(run_dir).identical is True


class TestStdinReply:
    """`patchmud play` 預設 stdin 讀取器（unit；monkeypatch input，不碰終端）。"""

    def _feed(self, monkeypatch, lines: list[str]) -> None:
        source = iter(lines)

        def _input() -> str:
            try:
                return next(source)
            except StopIteration:
                raise EOFError from None

        monkeypatch.setattr("builtins.input", _input)

    def test_whitespace_only_line_is_diff_content(self, monkeypatch) -> None:
        # unified diff 的空 context 行是單一空格：必須保留、不得視為回覆結束
        # （Step 4 手動驗收發現的 playability bug，鎖回歸）
        from patchmud.cli import _stdin_reply

        self._feed(
            monkeypatch,
            ["ACTION: PATCH", "PATCH:", "--- a/x", "+++ b/x", " ", "+new", "", "ignored"],
        )
        reply = _stdin_reply()
        assert reply.splitlines() == [
            "ACTION: PATCH", "PATCH:", "--- a/x", "+++ b/x", " ", "+new",
        ]

    def test_leading_blank_lines_ignored(self, monkeypatch) -> None:
        from patchmud.cli import _stdin_reply

        self._feed(monkeypatch, ["", "", "ACTION: LOOK", ""])
        assert _stdin_reply() == "ACTION: LOOK"

    def test_eof_without_content_propagates(self, monkeypatch) -> None:
        # 無內容的 EOF 上拋 → HumanAdapter 視同 COMMIT 收尾
        from patchmud.cli import _stdin_reply

        self._feed(monkeypatch, [])
        with pytest.raises(EOFError):
            _stdin_reply()

    def test_eof_after_content_ends_reply(self, monkeypatch) -> None:
        from patchmud.cli import _stdin_reply

        self._feed(monkeypatch, ["ACTION: LOOK"])
        assert _stdin_reply() == "ACTION: LOOK"


class TestHumanRunExcludedFromMetrics:
    """Task 15 聚合函數對含 human run 的集合一律 raise（spec §5.4）。"""

    MODEL = RunSample(
        clear=1,
        power=80.0,
        cost=Decimal("2.5"),
        work_tokens=1000,
        observable_tokens=1200,
    )
    HUMAN = RunSample(clear=1, power=80.0, human=True)

    def test_cost_per_clear_rejects_human(self) -> None:
        with pytest.raises(HumanRunExcluded):
            cost_per_clear([self.MODEL, self.HUMAN])

    def test_tokens_per_clear_rejects_human(self) -> None:
        with pytest.raises(HumanRunExcluded):
            tokens_per_clear([self.MODEL, self.HUMAN])

    def test_qaty_rejects_human(self) -> None:
        with pytest.raises(HumanRunExcluded):
            qaty([self.MODEL, self.HUMAN])

    def test_eutb_rejects_human(self) -> None:
        budget = RegisteredBudget(budget_tokens=1000, grid_points=4)
        with pytest.raises(HumanRunExcluded):
            eutb([self.MODEL, self.HUMAN], budget)

    def test_economy_score_rejects_human(self) -> None:
        with pytest.raises(HumanRunExcluded):
            economy_score(self.HUMAN, CARD)
