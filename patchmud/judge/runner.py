"""P1 benchmark runner：provider × 題目 × variant × run，逐次落盤 JSONL。

- 每個 provider 一條 lane（thread），lane 內循序呼叫；紀錄以 lock 保護逐行
  append 並 flush，中斷後可續跑：已有非 ``transport`` 錯誤紀錄的 task 跳過，
  技術失敗的 task 下次重跑。
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

from patchmud.judge.bank import Bank, build_request
from patchmud.judge.structured import (
    ChoiceAnswer,
    JudgeError,
    StructuredJudgeAdapter,
    sha256_json,
)
from patchmud.judge import bank as bank_mod

__all__ = ["RECORD_SCHEMA", "Task", "load_records", "plan_tasks", "run_benchmark"]

RECORD_SCHEMA = "jev-p1-record/v1"


@dataclass(frozen=True)
class Task:
    provider: str
    item_id: str
    variant: str
    run_index: int

    def key(self) -> tuple:
        return (self.provider, self.item_id, self.variant, self.run_index)


def plan_tasks(
    bank: Bank,
    providers: list,
    *,
    runs: int = 2,
    induced_runs: int = 1,
    item_ids: list | None = None,
) -> list:
    wanted = set(item_ids) if item_ids else None
    items = [item for item in bank.items if wanted is None or item.id in wanted]
    if wanted is not None:
        missing = sorted(wanted - {item.id for item in items})
        if missing:
            raise ValueError(f"題庫中找不到題目：{missing}")
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
    adapter: StructuredJudgeAdapter,
    task: Task,
    item,
    digests: dict,
    clock: Callable[[], float],
    now: Callable[[], str],
) -> dict:
    request = build_request(item, task.variant)
    record = {
        "schema": RECORD_SCHEMA,
        "provider": task.provider,
        "model": getattr(adapter, "model", None),
        "item_id": item.id,
        "split": item.split,
        "variant": task.variant,
        "run_index": task.run_index,
        **digests,
        "request_sha256": request.sha256(),
        "started_at": now(),
        "verdict": None,
        "confidence": None,
        "probabilities": None,
        "wall_ms": None,
        "api_ms": None,
        "attempts": None,
        "cost": None,
        "usage": None,
        "response_sha256": None,
        "error": None,
    }
    started = clock()
    try:
        response = adapter.judge(request)
    except JudgeError as exc:
        if exc.kind == "config":
            raise
        record["wall_ms"] = round((clock() - started) * 1000)
        record["error"] = {"kind": exc.kind, "status": exc.status, "message": str(exc)[:300]}
        return record
    answer = response.answers["verdict"]
    if not isinstance(answer, ChoiceAnswer):  # pragma: no cover - parse_answers 已保證
        raise TypeError("verdict answer 必須是 ChoiceAnswer")
    record.update(
        {
            "model": response.model,
            "verdict": answer.choice,
            "confidence": answer.confidence,
            "probabilities": answer.probabilities,
            "wall_ms": response.wall_ms,
            "api_ms": response.api_ms,
            "attempts": response.attempts,
            "cost": response.cost.to_dict(),
            "usage": response.usage,
            "response_sha256": response.response_sha256,
        }
    )
    return record


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
    """執行尚未完成的 task；回傳本次新增紀錄數與錯誤數。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    digests = {
        "bank_sha256": bank.sha256(),
        "judge_spec_sha256": sha256_json(bank_mod.judge_spec()),
        "frozen": frozen is not None,
    }
    done = {_record_key(r) for r in load_records(out_path) if _is_final(r)}
    items = bank.by_id()
    pending: dict = {}
    for task in plan_tasks(bank, list(adapters), runs=runs, induced_runs=induced_runs, item_ids=item_ids):
        if task.key() not in done:
            pending.setdefault(task.provider, []).append(task)
    lock = threading.Lock()
    stats = {"written": 0, "errors": 0}
    abort = threading.Event()

    def lane(provider: str) -> None:
        adapter = adapters[provider]
        for task in pending.get(provider, []):
            if abort.is_set():
                return
            try:
                record = _execute(adapter, task, items[task.item_id], digests, clock, now)
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

    providers = [p for p in adapters if pending.get(p)]
    if providers:
        with ThreadPoolExecutor(max_workers=len(providers)) as pool:
            futures = [pool.submit(lane, provider) for provider in providers]
            for future in futures:
                future.result()
    return stats
