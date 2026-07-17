"""Task 13 RED：reviewer subcall 協定（spec §6.2、F9／F10）。

鎖定：
- findings schema：最多 5 筆，每筆 `{category, severity, summary,
  evidence: [{path, line}]}`；schema 驗證失敗 → invalid（None，成本照計）。
- reviewer 輸入嚴格白名單：issue card 公開部分、當下 cumulative diff、
  public probe 最新結果、作者可見 artifacts（plan、claim 歷史）；
  **不含**作者 transcript、**不含** hidden 資產路徑。
- findings 敘事 render 一律出自 zh-TW render pack。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from patchmud.deck.loader import load_card
from patchmud.engine.plan_schema import PlanArtifact
from patchmud.engine.reviewer import (
    MAX_FINDINGS,
    ReviewerFinding,
    build_reviewer_messages,
    parse_findings,
    render_findings,
)
from tests.evaluator.helpers import make_outcome

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"
CARD = load_card(FIXTURE / "card.yaml")
MAIN_PROBE = CARD.public_requirements[0].probe
HIDDEN_PROBE = CARD.critical_requirements[0].hidden_probe

VALID_YAML = textwrap.dedent(
    """\
    findings:
      - category: correctness
        severity: high
        summary: remove() 對缺貨數量未做防護
        evidence:
          - {path: src/inventory.py, line: 15}
      - category: robustness
        severity: low
        summary: 邊界條件缺測試
        evidence: []
    """
)

PLAN = PlanArtifact(
    requirements=("MAIN-1",),
    invariants=("庫存不得為負",),
    files_to_inspect=("src/inventory.py",),
    risks=("KeyError 邊界",),
    test_targets=("tests/agent/test_new.py",),
)


# ---------------------------------------------------------------------------
# findings schema（§6.2：最多 5 筆；驗證失敗 → invalid）
# ---------------------------------------------------------------------------


class TestParseFindings:
    def test_valid_findings_roundtrip(self) -> None:
        findings = parse_findings(VALID_YAML)
        assert findings is not None
        assert len(findings) == 2
        first = findings[0]
        assert isinstance(first, ReviewerFinding)
        assert first.category == "correctness"
        assert first.severity == "high"
        assert "remove()" in first.summary
        assert first.evidence[0].path == "src/inventory.py"
        assert first.evidence[0].line == 15
        # 第二筆允許空 evidence 列表
        assert findings[1].evidence == ()

    def test_code_fence_stripped(self) -> None:
        fenced = "```yaml\n" + VALID_YAML + "```\n"
        findings = parse_findings(fenced)
        assert findings is not None and len(findings) == 2

    def test_empty_findings_list_is_valid(self) -> None:
        findings = parse_findings("findings: []\n")
        assert findings == ()

    def test_more_than_five_findings_invalid(self) -> None:
        assert MAX_FINDINGS == 5
        items = "\n".join(
            f"  - {{category: c{i}, severity: low, summary: s{i}, evidence: []}}"
            for i in range(6)
        )
        assert parse_findings("findings:\n" + items) is None

    def test_missing_field_invalid(self) -> None:
        text = textwrap.dedent(
            """\
            findings:
              - category: correctness
                severity: high
                evidence: []
            """
        )
        assert parse_findings(text) is None

    def test_bad_evidence_invalid(self) -> None:
        # line 非 int → invalid
        text = textwrap.dedent(
            """\
            findings:
              - category: correctness
                severity: high
                summary: s
                evidence:
                  - {path: src/inventory.py, line: twelve}
            """
        )
        assert parse_findings(text) is None
        # evidence 非列表 → invalid
        text2 = textwrap.dedent(
            """\
            findings:
              - category: correctness
                severity: high
                summary: s
                evidence: src/inventory.py
            """
        )
        assert parse_findings(text2) is None

    def test_non_yaml_or_non_mapping_invalid(self) -> None:
        assert parse_findings("findings: [unclosed") is None
        assert parse_findings("只是一段散文，不是 YAML mapping") is None


# ---------------------------------------------------------------------------
# reviewer 輸入白名單（§6.2：不含 transcript、不含 hidden 資產）
# ---------------------------------------------------------------------------


class TestBuildReviewerMessages:
    def _joined(self, messages: list[dict]) -> str:
        return "\n".join(str(m.get("content", "")) for m in messages)

    def test_whitelist_contents_present(self) -> None:
        diff = "--- a/src/inventory.py\n+++ b/src/inventory.py\n+x\n"
        messages = build_reviewer_messages(
            CARD,
            cumulative_diff=diff,
            probe_results={MAIN_PROBE: make_outcome("failed")},
            plan=PLAN,
            claims=("turn 1: 修 remove 缺貨防護",),
        )
        assert messages and all({"role", "content"} <= set(m) for m in messages)
        joined = self._joined(messages)
        # 白名單四成分都在：diff、probe 結果、plan、claim 歷史
        assert diff.strip() in joined
        assert MAIN_PROBE in joined and "failed" in joined
        assert "src/inventory.py" in joined
        assert "修 remove 缺貨防護" in joined
        # card 公開部分（issue id 與公開需求）
        assert CARD.issue_id in joined
        assert CARD.public_requirements[0].text in joined

    def test_no_hidden_assets_leaked(self) -> None:
        messages = build_reviewer_messages(
            CARD,
            cumulative_diff="",
            probe_results={MAIN_PROBE: make_outcome("failed")},
            plan=None,
            claims=(),
        )
        joined = self._joined(messages)
        assert "hidden/" not in joined
        assert HIDDEN_PROBE not in joined


# ---------------------------------------------------------------------------
# findings render（zh-TW 敘事；命令與 artifact 關鍵字英文）
# ---------------------------------------------------------------------------


class TestRenderFindings:
    def test_findings_rendered_zh(self) -> None:
        findings = parse_findings(VALID_YAML)
        assert findings is not None
        text = render_findings(findings)
        assert "審查" in text
        assert "2" in text  # 筆數
        assert "remove()" in text
        assert "high" in text and "correctness" in text  # 結構欄位維持英文
        assert "src/inventory.py:15" in text

    def test_zero_findings_rendered_zh(self) -> None:
        text = render_findings(())
        assert "審查" in text
