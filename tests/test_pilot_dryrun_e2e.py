"""Task 21 RED→PASS：milestone D 收口——矩陣 dry-run 驗收（spec §11、§13）。

`pilot-v1`（8 encounters）× 8 loadouts × 2 個 `ScriptedAdapter` 假模型
（``fixer`` 會依 loadout 動態組出「先 PLAN／WRITE_TEST（依 P／T 需要）→
套用 reference.patch → SUMMON REVIEWER（依 R 需要）→ COMMIT」的合法回覆序列
把 issue 修好；``flooder`` 只留一次落空的 WRITE_TEST 與一次必被拒的 PATCH，
其餘 LOOK 填滿到 ``max_turns``，永不 COMMIT）跑完 128 runs：

- schedule 先於首個 run 落盤且 hash 與 `load_schedule` 重算相符（F21）。
- 全部 128 個 run 有 `result.yaml`。
- `patchmud report` 可對整批 run 產出多榜。
- 抽 3 個 run `replay_l1` 位元一致（identical=True）。
- 中斷（模擬 kill：第 N 次 execute 呼叫前直接拋例外）後以新 `PilotRunner`
  續跑，registry 冪等——總完成 run 數仍是 128、無重複、無遺漏。

真 bwrap e2e（環境無能力時 skip，比照 test_run_e2e）；`PilotRunner` 走真
`_wire_and_run_encounter`（`patchmud pilot` 同一佈線），僅 ``execute`` 由本測試
依 (encounter, loadout, model) 動態組 `ScriptedAdapter`——`models.yaml` 靜態
單檔劇本無法涵蓋 loadout 相依的合法動作序列（Task 18 note：「真矩陣 e2e 見
Task 21」）。preflight gate 的 estimators repo 是獨立 scratch git repo
（比照 `tests/engine/test_pilot.py` 的 `git_repo()`），不觸碰本 repo 的
git 狀態。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from patchmud.adapters.scripted import ScriptedAdapter
from patchmud.cli import _wire_and_run_encounter, build_report
from patchmud.deck.loader import load_card
from patchmud.deck.model import IssueCard
from patchmud.engine.pilot import ModelEntry, PilotRunner
from patchmud.engine.schedule import (
    RunMatrix,
    ScheduleItem,
    build_schedule,
    load_schedule,
    save_schedule,
)
from patchmud.engine.strategy import Loadout
from patchmud.sandbox.isolate import DEFAULT_BWRAP_PATH, IsolationRunner
from patchmud.store.replay import replay_l1

REPO_ROOT = Path(__file__).resolve().parents[1]
DECK_DIR = REPO_ROOT / "decks" / "pilot-v1"

LOADOUTS = tuple(f"P{p}T{t}R{r}" for p in (0, 1) for t in (0, 1) for r in (0, 1))
MODELS = ("fixer", "flooder")

_FLOOD_TEST_PATH = "tests/agent/test_flood_probe.py"
_FLOOD_TEST_DIFF = (
    "diff --git a/tests/agent/test_flood_probe.py b/tests/agent/test_flood_probe.py\n"
    "new file mode 100644\n"
    "index 0000000..e69de29\n"
    "--- /dev/null\n"
    "+++ b/tests/agent/test_flood_probe.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+def test_flood_probe():\n"
    '+    assert False, "red"\n'
)
#: 目標路徑不存在＋context 保證不符 → `git apply --check` 必拒（error 而非
#: illegal），與具體 encounter 內容無關，跨全部 8 個 encounters 通用。
_BAD_PATCH_DIFF = (
    "--- a/nonexistent/file.py\n"
    "+++ b/nonexistent/file.py\n"
    "@@ -1,3 +1,4 @@\n"
    " this context line will never match\n"
    "+flood filler line that fixes nothing\n"
    " another impossible context line\n"
    " yet another line that does not exist\n"
)

_SUMMON_REPLY = "ACTION: SUMMON REVIEWER"
_REVIEW_RESPONSE = "findings: []\n"
_COMMIT_REPLY = "ACTION: COMMIT"
_LOOK_REPLY = "ACTION: LOOK"
_BAD_PATCH_REPLY = (
    "ACTION: PATCH\nTARGET_ISSUES: MAIN-1\nPATCH:\n" + _BAD_PATCH_DIFF
)
_WRITE_TEST_REPLY = "ACTION: WRITE_TEST\nPATCH:\n" + _FLOOD_TEST_DIFF


# ---------------------------------------------------------------------------
# 假模型：依 (card, loadout) 動態組出合法回覆序列
# ---------------------------------------------------------------------------


def _plan_reply(card: IssueCard) -> str:
    payload = yaml.safe_dump(
        {
            "requirements": [req.id for req in card.public_requirements],
            "invariants": ["不得破壞既有相容性"],
            "files_to_inspect": list(card.expected_paths),
            "risks": ["修改範圍可能超出預期"],
            "test_targets": [_FLOOD_TEST_PATH],
        },
        allow_unicode=True,
        sort_keys=False,
    )
    return "ACTION: PLAY PLAN\nPLAN:\n" + payload


def _patch_reply(reference_diff: str) -> str:
    return (
        "ACTION: PATCH\nTARGET_ISSUES: MAIN-1\nCLAIM: dry-run fixer patch\n"
        "PATCH:\n" + reference_diff
    )


def build_fixer_script(
    card: IssueCard, reference_diff: str, loadout: Loadout
) -> list[str]:
    """「會修」劇本：依 loadout 動態插入 PLAN／WRITE_TEST／SUMMON，恆合法。"""
    replies: list[str] = []
    if loadout.plan:
        replies.append(_plan_reply(card))
    if loadout.tdd:
        replies.append(_WRITE_TEST_REPLY)
    replies.append(_patch_reply(reference_diff))
    if loadout.reviewer:
        replies.append(_SUMMON_REPLY)
        replies.append(_REVIEW_RESPONSE)  # reviewer subcall 消耗、非新 turn（F9）
    replies.append(_COMMIT_REPLY)
    return replies


def build_flooder_script(card: IssueCard) -> list[str]:
    """「會 flood」劇本：一次落空的 WRITE_TEST＋一次必拒的 PATCH，其餘 LOOK
    填滿到 `max_turns`（永不 COMMIT，全部 loadout 下 turn 數固定 = max_turns，
    與具體 encounter／loadout 無關的通用劇本）。"""
    replies = [_WRITE_TEST_REPLY, _BAD_PATCH_REPLY]
    replies.extend(_LOOK_REPLY for _ in range(card.max_turns - len(replies)))
    return replies[: card.max_turns]


# ---------------------------------------------------------------------------
# preflight gate 素材：獨立 scratch git repo（不動本 repo 的 git 狀態）
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _estimators_repo(tmp_path: Path) -> Path:
    root = tmp_path / "estimators-repo"
    root.mkdir()
    _git(root, "init", "-q")
    registered = root / "analysis" / "registered"
    registered.mkdir(parents=True)
    (registered / "estimators.yaml").write_text(
        "estimators: pilot-dryrun-fixture\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(
        root,
        "-c", "user.name=t", "-c", "user.email=t@example.com",
        "commit", "-qm", "register estimators",
    )
    return root


def _encounter_names() -> tuple[str, ...]:
    return tuple(
        sorted(p.name for p in DECK_DIR.iterdir() if (p / "card.yaml").is_file())
    )


class Interrupted(Exception):
    """模擬 kill：中斷注入，發生於 execute 對第 N 個 run 動手前。"""


@pytest.fixture()
def real_capabilities():
    probe = IsolationRunner(Path("/tmp"), bwrap_path=DEFAULT_BWRAP_PATH)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        pytest.skip("namespace 能力不足（degraded）")


def _final_run_dirs(registry_path: Path) -> dict[str, str]:
    """registry.jsonl 的 done 行 → run_id → 落盤 run_dir（無 --force 時恰一筆）。"""
    mapping: dict[str, str] = {}
    for line in registry_path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["status"] == "done":
            mapping[record["run_id"]] = record["run_dir"]
    return mapping


class TestPilotMatrixDryRun:
    def test_full_matrix_dry_run(self, real_capabilities, tmp_path: Path) -> None:
        encounters = _encounter_names()
        assert len(encounters) == 8, "pilot-v1 必須恰有 8 個 encounter（Task 20）"
        matrix = RunMatrix(
            encounters=encounters, loadouts=LOADOUTS, models=MODELS
        )
        assert len(matrix.encounters) * len(matrix.loadouts) * len(matrix.models) == 128

        # ---- schedule 先於首個 run 落盤（F21） ----------------------------
        runs_root = tmp_path / "runs"
        schedule_path = runs_root / "schedule.yaml"
        built = build_schedule(matrix, seed=20260716)
        save_schedule(built, schedule_path)
        sealed = load_schedule(schedule_path)
        assert sealed.sha256 == built.sha256
        assert len(sealed.items) == 128

        estimators_repo = _estimators_repo(tmp_path)
        models = (
            ModelEntry(id="fixer", adapter="scripted:dynamic"),
            ModelEntry(id="flooder", adapter="scripted:dynamic"),
        )

        card_cache: dict[str, IssueCard] = {}
        reference_cache: dict[str, str] = {}

        def _card(encounter: str) -> IssueCard:
            if encounter not in card_cache:
                card_cache[encounter] = load_card(DECK_DIR / encounter / "card.yaml")
            return card_cache[encounter]

        def _reference_diff(encounter: str) -> str:
            if encounter not in reference_cache:
                reference_cache[encounter] = (
                    DECK_DIR / encounter / "hidden" / "reference.patch"
                ).read_text(encoding="utf-8")
            return reference_cache[encounter]

        def make_execute(*, interrupt_after: int | None):
            calls = {"n": 0}

            def execute(item: ScheduleItem, attempt: int) -> dict:
                if interrupt_after is not None and calls["n"] == interrupt_after:
                    raise Interrupted(f"模擬 kill：第 {calls['n'] + 1} 個 run 前中斷")
                calls["n"] += 1

                card = _card(item.encounter)
                loadout = Loadout.from_string(item.loadout)
                if item.model == "fixer":
                    replies = build_fixer_script(
                        card, _reference_diff(item.encounter), loadout
                    )
                else:
                    replies = build_flooder_script(card)
                adapter = ScriptedAdapter(replies)

                effective_run_id = (
                    item.run_id if attempt == 1 else f"{item.run_id}--attempt{attempt}"
                )
                result = _wire_and_run_encounter(
                    card,
                    DECK_DIR / item.encounter,
                    adapter,
                    loadout,
                    runs_root,
                    effective_run_id,
                    bwrap_path=DEFAULT_BWRAP_PATH,
                    record_extra={
                        "model": f"scripted:{item.model}",
                        "model_id": item.model,
                        "schedule_ref": sealed.sha256,
                    },
                )
                return {
                    "run_dir": str(runs_root / effective_run_id),
                    "end_reason": result.end_reason,
                    "clear": result.clear,
                }

            return execute

        def make_runner(execute) -> PilotRunner:
            return PilotRunner(
                registry_path=runs_root / "registry.jsonl",
                execute=execute,
                models=models,
                capabilities=lambda: IsolationRunner(
                    Path("/tmp"), bwrap_path=DEFAULT_BWRAP_PATH
                ).capabilities(),
                repo_root=estimators_repo,
            )

        # ---- 中斷（模擬 kill）：第 41 個 run 前直接拋例外 -------------------
        interrupt_execute = make_execute(interrupt_after=40)
        with pytest.raises(Interrupted):
            make_runner(interrupt_execute).run(schedule_path)

        registry_path = runs_root / "registry.jsonl"
        done_after_interrupt = _final_run_dirs(registry_path)
        assert len(done_after_interrupt) == 40

        # ---- 續跑（模擬重啟：全新 PilotRunner、同一 schedule/registry） ----
        resume_execute = make_execute(interrupt_after=None)
        report = make_runner(resume_execute).run(schedule_path)
        all_run_ids = {item.run_id for item in sealed.items}
        assert len(report.executed) == 88
        assert len(report.skipped) == 40
        assert set(report.executed) | set(report.skipped) == all_run_ids
        assert set(report.executed) & set(report.skipped) == set()
        assert set(report.skipped) == set(done_after_interrupt)

        # ---- 冪等：全部完成後再跑一次 → 零執行、全 skipped -----------------
        noop_execute = make_execute(interrupt_after=None)
        report2 = make_runner(noop_execute).run(schedule_path)
        assert report2.executed == ()
        assert sorted(report2.skipped) == sorted(item.run_id for item in sealed.items)

        # ---- 全部 128 個 run 都有 result.yaml（總數不因中斷而變） ----------
        final_dirs = _final_run_dirs(registry_path)
        assert len(final_dirs) == 128
        assert set(final_dirs) == {item.run_id for item in sealed.items}
        for run_id, run_dir in final_dirs.items():
            result_path = Path(run_dir) / "result.yaml"
            assert result_path.is_file(), f"{run_id} 缺 result.yaml"

        # fixer 劇本對每個 (encounter, loadout) 都合法地套用 reference.patch
        # → 全部 64 個 fixer cell 都 Clear=1；flooder 從不觸及 MAIN → 全部
        # 64 個 flooder cell 都 Clear=0（sanity，非本 task 核心斷言，但能在
        # 「串線問題」階段快速定位是哪個 loadout／encounter 組合壞掉）。
        fixer_clears = 0
        flooder_clears = 0
        for item in sealed.items:
            run_dir = Path(final_dirs[item.run_id])
            result = yaml.safe_load(
                (run_dir / "result.yaml").read_text(encoding="utf-8")
            )
            if item.model == "fixer":
                fixer_clears += result["clear"]
                assert result["clear"] == 1, (item.run_id, result["end_reason"])
            else:
                flooder_clears += result["clear"]
                assert result["clear"] == 0, (item.run_id, result["end_reason"])

        # ---- report 產出 ---------------------------------------------------
        # 聚合鍵是 (model, loadout)：每個 model 在 clear_rate 榜上有 8 列
        # （每個 loadout 一列，逐列橫跨 8 個 encounters 算 clear rate）。
        out_dir = tmp_path / "report-out"
        report_doc = build_report(str(runs_root / "*"), out_dir)
        assert report_doc["runs_included"] == 128
        assert (out_dir / "report.yaml").is_file()
        assert (out_dir / "clear_rate.csv").is_file()
        clear_rate = report_doc["leaderboards"]["clear_rate"]
        assert len(clear_rate["rows"]) == 16  # 2 models × 8 loadouts
        fixer_clears_reported = sum(
            row["clears"] for row in clear_rate["rows"] if row["model"] == "scripted:fixer"
        )
        flooder_clears_reported = sum(
            row["clears"]
            for row in clear_rate["rows"]
            if row["model"] == "scripted:flooder"
        )
        assert fixer_clears_reported == fixer_clears == 64  # 全部 8×8 cell 都修好
        assert flooder_clears_reported == flooder_clears == 0

        # ---- 抽 3 個 run replay_l1 位元一致 ---------------------------------
        sample_ids = [
            sealed.items[0].run_id,
            sealed.items[len(sealed.items) // 2].run_id,
            sealed.items[-1].run_id,
        ]
        for run_id in sample_ids:
            replay = replay_l1(Path(final_dirs[run_id]))
            assert replay.identical, (run_id, replay.diffs)
