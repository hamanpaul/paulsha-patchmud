"""Reviewer subcall 協定：輸入白名單、findings schema、advisory render（spec §6.2）。

- reviewer 使用與作者相同 model snapshot、**全新 context**（self-team track）：
  輸入嚴格限定 issue card 公開部分、當下 cumulative diff、public probe 最新
  結果、作者可見 artifacts（plan、claim 歷史）——不含作者 transcript、不含
  hidden 資產（本模組是唯一組裝點，測試以字串掃描鎖定）。
- 輸出 schema：最多 :data:`MAX_FINDINGS` 筆 finding，每筆
  ``{category, severity, summary, evidence: [{path, line}]}``；驗證失敗 →
  該次 review 記為 ``invalid``（:func:`parse_findings` 回 ``None``），成本照計。
- **findings 是 advisory（F10）**：只 render 給作者（zh-TW render pack）並由
  turn loop 落盤 ``artifacts/review_<n>.yaml``——不生成 queue item、不進
  Flood Index、不進 ranked Control。
- 敘事文字一律出自 zh-TW render pack；findings 欄位名與 severity/category
  值維持英文（結構化協定）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import yaml

from patchmud.deck.model import IssueCard
from patchmud.engine import render_zh_tw as zh
from patchmud.engine.plan_schema import PlanArtifact
from patchmud.sandbox.probes import ProbeOutcome

__all__ = [
    "MAX_FINDINGS",
    "Evidence",
    "ReviewerFinding",
    "build_reviewer_messages",
    "parse_findings",
    "render_findings",
]

#: 單次 review 的 finding 上限（spec §6.2）。
MAX_FINDINGS = 5

_REQUIRED_FIELDS = ("category", "severity", "summary")


@dataclass(frozen=True)
class Evidence:
    """finding 的證據定位：repo-relative path + 行號。"""

    path: str
    line: int


@dataclass(frozen=True)
class ReviewerFinding:
    """一筆 schema-valid 的 reviewer finding（advisory，只落盤與 render）。"""

    category: str
    severity: str
    summary: str
    evidence: tuple[Evidence, ...]


# ---------------------------------------------------------------------------
# 輸入組裝（§6.2 白名單；本模組是 reviewer 輸入的唯一組裝點）
# ---------------------------------------------------------------------------


def build_reviewer_messages(
    card: IssueCard,
    *,
    cumulative_diff: str,
    probe_results: Mapping[str, ProbeOutcome],
    plan: PlanArtifact | None = None,
    claims: Sequence[str] = (),
) -> list[dict]:
    """組 reviewer 的 fresh-context messages（system + 單一 user）。

    白名單四成分：card 公開部分、cumulative diff、public probe 最新結果、
    作者可見 artifacts（plan、claim 歷史）。不接受 transcript 參數——
    結構上排除作者對話洩漏。
    """
    parts = [_card_public_brief(card)]

    diff_lines = [zh.text("reviewer.diff_header")]
    diff_lines.append(cumulative_diff if cumulative_diff.strip() else zh.text("reviewer.diff_empty"))
    parts.append("\n".join(diff_lines))

    probe_lines = [zh.text("reviewer.probes_header")]
    probe_lines.extend(
        zh.text("loop.probe_line", probe_id=probe_id, status=outcome.status)
        for probe_id, outcome in probe_results.items()
    )
    parts.append("\n".join(probe_lines))

    if plan is not None:
        plan_lines = [zh.text("reviewer.plan_header")]
        plan_lines.append(
            yaml.safe_dump(
                {
                    "requirements": list(plan.requirements),
                    "invariants": list(plan.invariants),
                    "files_to_inspect": list(plan.files_to_inspect),
                    "risks": list(plan.risks),
                    "test_targets": list(plan.test_targets),
                },
                sort_keys=False,
                allow_unicode=True,
            ).rstrip()
        )
        parts.append("\n".join(plan_lines))

    if claims:
        claim_lines = [zh.text("reviewer.claims_header")]
        claim_lines.extend(f"- {claim}" for claim in claims)
        parts.append("\n".join(claim_lines))

    return [
        {"role": "system", "content": zh.text("reviewer.system_rules")},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def _card_public_brief(card: IssueCard) -> str:
    """card 公開部分：與作者 system prompt 同一 render pack 模板（無 hidden）。"""
    lines = [
        zh.text(
            "prompt.card_brief",
            issue_id=card.issue_id,
            archetype=card.archetype,
            difficulty=card.difficulty,
            max_turns=card.max_turns,
            wall_clock_seconds=card.wall_clock_seconds,
            allowed_paths=", ".join(card.allowed_paths),
            expected_paths=", ".join(card.expected_paths),
        )
    ]
    lines.extend(
        zh.text("prompt.card_requirement", req_id=req.id, text=req.text)
        for req in card.public_requirements
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 輸出 schema 驗證（失敗 → None = invalid review；成本照計）
# ---------------------------------------------------------------------------


def parse_findings(text: str) -> tuple[ReviewerFinding, ...] | None:
    """reviewer 回覆 → findings tuple；schema 驗證失敗一律回 ``None``。"""
    try:
        data = yaml.safe_load(_strip_fence(text))
    except yaml.YAMLError:
        return None
    if isinstance(data, dict):
        raw = data.get("findings")
    elif isinstance(data, list):
        raw = data  # 容錯：頂層直接是 findings 列表
    else:
        return None
    if not isinstance(raw, list) or len(raw) > MAX_FINDINGS:
        return None

    findings: list[ReviewerFinding] = []
    for item in raw:
        finding = _parse_one(item)
        if finding is None:
            return None
        findings.append(finding)
    return tuple(findings)


def _parse_one(item: object) -> ReviewerFinding | None:
    if not isinstance(item, dict):
        return None
    values: dict[str, str] = {}
    for field in _REQUIRED_FIELDS:
        value = item.get(field)
        if not isinstance(value, str) or not value.strip():
            return None
        values[field] = value.strip()
    raw_evidence = item.get("evidence")
    if not isinstance(raw_evidence, list):
        return None
    evidence: list[Evidence] = []
    for entry in raw_evidence:
        if not isinstance(entry, dict):
            return None
        path = entry.get("path")
        line = entry.get("line")
        if not isinstance(path, str) or not path.strip():
            return None
        if not isinstance(line, int) or isinstance(line, bool):
            return None
        evidence.append(Evidence(path=path.strip(), line=line))
    return ReviewerFinding(evidence=tuple(evidence), **values)


def _strip_fence(text: str) -> str:
    lines = [line for line in text.splitlines()]
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
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# advisory render（zh-TW；只給作者看，不進任何分數）
# ---------------------------------------------------------------------------


def render_findings(findings: tuple[ReviewerFinding, ...]) -> str:
    """findings → zh-TW 敘事（turn loop 於同一 turn 結束時 render 給作者，F9）。"""
    if not findings:
        return zh.text("reviewer.no_findings")
    lines = [zh.text("reviewer.findings_header", count=len(findings))]
    for finding in findings:
        evidence = ", ".join(f"{e.path}:{e.line}" for e in finding.evidence)
        lines.append(
            zh.text(
                "reviewer.finding_item",
                severity=finding.severity,
                category=finding.category,
                summary=finding.summary,
                evidence=evidence or zh.text("reviewer.finding_no_evidence"),
            )
        )
    return "\n".join(lines)
