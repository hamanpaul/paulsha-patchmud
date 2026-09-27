"""Judge benchmark runner：arm × 題目 × variant × run，逐次落盤 JSONL。

- arm 是「一個 adapter＋怎麼組 request＋怎麼把回應解讀成判決」。P1 的
  arm 都是整包三態判決（``whole_pack_arm``）；P1b 另有路由與拆子句的 arm
  （見 ``patchmud.judge.p1b``）。紀錄的 ``provider`` 欄位就是 arm 名稱。
- 每個 arm 一條 lane（thread），lane 內循序呼叫；紀錄以 lock 保護逐行
  append 並 flush，中斷後可續跑：已有非 ``transport`` 錯誤紀錄的 task 跳過，
  技術失敗的 task 下次重跑。
- arm 的 ``build`` 可以回傳 None，表示判決由程式直接決定、不呼叫 provider
  （例如宣稱類證據過濾後已無證據）；此時由 ``interpret(item, None)`` 給判決。
- 紀錄不含標註（gold 只在 evaluator 端比對），含 request／response hash、
  延遲、成本與其 basis、錯誤類型。
- ``config`` 類錯誤（例如缺 API key）直接中止整個 run，不把同一個設定
  問題寫成上百筆錯誤紀錄。
"""

from __future__ import annotations

import datetime as _dt
import json
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from patchmud.judge import bank as bank_mod
from patchmud.judge.bank import Bank, BankItem, build_request
from patchmud.judge.structured import (
    ChoiceAnswer,
    JudgeError,
    StructuredJudgeAdapter,
    StructuredRequest,
    StructuredResponse,
    sha256_json,
)

__all__ = [
    "RECORD_SCHEMA",
    "Arm",
    "Task",
    "load_records",
    "plan_tasks",
    "run_benchmark",
    "run_tasks",
    "whole_pack_arm",
]

RECORD_SCHEMA = "jev-p1-record/v1"


@dataclass(frozen=True)
class Arm:
    """一個受測組合：adapter、request 組裝與判決解讀。"""

    name: str
    adapter: StructuredJudgeAdapter
    build: Callable[[BankItem, str], StructuredRequest | None]
    interpret: Callable[[BankItem, StructuredResponse | None], dict]


def _interpret_verdict(item: BankItem, response: StructuredResponse | None) -> dict:
    answer = response.answers["verdict"]
    if not isinstance(answer, ChoiceAnswer):  # pragma: no cover - parse_answers 已保證
        raise TypeError("verdict answer 必須是 ChoiceAnswer")
    return {"verdict": answer.choice, "confidence": answer.confidence, "probabilities": answer.probabilities}


def whole_pack_arm(name: str, adapter: StructuredJudgeAdapter) -> Arm:
    """P1 的整包三態判決：一題一個 Choice，state 為 criterion＋全部證據。"""
    return Arm(name=name, adapter=adapter, build=build_request, interpret=_interpret_verdict)


@dataclass(frozen=True)
class Task:
    provider: str
    item_id: str
    variant: str
    run_index: int

    def key(self) -> tuple:
        return (self.provider, self.item_id, self.variant, self.run_index)


def _select_items(bank: Bank, item_ids: list | None) -> list:
    wanted = set(item_ids) if item_ids else None
    items = [item for item in bank.items if wanted is None or item.id in wanted]
    if wanted is not None:
        missing = sorted(wanted - {item.id for item in items})
        if missing:
            raise ValueError(f"題庫中找不到題目：{missing}")
    return items


def plan_tasks(
    bank: Bank,
    providers: list,
    *,
    runs: int = 2,
    induced_runs: int = 1,
    item_ids: list | None = None,
) -> list:
    """P1 的規劃：每個 provider 對每題跑 ``runs`` 次 clean、``induced_runs`` 次誘導。"""
    items = _select_items(bank, item_ids)
    tasks = []
    for provider in providers:
        for run_index in range(runs):
            tasks += [Task(provider, item.id, "clean", run_index) for item in items]
        for run_index in range(induced_runs):
            tasks += [Task(provider, item.id, "induced", run_index) for item in items]
    return tasks


