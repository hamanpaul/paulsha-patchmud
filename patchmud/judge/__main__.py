"""``python -m patchmud.judge``：題庫檢核、凍結、執行與計分。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from patchmud.judge import bank as bank_mod
from patchmud.judge.llm_cli import ClaudeCliJudge, CopilotCliJudge, copilot_user_mcp_servers
from patchmud.judge.runner import load_records, run_benchmark
from patchmud.judge.scoring import decide, render_report, summarize_provider
from patchmud.judge.structured import JudgeError, sha256_json
from patchmud.judge.typesafe import TypeSafeJudgeAdapter

PROVIDERS = ("jev", "claude", "copilot")


def _load(path: str) -> bank_mod.Bank:
    return bank_mod.load_bank(Path(path))


def _cmd_validate(args) -> int:
    bank = _load(args.bank)
    problems = bank_mod.check_composition(bank)
    for problem in problems:
        print(f"組成問題：{problem}")
    try:
        record = bank_mod.verify_frozen(bank)
        print(f"凍結一致（{record['frozen_at']}）")
    except bank_mod.BankError as exc:
        print(f"凍結狀態：{exc}")
    print(f"題數 {len(bank.items)}；bank_sha256 {bank.sha256()}")
    return 1 if problems else 0


def _cmd_freeze(args) -> int:
    record = bank_mod.freeze(_load(args.bank), force=args.force)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def _build_adapters(args) -> dict:
    adapters = {}
    for name in args.providers.split(","):
        name = name.strip()
        if name == "jev":
            adapters[name] = TypeSafeJudgeAdapter()
        elif name == "claude":
            adapters[name] = ClaudeCliJudge(model=args.claude_model)
        elif name == "copilot":
            servers = copilot_user_mcp_servers(Path(args.copilot_mcp_config).expanduser())
            adapters[name] = CopilotCliJudge(model=args.copilot_model, disabled_mcp_servers=tuple(servers))
        else:
            raise SystemExit(f"未知 provider：{name}（可用：{', '.join(PROVIDERS)}）")
    return adapters


def _cmd_run(args) -> int:
    bank = _load(args.bank)
    frozen = None
    if args.allow_unfrozen:
        print("警告：未驗證凍結狀態，本次結果只能當管線 smoke，不計入 P1。", file=sys.stderr)
    else:
        frozen = bank_mod.verify_frozen(bank)
    adapters = _build_adapters(args)
    items = [i.strip() for i in args.items.split(",")] if args.items else None

    def progress(record: dict) -> None:
        verdict = record["verdict"] or f"ERROR:{record['error']['kind']}"
        print(
            f"{record['provider']:8} {record['variant']:7} r{record['run_index']} "
            f"{record['item_id']:40} {verdict:14} {record['wall_ms']} ms",
            flush=True,
        )

    try:
        stats = run_benchmark(
            bank,
            adapters,
            Path(args.out),
            runs=args.runs,
            induced_runs=args.induced_runs,
            item_ids=items,
            frozen=frozen,
            progress=progress,
        )
    except JudgeError as exc:
        print(f"中止（{exc.kind}）：{exc}", file=sys.stderr)
        return 2
    print(f"新增紀錄 {stats['written']} 筆，其中錯誤 {stats['errors']} 筆")
    return 0


def _cmd_score(args) -> int:
    bank = _load(args.bank)
    records_path = Path(args.records)
    records = load_records(records_path)
    providers = [p for p in PROVIDERS if any(r["provider"] == p for r in records)]
    summaries = {
        p: summarize_provider(bank, records, p, runs=args.runs, induced_runs=args.induced_runs)
        for p in providers
    }
    decision = decide(summaries)
    try:
        frozen = bank_mod.verify_frozen(bank)
    except bank_mod.BankError:
        frozen = {}
    bank_digests = {r.get("bank_sha256") for r in records}
    if bank_digests - {bank.sha256()}:
        decision["decision"] = "no-go"
        decision["reasons"].append("紀錄的題庫 digest 與目前題庫不符")
    meta = {
        "run_id": records_path.parent.name,
        "bank_sha256": bank.sha256(),
        "judge_spec_sha256": sha256_json(bank_mod.judge_spec()),
        "frozen_at": frozen.get("frozen_at", "未凍結"),
    }
    summary = {"meta": meta, "decision": decision, "providers": summaries}
    out_dir = records_path.parent
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_report(summaries, decision, meta), encoding="utf-8")
    print(f"判定：{decision['decision']}")
    for reason in decision["reasons"]:
        print(f"  - {reason}")
    print(f"已寫入 {out_dir / 'summary.json'} 與 {out_dir / 'report.md'}")
    return 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m patchmud.judge", description="JEV P1 judge benchmark")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="檢核題庫組成與凍結狀態")
    p.add_argument("--bank", required=True)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("freeze", help="凍結題庫與判決規格")
    p.add_argument("--bank", required=True)
    p.add_argument("--force", action="store_true", help="覆寫既有 FROZEN.json")
    p.set_defaults(func=_cmd_freeze)

    p = sub.add_parser("run", help="執行 benchmark（可續跑）")
    p.add_argument("--bank", required=True)
    p.add_argument("--out", required=True, help="records.jsonl 路徑")
    p.add_argument("--providers", default=",".join(PROVIDERS))
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--induced-runs", type=int, default=1)
    p.add_argument("--items", default="", help="逗號分隔的題目 id（smoke 用）")
    p.add_argument("--allow-unfrozen", action="store_true")
    p.add_argument("--claude-model", default="sonnet")
    p.add_argument("--copilot-model", default="gpt-5.4")
    p.add_argument("--copilot-mcp-config", default="~/.copilot/mcp-config.json")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("score", help="計分並判定 go／no-go")
    p.add_argument("--bank", required=True)
    p.add_argument("--records", required=True)
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--induced-runs", type=int, default=1)
    p.set_defaults(func=_cmd_score)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
