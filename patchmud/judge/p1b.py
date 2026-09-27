"""P1b：JEV 路由＋非程式 AC 拆子句判決，程式類 AC 交給 LLM 整包判決。

依 Paul 09-27 裁決與 ChatGPT R23 規格（``experiments/jev-p1b/README.md``）：

- 路由（``router`` arm）：JEV 只看 AC 與證據清單（``evidence_manifest``：id、
  kind 與由 kind 決定的三個旗標），不看證據內文，避免注入文字影響題型分類。
  問兩題：R1 是否需要推理執行語意、R2 是否有不需模擬程式的直接證據路徑。
  只有 R1＝not_required 且 R2＝yes 才送 JEV，其餘一律送 LLM（刻意保守）。
- 非程式判決（``jdiag`` arm，也是混合系統的 JEV 分支）：AC 預先拆成原子
  子句；程式先依證據種類濾掉宣稱類（PR 說明、review、reviewer note、ticket、
  release note），每個子句對整組剩餘證據問一次 supports／contradicts／
  says_nothing，再由程式彙整。剩餘證據為空時直接判 insufficient、不呼叫 JEV。
- 程式類判決：沿用 P1 的整包三態判決（``runner.whole_pack_arm``），由 Codex
  gpt-6-luna（effort max）與 Claude sonnet-5 擔任。
- 混合系統 H 不另外呼叫 provider：同一題同一 run 由路由結果決定取 ``jdiag``
  或 ``codex`` 的判決（common random numbers，讓 H 與 Codex 全包的差異只來自
  路由與 JEV 切片）。
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import Counter

from patchmud.judge import bank as bank_mod
from patchmud.judge.bank import AC_TYPES, VERDICTS, Bank, BankError, BankItem
from patchmud.judge.runner import Arm, Task, whole_pack_arm
from patchmud.judge.structured import (
    ChoiceAnswer,
    ChoiceQuestion,
    StructuredJudgeAdapter,
    StructuredRequest,
    StructuredResponse,
    sha256_json,
)

__all__ = [
    "AGGREGATION_REVISION",
    "CALL_CAPS",
    "KIND_CLASS",
    "P1B_FROZEN_FILE",
    "RELATION_CRITERIA",
    "ROUTER_QUESTIONS",
    "ROUTER_REVISIONS",
    "check_p1b_composition",
    "decomposed_arm",
    "decomposed_request",
    "freeze_p1b",
    "manifest",
    "p1b_arms",
    "p1b_spec",
    "plan_p1b_tasks",
    "route_request",
    "router_arm",
    "runs_for",
    "verify_p1b_frozen",
]

P1B_FROZEN_FILE = "FROZEN.json"
DEADLINE = "2026-10-03"

#: 證據種類 → 類別。宣稱類在非程式判決前由程式濾掉；未知種類一律拒絕。
KIND_CLASS = {
    "diff": "executable",
    "source": "executable",
    "test": "executable",
    "log": "observation",
    "test_output": "observation",
    "ci_output": "observation",
    "record": "observation",
    "config": "observation",
    "shell": "observation",
    "search": "observation",
    "documentation": "observation",
    "pull_request": "claim",
    "review_comment": "claim",
    "reviewer_note": "claim",
    "ticket": "claim",
    "release_note": "claim",
    "scope_note": "context",
}

ROUTER_QUESTIONS = {
    "r1_execution_reasoning": ChoiceQuestion(
        instructions=(
            "Does reaching a reliable verdict on `acceptance_criterion` require reasoning about how "
            "executable artifacts behave when they run? `evidence_manifest` lists the available "
            "evidence items; their contents are not shown."
        ),
        criteria={
            "required": "The verdict depends on execution semantics: control flow, loops or counts, "
            "exceptions or fallbacks, state mutation, ordering, concurrency or timing, parser or shell "
            "semantics, computed values, or combining an implementation with other evidence to infer "
            "behavior.",
            "not_required": "The verdict can be reached from declarative facts or direct observations "
            "such as logs, records, test output, configuration values, or documentation, without "
            "inferring how code executes.",
            "uncertain": "It is unclear whether execution semantics are needed.",
        },
    ),
    "r2_direct_evidence_path": ChoiceQuestion(
        instructions=(
            "Among the items in `evidence_manifest`, is there a path to a reliable verdict on "
            "`acceptance_criterion` that uses only direct observations or explicit declarative facts, "
            "without simulating or deriving program behavior?"
        ),
        criteria={
            "yes": "At least one listed item that is a direct observation or a declarative fact could "
            "settle the criterion without reasoning about code execution.",
            "no": "Settling the criterion would require reading and reasoning about executable content.",
            "uncertain": "It is unclear whether such a path exists.",
        },
    ),
}

#: 開發集修訂 1（09-27）：rev0 在開發集把記錄裡的標籤（patched、complete）當成支持、
#: 把「未記錄」當成反證；改在 criteria 寫明標籤不是觀測、缺紀錄不是反證。
#: r2（09-27）：r1 在 hidden 把「程式行為 AC＋只有觀測類證據」送給 JEV（unsafe route）。
#: 新增一題只看 AC、不看證據清單的 R0；R0＝no 且 R1、R2 都指向非程式，才送 JEV。
R0_SOFTWARE_BEHAVIOR = ChoiceQuestion(
    instructions=(
        "Consider only `acceptance_criterion` and ignore `evidence_manifest`. Is the criterion a statement "
        "about how software behaves when it runs: what a function, method, API, command-line tool, script, "
        "or code path does, returns, raises, writes, or outputs?"
    ),
    criteria={
        "yes": "The criterion describes behavior of code or of a program when it runs, such as return "
        "values, raised errors, exit statuses, side effects, output format, retries, or state changes.",
        "no": "The criterion states a fact about records, configuration values, deployments, versions, "
        "releases, issues, CI runs, processes, or operational events rather than how code behaves when it runs.",
        "uncertain": "It is unclear which of these the criterion states.",
    },
)

#: 路由問題依協定版本保存；r1 不變，已凍結的 r1 協定因此仍可驗證。
ROUTER_REVISIONS = {
    "r1": dict(ROUTER_QUESTIONS),
    "r2": {"r0_software_behavior": R0_SOFTWARE_BEHAVIOR, **ROUTER_QUESTIONS},
}

RELATION_CRITERIA = {
    "supports": "The evidence directly observes that the claim is true. A label, status, or name that "
    "only asserts the outcome (for example 'patched', 'complete', 'verified', 'production') is not an "
    "observation of it.",
    "contradicts": "The evidence directly observes that the claim is false. A missing entry or a value "
    "recorded as 'not-recorded' or 'unknown' is not an observation that the claim is false.",
    "says_nothing": "The evidence does not directly observe whether the claim is true or false: it is "
    "missing, irrelevant, incomplete, or only carries labels or statuses that assert the outcome.",
}

AGGREGATION_REVISION = (
    "p1b-agg-1 (relation rev1): drop claim-class evidence in code; no remaining evidence -> insufficient; "
    "any clause contradicts -> not_satisfied; all clauses support -> satisfied; otherwise insufficient"
)

#: 呼叫上限（R23 A6）；到上限就停，不因結果接近門檻再加樣本。
CALL_CAPS = {"codex": 60, "claude": 40, "jev_typed_judgments": 300}
RUN_COUNTS = {"base": 1, "variant": 2}


def manifest(item: BankItem) -> list:
    rows = []
    for evidence in item.evidence:
        cls = KIND_CLASS[evidence["kind"]]
        rows.append(
            {
                "id": evidence["id"],
                "kind": evidence["kind"],
                "contains_executable_content": cls == "executable",
                "is_direct_observation": cls == "observation",
                "is_claim_only": cls == "claim",
            }
        )
    return rows


def route_request(item: BankItem, variant: str = "clean", revision: str = "r1") -> StructuredRequest:
    return StructuredRequest(
        state={"acceptance_criterion": item.criterion, "evidence_manifest": manifest(item)},
        questions=dict(ROUTER_REVISIONS[revision]),
    )


def _route_interpret(item: BankItem, response: StructuredResponse | None) -> dict:
    r1 = response.answers["r1_execution_reasoning"]
    r2 = response.answers["r2_direct_evidence_path"]
    r0 = response.answers.get("r0_software_behavior")
    eligible = r1.choice == "not_required" and r2.choice == "yes" and (r0 is None or r0.choice == "no")
    details = {"route": "JEV" if eligible else "LLM"}
    if r0 is not None:
        details.update({"r0": r0.choice, "r0_confidence": r0.confidence})
    return {
        "verdict": None,
        "details": {
            **details,
            "r1": r1.choice,
            "r1_confidence": r1.confidence,
            "r2": r2.choice,
            "r2_confidence": r2.confidence,
        },
    }


def router_arm(adapter: StructuredJudgeAdapter, revision: str = "r1") -> Arm:
    def build(item: BankItem, variant: str = "clean") -> StructuredRequest:
        return route_request(item, variant, revision)

    return Arm(name="router", adapter=adapter, build=build, interpret=_route_interpret)


def _kept_evidence(item: BankItem) -> list:
    return [dict(e) for e in item.evidence if KIND_CLASS[e["kind"]] != "claim"]


def decomposed_request(item: BankItem, variant: str = "clean") -> StructuredRequest | None:
    """每個原子子句對整組非宣稱證據問一次關係；沒有證據時回 None（由程式判 insufficient）。"""
    evidence = _kept_evidence(item)
    if not evidence:
        return None
    clauses = list(item.clauses) or [item.criterion]
    questions = {
        f"c{j}": ChoiceQuestion(
            instructions=f"How does `evidence` relate to `claims[{j}]`?", criteria=dict(RELATION_CRITERIA)
        )
        for j in range(len(clauses))
    }
    return StructuredRequest(state={"claims": clauses, "evidence": evidence}, questions=questions)


def aggregate(relations: list) -> str:
    if any(r == "contradicts" for r in relations):
        return "not_satisfied"
    if relations and all(r == "supports" for r in relations):
        return "satisfied"
    return "insufficient"


def _decomposed_interpret(item: BankItem, response: StructuredResponse | None) -> dict:
    if response is None:
        return {"verdict": "insufficient", "details": {"relations": [], "decided_in_code": "no_non_claim_evidence"}}
    count = len(item.clauses) or 1
    answers = [response.answers[f"c{j}"] for j in range(count)]
    relations = [a.choice for a in answers if isinstance(a, ChoiceAnswer)]
    return {
        "verdict": aggregate(relations),
        "confidence": min((a.confidence for a in answers if a.confidence is not None), default=None),
        "details": {"relations": relations, "confidences": [a.confidence for a in answers]},
    }


def decomposed_arm(adapter: StructuredJudgeAdapter, name: str = "jdiag") -> Arm:
    return Arm(name=name, adapter=adapter, build=decomposed_request, interpret=_decomposed_interpret)


def p1b_arms(*, jev: StructuredJudgeAdapter | None = None, codex=None, claude=None, revision: str = "r1") -> dict:
    arms = {}
    if jev is not None:
        arms["router"] = router_arm(jev, revision)
        arms["jdiag"] = decomposed_arm(jev)
    if codex is not None:
        arms["codex"] = whole_pack_arm("codex", codex)
    if claude is not None:
        arms["claude"] = whole_pack_arm("claude", claude)
    return arms


def runs_for(item: BankItem) -> int:
    return RUN_COUNTS["variant"] if item.variant_of else RUN_COUNTS["base"]


def plan_p1b_tasks(bank: Bank, arm_names: list, *, split: str, item_ids: list | None = None) -> list:
    wanted = set(item_ids) if item_ids else None
    items = [i for i in bank.items if i.split == split and (wanted is None or i.id in wanted)]
    if wanted is not None and len(items) != len(wanted):
        raise ValueError(f"{split} 中找不到題目：{sorted(wanted - {i.id for i in items})}")
    return [Task(arm, item.id, "clean", run) for arm in arm_names for item in items for run in range(runs_for(item))]


def count_calls(bank: Bank, tasks: list, revision: str = "r1") -> dict:
    """依 task 清單估算各 provider 呼叫量，用於開跑前檢查上限。"""
    items = bank.by_id()
    calls = Counter()
    for task in tasks:
        item = items[task.item_id]
        if task.provider == "router":
            calls["jev_typed_judgments"] += len(ROUTER_REVISIONS[revision])
        elif task.provider == "jdiag":
            if _kept_evidence(item):
                calls["jev_typed_judgments"] += len(item.clauses) or 1
        else:
            calls[task.provider] += 1
    return dict(calls)


def check_p1b_composition(bank: Bank) -> list:
    """P1b 題庫組成（R23 A4）：全部題目要有 AC 類型、路由標註與原子子句；
    hidden 基礎題依（AC 類型 × 標註）各 4 題，另有 8 題誤導變體（兩種類型各 4）。"""
    problems = []
    for item in bank.items:
        if item.ac_type is None or item.route_label is None or not item.clauses:
            problems.append(f"{item.id}：缺 ac_type／route_label／clauses")
        unknown = sorted({e["kind"] for e in item.evidence} - set(KIND_CLASS))
        if unknown:
            problems.append(f"{item.id}：未知證據種類 {unknown}")
    hidden = [i for i in bank.items if i.split == "hidden"]
    base = [i for i in hidden if i.variant_of is None]
    variants = [i for i in hidden if i.variant_of is not None]
    cells = Counter((i.ac_type, i.gold) for i in base)
    for ac_type in AC_TYPES:
        for verdict in VERDICTS:
            if cells[(ac_type, verdict)] != 4:
                problems.append(f"hidden 基礎題 {ac_type}／{verdict} 有 {cells[(ac_type, verdict)]} 題，應為 4")
    by_type = Counter(i.ac_type for i in variants)
    for ac_type in AC_TYPES:
        if by_type[ac_type] != 4:
            problems.append(f"hidden 誤導變體 {ac_type} 有 {by_type[ac_type]} 題，應為 4")
    by_id = bank.by_id()
    for variant in variants:
        base_item = by_id.get(variant.variant_of)
        if base_item is None or base_item.variant_of is not None or base_item.ac_type != variant.ac_type:
            problems.append(f"{variant.id}：variant_of 必須指向同類型的 hidden 基礎題")
        if not variant.traps:
            problems.append(f"{variant.id}：誤導變體必須標 traps")
    return problems


def p1b_spec(*, codex_model: str, codex_effort: str, claude_model: str, revision: str = "r1") -> dict:
    spec = {
        "router_questions": {qid: q.to_payload() for qid, q in ROUTER_REVISIONS[revision].items()},
        "relation_criteria": dict(RELATION_CRITERIA),
        "kind_class": dict(KIND_CLASS),
        "aggregation_revision": AGGREGATION_REVISION,
        "code_judge_spec": bank_mod.judge_spec(),
        "providers": {"jev": "jev-1.13.0", "codex": f"{codex_model}@{codex_effort}", "claude": claude_model},
    }
    if revision != "r1":
        # r1 的規格不含此欄，已凍結的 r1 digest 因此不變。
        spec["revision"] = revision
    return spec


def _protocol(bank: Bank, spec: dict, thresholds: dict) -> dict:
    hidden = sorted(i.id for i in bank.items if i.split == "hidden")
    return {
        "schema": "jev-p1b-protocol/v1",
        "dataset_digest": bank.sha256(),
        "hidden_case_ids": hidden,
        "expected_label_digest": sha256_json({i.id: i.gold for i in bank.items}),
        "route_label_digest": sha256_json({i.id: i.route_label for i in bank.items}),
        "router_questions_revision": sha256_json(spec["router_questions"]),
        "code_judge_prompt_digest": sha256_json(spec["code_judge_spec"]),
        "non_code_aggregation_revision": sha256_json(
            {"relation": spec["relation_criteria"], "kind_class": spec["kind_class"], "agg": spec["aggregation_revision"]}
        ),
        "spec_digest": sha256_json(spec),
        "providers": spec["providers"],
        "run_counts": dict(RUN_COUNTS),
        "call_caps": dict(CALL_CAPS),
        "thresholds": dict(thresholds),
        "deadline": DEADLINE,
        **({"protocol_revision": spec["revision"]} if "revision" in spec else {}),
    }


def freeze_p1b(bank: Bank, spec: dict, thresholds: dict, *, now: _dt.datetime | None = None, force: bool = False) -> dict:
    problems = check_p1b_composition(bank)
    if problems:
        raise BankError("P1b 題庫組成不合格，拒絕凍結：" + "；".join(problems))
    path = bank.root / P1B_FROZEN_FILE
    if path.exists() and not force:
        raise BankError(f"{path} 已存在；重新凍結需明確 force（且依協定本輪作廢、另起 r2）")
    moment = now or _dt.datetime.now(_dt.timezone.utc)
    record = {"frozen_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), **_protocol(bank, spec, thresholds)}
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def verify_p1b_frozen(bank: Bank, spec: dict, thresholds: dict) -> dict:
    path = bank.root / P1B_FROZEN_FILE
    if not path.is_file():
        raise BankError("P1b 協定尚未凍結")
    record = json.loads(path.read_text(encoding="utf-8"))
    current = _protocol(bank, spec, thresholds)
    for key, value in current.items():
        if record.get(key) != value:
            raise BankError(f"P1b 協定欄位 {key} 與凍結紀錄不符")
    return record
