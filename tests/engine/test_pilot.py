"""Task 18 RED：pilot runner——sealed schedule、registry 冪等續跑、preflight
gates（spec §10.4–10.5、§11；plan Task 18 Step 1）。

鎖定契約：
- 同 seed 兩次 build → 相同排列與 schedule hash（F21 可重現）。
- 不同 seed → 不同排列，且 encounter／loadout／model 三軸皆被打散
  （統計性檢查：任一軸值的 runs 不連續成塊）。
- schedule 檔不存在或 hash 不符（遭竄改）→ runner 拒跑、execute 零呼叫。
- run registry JSONL 冪等續跑：中斷後重啟跳過已完成 run、只補殘餘；
  重跑已完成 run_id 必須顯式 `force`（attempt+1，舊 registry 行保留）。
- 三個 preflight fail-closed gates 各一測試：estimators.yaml 未 commit（F4）、
  sandbox namespace 能力不足（§7）、地端模型缺 cost_scenarios（F18）。

unit tests 全 fake execute，不啟真 namespace、不打真模型 API（plan invariant 3）。
"""

from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from patchmud.engine.pilot import (
    GateError,
    ModelEntry,
    PilotError,
    PilotRunner,
    check_gates,
    load_models,
)
from patchmud.engine.schedule import (
    RunMatrix,
    ScheduleError,
    ScheduleItem,
    build_schedule,
    load_schedule,
    save_schedule,
)
from patchmud.sandbox.isolate import Capabilities

CAPS_OK = Capabilities(mount_ns=True, net_ns=True, pid_ns=True)
CAPS_DEGRADED = Capabilities(mount_ns=True, net_ns=False, pid_ns=True)

ENCOUNTERS = tuple(f"enc-{i}" for i in range(1, 9))
LOADOUTS = tuple(f"P{p}T{t}R{r}" for p in (0, 1) for t in (0, 1) for r in (0, 1))
MODELS = ("model-fixer", "model-flooder")
MATRIX = RunMatrix(encounters=ENCOUNTERS, loadouts=LOADOUTS, models=MODELS)

#: 小矩陣（2×2×1 = 4 runs）：registry 續跑／force 語意測試用。
SMALL = RunMatrix(
    encounters=("enc-1", "enc-2"),
    loadouts=("P0T0R0", "P1T0R0"),
    models=("m1",),
)

REMOTE_MODEL = ModelEntry(id="m1", adapter="scripted:replies.txt")


class Interrupted(Exception):
    """fake execute 的中斷注入（模擬 kill 中途）。"""