def load_records(path: Path) -> list:
    path = Path(path)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _record_key(record: dict) -> tuple:
    return (record["provider"], record["item_id"], record["variant"], record["run_index"])


def _is_final(record: dict) -> bool:
    error = record.get("error")
    return error is None or error.get("kind") != "transport"


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _execute(
    arm: Arm,
    task: Task,
    item: BankItem,
    digests: dict,
    clock: Callable[[], float],
    now: Callable[[], str],
) -> dict:
    request = arm.build(item, task.variant)
    record = {
        "schema": RECORD_SCHEMA,
        "provider": task.provider,
        "model": getattr(arm.adapter, "model", None),
        "item_id": item.id,
        "split": item.split,
        "variant": task.variant,
        "run_index": task.run_index,
        **digests,
        "request_sha256": None if request is None else request.sha256(),
        "started_at": now(),
        "verdict": None,
        "confidence": None,
        "probabilities": None,
        "details": None,
        "wall_ms": None,
        "api_ms": None,
        "attempts": None,
        "cost": None,
        "usage": None,
        "response_sha256": None,
        "error": None,
    }
    if request is None:
        # 判決由程式直接決定（例如宣稱類證據過濾後已無證據），不呼叫 provider。
        record.update({"wall_ms": 0, "attempts": 0, "cost": {"usd": "0", "basis": "decided_in_code", "units": {}}})
        record.update(arm.interpret(item, None))
        return record
    started = clock()
    try:
        response = arm.adapter.judge(request)
    except JudgeError as exc:
        if exc.kind == "config":
            raise
        record["wall_ms"] = round((clock() - started) * 1000)
        record["error"] = {"kind": exc.kind, "status": exc.status, "message": str(exc)[:300]}
        return record
    record.update(
        {
            "model": response.model,
            "wall_ms": response.wall_ms,
            "api_ms": response.api_ms,
            "attempts": response.attempts,
            "cost": response.cost.to_dict(),
            "usage": response.usage,
            "response_sha256": response.response_sha256,
        }
    )
    record.update(arm.interpret(item, response))
    return record


def run_tasks(
    bank: Bank,
    arms: dict,
    tasks: list,
    out_path: Path,
    *,
    frozen: dict | None = None,
    spec_digest: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], str] = _utc_now,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """執行尚未完成的 task；回傳本次新增紀錄數與錯誤數。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    digests = {
        "bank_sha256": bank.sha256(),
        "judge_spec_sha256": spec_digest or sha256_json(bank_mod.judge_spec()),
        "frozen": frozen is not None,
    }
    done = {_record_key(r) for r in load_records(out_path) if _is_final(r)}
    items = bank.by_id()
    pending: dict = {}
    for task in tasks:
        if task.provider not in arms:
            raise ValueError(f"task 指向未定義的 arm：{task.provider}")
        if task.key() not in done:
            pending.setdefault(task.provider, []).append(task)
    lock = threading.Lock()
    stats = {"written": 0, "errors": 0}
    abort = threading.Event()

    def lane(name: str) -> None:
        arm = arms[name]
        for task in pending.get(name, []):
            if abort.is_set():
                return
            try:
                record = _execute(arm, task, items[task.item_id], digests, clock, now)
            except JudgeError:
                abort.set()
                raise
            with lock:
                with out_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                stats["written"] += 1
                if record["error"] is not None:
                    stats["errors"] += 1
                if progress is not None:
                    progress(record)

    names = [name for name in arms if pending.get(name)]
    if names:
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = [pool.submit(lane, name) for name in names]
            for future in futures:
                future.result()
    return stats


def run_benchmark(
    bank: Bank,
    adapters: dict,
    out_path: Path,
    *,
    runs: int = 2,
    induced_runs: int = 1,
    item_ids: list | None = None,
    frozen: dict | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], str] = _utc_now,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """P1：每個 provider 以整包三態判決跑 clean 與誘導變體。"""
    arms = {name: whole_pack_arm(name, adapter) for name, adapter in adapters.items()}
    tasks = plan_tasks(bank, list(adapters), runs=runs, induced_runs=induced_runs, item_ids=item_ids)
    return run_tasks(bank, arms, tasks, out_path, frozen=frozen, clock=clock, now=now, progress=progress)