class FakeExecute:
    """記錄 (run_id, attempt) 呼叫序；可在第 N 次呼叫時注入中斷。"""

    def __init__(self, interrupt_after: int | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self._interrupt_after = interrupt_after

    def __call__(self, item: ScheduleItem, attempt: int) -> dict:
        if self._interrupt_after is not None and len(self.calls) == self._interrupt_after:
            raise Interrupted(f"中斷於第 {len(self.calls) + 1} 個 run")
        self.calls.append((item.run_id, attempt))
        return {"run_dir": f"runs/{item.run_id}"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def git_repo(root: Path, *, estimators: str = "committed") -> Path:
    """建 git repo；estimators ∈ {committed, untracked, absent}（F4 gate 素材）。"""
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "README.md").write_text("pilot fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(
        root,
        "-c", "user.name=t", "-c", "user.email=t@example.com",
        "commit", "-qm", "init",
    )
    if estimators in ("committed", "untracked"):
        reg = root / "analysis" / "registered"
        reg.mkdir(parents=True)
        (reg / "estimators.yaml").write_text("estimators: pinned\n", encoding="utf-8")
        if estimators == "committed":
            _git(root, "add", "analysis/registered/estimators.yaml")
            _git(
                root,
                "-c", "user.name=t", "-c", "user.email=t@example.com",
                "commit", "-qm", "register estimators",
            )
    return root


def make_runner(
    tmp_path: Path,
    repo: Path,
    execute,
    *,
    caps: Capabilities = CAPS_OK,
    models: tuple[ModelEntry, ...] = (REMOTE_MODEL,),
) -> PilotRunner:
    return PilotRunner(
        registry_path=tmp_path / "registry.jsonl",
        execute=execute,
        models=models,
        capabilities=lambda: caps,
        repo_root=repo,
    )


def sealed_small_schedule(tmp_path: Path) -> tuple[Path, list[str]]:
    schedule = build_schedule(SMALL, seed=3)
    path = tmp_path / "schedule.yaml"
    save_schedule(schedule, path)
    return path, [item.run_id for item in schedule.items]


# ---------------------------------------------------------------------------
# schedule：全域單一隨機排列＋sealed 序列化（F21）
# ---------------------------------------------------------------------------


def test_same_seed_reproduces_same_schedule_hash() -> None:
    a = build_schedule(MATRIX, seed=7)
    b = build_schedule(MATRIX, seed=7)
    assert a.items == b.items
    assert a.sha256 == b.sha256


def test_different_seed_shuffles_all_three_axes() -> None:
    a = build_schedule(MATRIX, seed=7)
    b = build_schedule(MATRIX, seed=8)
    # 同一矩陣（同一 run 集合）、不同排列
    assert sorted(item.run_id for item in a.items) == sorted(
        item.run_id for item in b.items
    )
    assert a.items != b.items
    assert a.sha256 != b.sha256
    # 三軸皆被打散：任一軸值的 runs 不連續成塊（統計性檢查）
    for schedule in (a, b):
        for axis in ("encounter", "loadout", "model"):
            for value in {getattr(item, axis) for item in schedule.items}:
                pos = [
                    i
                    for i, item in enumerate(schedule.items)
                    if getattr(item, axis) == value
                ]
                assert len(pos) >= 2
                span = max(pos) - min(pos) + 1
                assert span > len(pos), f"{axis}={value} 的 runs 連續成塊"


def test_schedule_seal_roundtrip_and_refuses_overwrite(tmp_path: Path) -> None:
    schedule = build_schedule(SMALL, seed=3)
    path = tmp_path / "schedule.yaml"
    save_schedule(schedule, path)
    loaded = load_schedule(path)
    assert loaded == schedule
    assert loaded.sha256 == schedule.sha256
    # sealed：已封存即不可覆寫
    with pytest.raises(ScheduleError):
        save_schedule(schedule, path)


def test_matrix_validation_fail_closed() -> None:
    with pytest.raises(ScheduleError):
        RunMatrix(encounters=(), loadouts=("P0T0R0",), models=("m1",))
    with pytest.raises(ScheduleError):
        RunMatrix(encounters=("e1",), loadouts=("BAD",), models=("m1",))
    with pytest.raises(ScheduleError):
        RunMatrix(encounters=("e1", "e1"), loadouts=("P0T0R0",), models=("m1",))


# ---------------------------------------------------------------------------
# runner：schedule 檔 fail-closed（不存在／hash 不符即拒跑）
# ---------------------------------------------------------------------------


def test_runner_refuses_missing_schedule_file(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    execute = FakeExecute()
    runner = make_runner(tmp_path, repo, execute)
    with pytest.raises(ScheduleError):
        runner.run(tmp_path / "schedule.yaml")
    assert execute.calls == []
    assert not (tmp_path / "registry.jsonl").exists()


def test_runner_refuses_tampered_schedule(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    path, _ = sealed_small_schedule(tmp_path)
    # 竄改：交換前兩個排程項（hash 不符）
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["items"][0], doc["items"][1] = doc["items"][1], doc["items"][0]
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    execute = FakeExecute()
    runner = make_runner(tmp_path, repo, execute)
    with pytest.raises(ScheduleError):
        runner.run(path)
    assert execute.calls == []


# ---------------------------------------------------------------------------
# registry：冪等續跑與 force 語意
# ---------------------------------------------------------------------------


def test_interrupted_run_resumes_skipping_completed(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    path, run_ids = sealed_small_schedule(tmp_path)

    first = FakeExecute(interrupt_after=2)
    with pytest.raises(Interrupted):
        make_runner(tmp_path, repo, first).run(path)
    assert [rid for rid, _ in first.calls] == run_ids[:2]

    resume = FakeExecute()
    report = make_runner(tmp_path, repo, resume).run(path)
    assert [rid for rid, _ in resume.calls] == run_ids[2:]
    assert report.executed == tuple(run_ids[2:])
    assert report.skipped == tuple(run_ids[:2])

    # 全部完成後再跑一次 → 零執行、全 skipped
    again = FakeExecute()
    report2 = make_runner(tmp_path, repo, again).run(path)
    assert again.calls == []
    assert report2.executed == ()
    assert sorted(report2.skipped) == sorted(run_ids)


def test_force_reruns_completed_run_with_new_attempt(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    path, run_ids = sealed_small_schedule(tmp_path)
    make_runner(tmp_path, repo, FakeExecute()).run(path)

    target = run_ids[1]
    forced = FakeExecute()
    report = make_runner(tmp_path, repo, forced).run(path, force=(target,))
    assert forced.calls == [(target, 2)]
    assert report.executed == (target,)

    # 舊 registry 行保留：target 的 attempt 1 與 2 並存
    lines = (tmp_path / "registry.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(run_ids) + 1
    import json

    attempts = [
        json.loads(line)["attempt"]
        for line in lines
        if json.loads(line)["run_id"] == target
    ]
    assert sorted(attempts) == [1, 2]

    # force 的 run_id 不在 schedule 內 → 拒絕
    with pytest.raises(PilotError):
        make_runner(tmp_path, repo, FakeExecute()).run(path, force=("no-such-run",))


def test_registry_of_other_schedule_refused(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    path, _ = sealed_small_schedule(tmp_path)
    make_runner(tmp_path, repo, FakeExecute()).run(path)

    other = build_schedule(SMALL, seed=99)
    other_path = tmp_path / "schedule-other.yaml"
    save_schedule(other, other_path)
    execute = FakeExecute()
    with pytest.raises(PilotError):
        make_runner(tmp_path, repo, execute).run(other_path)
    assert execute.calls == []


# ---------------------------------------------------------------------------
# preflight gates（任一不滿足即拒絕啟動；execute 零呼叫）
# ---------------------------------------------------------------------------


def test_gate_estimators_must_be_committed(tmp_path: Path) -> None:
    path, _ = sealed_small_schedule(tmp_path)
    # 檔案不存在 → 拒絕
    absent = git_repo(tmp_path / "absent", estimators="absent")
    execute = FakeExecute()
    with pytest.raises(GateError):
        make_runner(tmp_path, absent, execute).run(path)
    assert execute.calls == []
    # 檔案存在但未 commit → 拒絕（F4：以 commit 為準）
    untracked = git_repo(tmp_path / "untracked", estimators="untracked")
    with pytest.raises(GateError):
        make_runner(tmp_path, untracked, execute).run(path)
    assert execute.calls == []


def test_gate_sandbox_capabilities_must_be_complete(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    path, _ = sealed_small_schedule(tmp_path)
    execute = FakeExecute()
    runner = make_runner(tmp_path, repo, execute, caps=CAPS_DEGRADED)
    with pytest.raises(GateError):
        runner.run(path)
    assert execute.calls == []


def test_gate_local_model_requires_cost_scenarios(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "repo")
    path, _ = sealed_small_schedule(tmp_path)
    local_missing = ModelEntry(id="local-a", adapter="openai:q@http://h", local=True)
    execute = FakeExecute()
    runner = make_runner(tmp_path, repo, execute, models=(REMOTE_MODEL, local_missing))
    with pytest.raises(GateError):
        runner.run(path)
    assert execute.calls == []

    # 有註冊三情境的地端模型 → gate 通過
    registered = ModelEntry(
        id="local-b",
        adapter="openai:q@http://h",
        local=True,
        cost_scenarios={
            "low": Decimal("20"),
            "mid": Decimal("40"),
            "high": Decimal("80"),
        },
    )
    check_gates(
        repo_root=repo, capabilities=CAPS_OK, models=(REMOTE_MODEL, registered)
    )


# ---------------------------------------------------------------------------
# CLI：`patchmud pilot` 操作性錯誤 → exit 2（真矩陣 e2e 見 Task 21）
# ---------------------------------------------------------------------------


def test_cli_pilot_empty_deck_exits_2(tmp_path: Path, capsys) -> None:
    from patchmud.cli import main

    deck = tmp_path / "deck"
    deck.mkdir()
    models = tmp_path / "models.yaml"
    models.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "models": [{"id": "m1", "adapter": "scripted:x"}]}
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "pilot",
            "--deck", str(deck),
            "--models", str(models),
            "--seed", "1",
            "--runs-root", str(tmp_path / "runs"),
        ]
    )
    assert code == 2
    assert "pilot 失敗" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# models.yaml：schema fail-closed 與 Decimal 金額
# ---------------------------------------------------------------------------


def test_load_models_parses_cost_scenarios_as_decimal(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "models": [
                    {"id": "remote-a", "adapter": "anthropic:claude-x"},
                    {
                        "id": "local-a",
                        "adapter": "openai:qwen@http://localhost:8000/v1",
                        "local": True,
                        "cost_scenarios": {
                            "low": "20.0",
                            "mid": "40.5",
                            "high": "81",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    remote, local = load_models(path)
    assert remote == ModelEntry(id="remote-a", adapter="anthropic:claude-x")
    assert local.local is True
    assert local.cost_scenarios == {
        "low": Decimal("20.0"),
        "mid": Decimal("40.5"),
        "high": Decimal("81"),
    }


def test_load_models_fail_closed(tmp_path: Path) -> None:
    def dump(models: list) -> Path:
        path = tmp_path / "models.yaml"
        path.write_text(
            yaml.safe_dump({"schema_version": 1, "models": models}), encoding="utf-8"
        )
        return path

    with pytest.raises(PilotError):  # 檔案不存在
        load_models(tmp_path / "no-such.yaml")
    with pytest.raises(PilotError):  # 重複 id
        load_models(
            dump(
                [
                    {"id": "a", "adapter": "scripted:x"},
                    {"id": "a", "adapter": "scripted:y"},
                ]
            )
        )
    with pytest.raises(PilotError):  # 缺 adapter
        load_models(dump([{"id": "a"}]))
    with pytest.raises(PilotError):  # cost_scenarios 缺情境鍵
        load_models(
            dump(
                [
                    {
                        "id": "a",
                        "adapter": "scripted:x",
                        "local": True,
                        "cost_scenarios": {"low": "1", "mid": "2"},
                    }
                ]
            )
        )
    with pytest.raises(PilotError):  # 金額不得用 float（Decimal 契約）
        load_models(
            dump(
                [
                    {
                        "id": "a",
                        "adapter": "scripted:x",
                        "local": True,
                        "cost_scenarios": {"low": 1.5, "mid": "2", "high": "3"},
                    }
                ]
            )
        )
