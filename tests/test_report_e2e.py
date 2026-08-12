"""Task 17 e2e：`patchmud report` milestone C 收口（spec §10.3、§13；報告 §11.2）。

兩場 scripted run（1 clear、1 fail；真 Workspace＋fake 執行面，invariant 3）
→ `patchmud report --runs <glob> --out <dir>` 產出 §11.2 對應的多榜 YAML/CSV：

- clear rate：通關率逐 (model, loadout) 群組。
- cost per clear：run.yaml pin 的 pricing snapshot（--pricing 提供同 hash 檔）
  → 金額 Decimal 字串；零 clear 群組 → ``"inf"``；未提供 snapshot → ``"NA"``
  （fail-closed，不得假 0，§10.1／§10.2）。
- tokens per clear：scripted run 的 T^work 為 NA（openai mapping 無
  cached/reasoning 揭露）→ value ``"NA"``、observable 欄照算（雙欄，F17）；
  零 clear 群組 observable → ``"inf"``。
- Power／Control／FTR：群組平均；τ 未校準必須標記 ``tau_uncalibrated``。
- EuTB：pre-registered ``eutb_budget.yaml`` 缺失 → 該榜標記 ``skipped``
  而非假值（報告 §19.9）；registered 檔存在 → 正常計算。
- human run（``human: true``）不進 ranked 榜：列入 runs_skipped。
- 跨 disclosure cohort（F17／§10.1）：群組 cohort 不一致 → 效率榜退為
  non-ranking，rows **只發布** 以 input + output_visible 一致計算的
  common-observable 描述性欄位（不得帶 cohort 依賴的 ``value`` 欄），且
  YAML rows 與 CSV 逐列帶 ``non_ranking``＋``note`` 標註（§13——CSV 檔案層
  必須能與排名榜區分）；排名一律委派 metrics 層 ``rank_efficiency``
  （方向由指標 pin、跨 cohort 在該層被拒），report 層不得重實作。
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import yaml

from patchmud.adapters.scripted import ScriptedAdapter
from patchmud.cli import main
from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.engine.loop import RunConfig, run_encounter
from patchmud.engine.prompts import HARNESS_PROMPT_VERSION
from patchmud.engine.strategy import SOLO
from patchmud.evaluator.evaluate import FinalEvaluation
from patchmud.evaluator.gates import apply_power_cap, evaluate_gates
from patchmud.evaluator.power import FileChange, score_power
from patchmud.ledger.pricing import PricingSnapshot
from patchmud.sandbox.probes import ProbeResults, smoke_probe_id
from patchmud.sandbox.workspace import Workspace
from patchmud.store.run_store import RunStore
from tests.evaluator.helpers import make_outcome

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "mini_encounter"
PRICING_PATH = REPO_ROOT / "pricing" / "example" / "2026-07-16.yaml"

CARD = load_card(FIXTURE / "card.yaml")
MAIN = CARD.public_requirements[0].probe
STARTER = CARD.regression_probes[0].path
SMOKE = smoke_probe_id(CARD.regression_probes[1].smoke)
COMPAT = CARD.compat_probes[0].probe
HIDDEN = CARD.critical_requirements[0].hidden_probe
REFERENCE_DIFF = (FIXTURE / "hidden" / "reference.patch").read_text(encoding="utf-8")

BASE = {MAIN: "failed", STARTER: "passed", SMOKE: "passed", COMPAT: "passed"}
GREEN = {**BASE, MAIN: "passed"}

PATCH_REPLY = (
    "ACTION: PATCH\n"
    "TARGET_ISSUES: MAIN-1\n"
    "PATCH:\n" + REFERENCE_DIFF
)
COMMIT_REPLY = "ACTION: COMMIT"


# ---------------------------------------------------------------------------
# fakes（不執行任何 candidate code、不打網路；同 tests/store/test_replay.py）
# ---------------------------------------------------------------------------


class FakeSuite:
    """公開 probe 套件 fake：依呼叫次序回放 scripted 結果；耗盡沿用最後一組。"""

    def __init__(self, results: list[dict[str, str]]) -> None:
        self._results = results
        self.calls = 0

    @property
    def probe_ids(self) -> tuple[str, ...]:
        return tuple(self._results[0])

    def run(self, workspace, subset=None) -> ProbeResults:
        statuses = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        if subset is not None:
            wanted = set(subset)
            statuses = {k: v for k, v in statuses.items() if k in wanted}
        return ProbeResults(
            {pid: make_outcome(status) for pid, status in statuses.items()}
        )


class ConsistentEvaluate:
    """終局 evaluator fake：以真 rubric 純函數在合成 outcomes 上評分。"""

    def __init__(
        self,
        card,
        statuses: dict[str, str],
        *,
        file_changes: tuple[FileChange, ...],
    ) -> None:
        self.card = card
        self.statuses = statuses
        self.file_changes = file_changes

    def __call__(self, final_diff: str) -> FinalEvaluation:
        outcomes = ProbeResults(
            {pid: make_outcome(status) for pid, status in self.statuses.items()}
        )
        gates = evaluate_gates(self.card, outcomes)
        power = apply_power_cap(
            score_power(
                self.card,
                outcomes,
                self.file_changes,
                lint_new_diagnostics=0,
                reference_timings_ms={HIDDEN: 100.0},
            ),
            gates,
        )
        return FinalEvaluation(probe_outcomes=outcomes, power=power, gates=gates)


# ---------------------------------------------------------------------------
# 佈線 helpers
# ---------------------------------------------------------------------------


def pricing_hash() -> str:
    return PricingSnapshot.load(PRICING_PATH).content_hash


def make_run(
    tmp_path: Path,
    runs_root: Path,
    *,
    run_id: str,
    model: str,
    replies: list[str],
    suite_results: list[dict[str, str]],
    evaluator_statuses: dict[str, str],
    file_changes: tuple[FileChange, ...],
    expected_clear: int,
) -> Path:
    """scripted run → run 目錄（run.yaml pin 住 example pricing snapshot）。"""
    frozen = materialize_repo(FIXTURE, tmp_path / f"worktree-{run_id}")
    workspace = Workspace(
        frozen=frozen,
        encounter_dir=FIXTURE,
        shadow_dir=tmp_path / f"shadow-{run_id}",
    )
    store = RunStore.create(
        {
            "run_id": run_id,
            "frozen_sha": frozen.sha,
            "pricing_hash": pricing_hash(),
            "harness_prompt_version": HARNESS_PROMPT_VERSION,
            "schedule_ref": "NA",
            "encounter_dir": str(FIXTURE),
            "model": model,
        },
        runs_root,
    )
    config = RunConfig(
        workspace=workspace,
        probe_suite=FakeSuite(suite_results),
        evaluate=ConsistentEvaluate(
            CARD, evaluator_statuses, file_changes=file_changes
        ),
    )
    result = run_encounter(CARD, ScriptedAdapter(replies), SOLO, config, store)
    assert result.clear == expected_clear  # 前提：劇本行為固定
    return store.run_dir


def make_fixture_runs(tmp_path: Path) -> Path:
    """兩場 scripted run：fixer 兩回合修好（clear=1）、quitter 直接棄局
    （clear=0，critical 紅）。回傳 runs root。"""
    runs_root = tmp_path / "runs"
    make_run(
        tmp_path,
        runs_root,
        run_id="report-fixer",
        model="scripted:fixer",
        replies=[PATCH_REPLY, COMMIT_REPLY],
        suite_results=[BASE, GREEN, GREEN],
        evaluator_statuses={HIDDEN: "passed", COMPAT: "passed"},
        file_changes=(FileChange(path="src/inventory.py", added=4, deleted=1),),
        expected_clear=1,
    )
    make_run(
        tmp_path,
        runs_root,
        run_id="report-quitter",
        model="scripted:quitter",
        replies=[COMMIT_REPLY],
        suite_results=[BASE],
        evaluator_statuses={HIDDEN: "failed", COMPAT: "passed"},
        file_changes=(),
        expected_clear=0,
    )
    return runs_root


def run_report(runs_root: Path, out_dir: Path, *extra: str) -> dict:
    rc = main(
        ["report", "--runs", str(runs_root / "*"), "--out", str(out_dir), *extra]
    )
    assert rc == 0
    return yaml.safe_load((out_dir / "report.yaml").read_text(encoding="utf-8"))


def make_mixed_cohort_runs(tmp_path: Path) -> Path:
    """兩場 run 分屬不同 disclosure cohort（F17 的現實情境：anthropic
    mapper reasoning 恆 NA → observable、openai 揭露 reasoning_tokens →
    full）。scripted run 的 T^work 皆 NA，這裡把 fixer 的封存改寫成
    full-disclosure（ledger.work_tokens 為正整數）製造 mixed cohort。"""
    runs_root = make_fixture_runs(tmp_path)
    result_path = runs_root / "report-fixer" / "result.yaml"
    data = yaml.safe_load(result_path.read_text(encoding="utf-8"))
    assert data["ledger"]["work_tokens"] == "NA"  # 前提：scripted → NA
    data["ledger"]["work_tokens"] = 4321
    result_path.write_text(
        yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    return runs_root


def write_registered_budget(tmp_path: Path) -> Path:
    registered = tmp_path / "registered"
    registered.mkdir()
    (registered / "eutb_budget.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": 1, "budget_tokens": 100_000, "grid_points": 256}
        ),
        encoding="utf-8",
    )
    return registered


def read_csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def row_of(board: dict, model: str) -> dict:
    matches = [row for row in board["rows"] if row["model"] == model]
    assert len(matches) == 1, f"{model} 應恰有一列：{board['rows']!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# 多榜 report（plan Task 17 Step 1 RED）
# ---------------------------------------------------------------------------


class TestReportLeaderboards:
    def test_multi_leaderboard_report(self, tmp_path) -> None:
        runs_root = make_fixture_runs(tmp_path)
        out = tmp_path / "out"
        report = run_report(runs_root, out, "--pricing", str(PRICING_PATH))

        assert report["schema_version"] == 1
        assert report["runs_included"] == 2
        assert report["runs_skipped"] == []
        boards = report["leaderboards"]

        # clear rate：fixer 1.0、quitter 0.0；最佳在前
        clear_rate = boards["clear_rate"]
        assert row_of(clear_rate, "scripted:fixer")["value"] == 1.0
        assert row_of(clear_rate, "scripted:quitter")["value"] == 0.0
        assert clear_rate["rows"][0]["model"] == "scripted:fixer"

        # cost per clear：pin 的 snapshot 計價 → fixer 有限正 Decimal；
        # 零 clear 群組 → "inf"（不剔除、不假 0）
        cost = boards["cost_per_clear"]
        fixer_cost = row_of(cost, "scripted:fixer")
        assert fixer_cost["ranked"] is True
        assert Decimal(fixer_cost["value"]) > 0
        assert Decimal(fixer_cost["value"]).is_finite()
        assert row_of(cost, "scripted:quitter")["value"] == "inf"
        assert cost["rows"][0]["model"] == "scripted:fixer"

        # tokens per clear：scripted run 的 T^work NA → value "NA"、
        # observable 雙欄照算（§10.1／F17）；零 clear → observable "inf"
        tokens = boards["tokens_per_clear"]
        fixer_tokens = row_of(tokens, "scripted:fixer")
        assert fixer_tokens["value"] == "NA"
        assert fixer_tokens["observable"] > 0
        assert fixer_tokens["disclosure_cohort"] == "observable"
        quitter_tokens = row_of(tokens, "scripted:quitter")
        assert quitter_tokens["value"] == "NA"
        assert quitter_tokens["observable"] == "inf"
        assert tokens["rows"][0]["model"] == "scripted:fixer"

        # Power：群組平均；fixer 顯著高於 quitter
        power = boards["power"]
        assert row_of(power, "scripted:fixer")["value"] >= 60
        assert (
            row_of(power, "scripted:fixer")["value"]
            > row_of(power, "scripted:quitter")["value"]
        )

        # Control：τ 未校準必須標記，不得假造校準值（spec §8.2）
        control = boards["control"]
        for model in ("scripted:fixer", "scripted:quitter"):
            row = row_of(control, model)
            assert 0 < row["value"] <= 100
            assert row["tau_uncalibrated"] is True

        # FTR：兩場都沒有 flooding（backlog 從未高於 B_0）→ 0.0
        ftr = boards["ftr"]
        for model in ("scripted:fixer", "scripted:quitter"):
            assert row_of(ftr, model)["value"] == 0.0

        # EuTB：registered 檔缺失 → 標記 skipped 而非假值（報告 §19.9）
        eutb = boards["eutb"]
        assert eutb["status"] == "skipped"
        assert eutb["reason"]
        assert "rows" not in eutb

        # CSV：有列資料的榜各出一份；skipped 榜不出假 CSV
        for name in (
            "clear_rate",
            "cost_per_clear",
            "tokens_per_clear",
            "power",
            "control",
            "ftr",
        ):
            assert (out / f"{name}.csv").is_file()
        assert not (out / "eutb.csv").exists()
        csv_lines = (
            (out / "clear_rate.csv").read_text(encoding="utf-8").strip().splitlines()
        )
        assert len(csv_lines) == 3  # header + 兩群組

    def test_cost_na_without_pricing_snapshot(self, tmp_path) -> None:
        """未提供 --pricing → cost 一律 "NA"（fail-closed，不得假 0）。"""
        runs_root = make_fixture_runs(tmp_path)
        report = run_report(runs_root, tmp_path / "out")
        cost = report["leaderboards"]["cost_per_clear"]
        for model in ("scripted:fixer", "scripted:quitter"):
            row = row_of(cost, model)
            assert row["value"] == "NA"
            assert row["ranked"] is False
            assert row["reason"]

    def test_eutb_computed_with_registered_budget(self, tmp_path) -> None:
        """registered 預算檔存在 → EuTB 正常計算（skipped 不是寫死）。"""
        runs_root = make_fixture_runs(tmp_path)
        registered = tmp_path / "registered"
        registered.mkdir()
        (registered / "eutb_budget.yaml").write_text(
            yaml.safe_dump(
                {"schema_version": 1, "budget_tokens": 100_000, "grid_points": 256}
            ),
            encoding="utf-8",
        )
        report = run_report(
            runs_root, tmp_path / "out", "--registered", str(registered)
        )
        eutb = report["leaderboards"]["eutb"]
        assert eutb["status"] == "ok"
        fixer = row_of(eutb, "scripted:fixer")
        assert fixer["value"] == "NA"  # T^work NA → 雙欄傳染
        assert 0 < fixer["observable"] <= 1
        assert row_of(eutb, "scripted:quitter")["observable"] == 0.0

    def test_human_run_excluded_from_ranked_boards(self, tmp_path) -> None:
        """human run 不進 ranked 榜：列入 runs_skipped、榜上不出現。"""
        runs_root = make_fixture_runs(tmp_path)
        human_dir = make_run(
            tmp_path,
            runs_root,
            run_id="report-human",
            model="scripted:human",
            replies=[COMMIT_REPLY],
            suite_results=[BASE],
            evaluator_statuses={HIDDEN: "failed", COMPAT: "passed"},
            file_changes=(),
            expected_clear=0,
        )
        result_path = human_dir / "result.yaml"
        data = yaml.safe_load(result_path.read_text(encoding="utf-8"))
        data["human"] = True
        result_path.write_text(
            yaml.safe_dump(data, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )

        report = run_report(runs_root, tmp_path / "out")
        assert report["runs_included"] == 2
        assert len(report["runs_skipped"]) == 1
        skipped = report["runs_skipped"][0]
        assert skipped["run_id"] == "report-human"
        assert "human" in skipped["reason"]
        rows = report["leaderboards"]["clear_rate"]["rows"]
        assert {row["model"] for row in rows} == {
            "scripted:fixer",
            "scripted:quitter",
        }


# ---------------------------------------------------------------------------
# 跨 disclosure cohort 發布契約（F17／§10.1／§13；review findings 1–3）
# ---------------------------------------------------------------------------

#: F17 管轄的效率榜（TokensPerClear／QATY／EuTB）。
EFFICIENCY_BOARDS = ("tokens_per_clear", "qaty", "eutb")


class TestMixedCohortPublication:
    def test_mixed_cohort_publishes_only_common_observable(self, tmp_path) -> None:
        """cohort 不一致 → 效率榜 rows 不得帶 cohort 依賴的 value 欄。

        full-disclosure 群組的 T^work 基礎值一旦發布，跨 cohort 比較就
        繞過 non_ranking 標註成立（F17：「跨 cohort 只發布以 input +
        output_visible 一致計算的 common-observable 描述性欄位」）。
        """
        runs_root = make_mixed_cohort_runs(tmp_path)
        registered = write_registered_budget(tmp_path)
        report = run_report(
            runs_root, tmp_path / "out", "--registered", str(registered)
        )

        for name in EFFICIENCY_BOARDS:
            board = report["leaderboards"][name]
            assert board["status"] == "ok", name
            assert board["non_ranking"] is True, name
            assert "F17" in board["note"], name
            # 列序退為群組名稱字典序（不構成排名）
            assert [row["model"] for row in board["rows"]] == [
                "scripted:fixer",
                "scripted:quitter",
            ], name
            for row in board["rows"]:
                # cohort 依賴的 value 欄不得出現在發布產物
                assert "value" not in row, (name, row)
                assert "observable" in row, (name, row)
                # 逐列標註：YAML rows 即 CSV rows，檔案層自我描述（§13）
                assert row["non_ranking"] is True, (name, row)
                assert "F17" in row["note"], (name, row)
            cohorts = {row["disclosure_cohort"] for row in board["rows"]}
            assert cohorts == {"full", "observable"}, name

    def test_mixed_cohort_csv_marked_and_without_value_column(
        self, tmp_path
    ) -> None:
        """non_ranking 榜的 CSV 檔內必須自帶標註（§13）。

        ranked 榜 CSV 的列序即排名——跨 cohort CSV 若無 non_ranking／note
        欄位，檔案層與排名榜無法區分（review finding 2）。
        """
        runs_root = make_mixed_cohort_runs(tmp_path)
        registered = write_registered_budget(tmp_path)
        out = tmp_path / "out"
        run_report(runs_root, out, "--registered", str(registered))

        for name in EFFICIENCY_BOARDS:
            rows = read_csv_rows(out / f"{name}.csv")
            assert len(rows) == 2, name
            for row in rows:
                assert "value" not in row, (name, row)
                assert row["non_ranking"] == "True", (name, row)
                assert "F17" in row["note"], (name, row)

    def test_ranked_efficiency_rows_marked_non_ranking_false(
        self, tmp_path
    ) -> None:
        """同 cohort 的排名榜逐列帶 non_ranking=False——CSV 檔案層即可
        與跨 cohort 榜區分（§13），不必回頭翻 report.yaml。"""
        runs_root = make_fixture_runs(tmp_path)  # 兩群組同為 observable
        out = tmp_path / "out"
        report = run_report(runs_root, out)

        board = report["leaderboards"]["tokens_per_clear"]
        assert board["non_ranking"] is False
        for row in board["rows"]:
            assert row["non_ranking"] is False
        csv_rows = read_csv_rows(out / "tokens_per_clear.csv")
        assert [row["non_ranking"] for row in csv_rows] == ["False", "False"]

    def test_efficiency_ordering_delegates_to_rank_efficiency(
        self, tmp_path, monkeypatch
    ) -> None:
        """report 層排名必須委派 metrics 層 rank_efficiency（F17 防線）。

        report 層若自行重實作排名，metrics 層的 CohortMismatchError 防線
        對 report 不生效（review finding 3）——以 stub 座標鎖住委派：列序
        必須跟著 rank_efficiency 的回傳走。
        """
        import patchmud.cli as cli_mod

        calls: list[dict] = []

        def fake_rank(results):
            calls.append(dict(results))
            return tuple(sorted(results, reverse=True))  # 反字典序

        monkeypatch.setattr(cli_mod, "rank_efficiency", fake_rank)
        runs_root = make_fixture_runs(tmp_path)
        report = run_report(runs_root, tmp_path / "out")

        assert calls, "report 層未呼叫 rank_efficiency"
        # 真排名會把 fixer（有限 observable）排在 quitter（inf）前；
        # stub 反字典序 → quitter 在前 ⟺ 列序確實來自 rank_efficiency
        board = report["leaderboards"]["tokens_per_clear"]
        assert [row["model"] for row in board["rows"]] == [
            "scripted:quitter",
            "scripted:fixer",
        ]


class TestReportRunProvenanceFields:
    """runs[] 逐列透傳 encounter／end_reason／protocol_failed（issue #24）。

    下游（cortex #452 profile 巷道）要能從 report 層做全覆蓋精確驗證，並把
    「未通關因協定失敗」與「未通關因修不好」分開——後者才是能力訊號
    （#21：格式噪音不得被誤讀成能力缺陷）。schema_version 維持 1（純加欄）。
    """

    def test_runs_rows_carry_encounter_and_end_reason(self, tmp_path) -> None:
        runs_root = make_fixture_runs(tmp_path)
        report = run_report(runs_root, tmp_path / "out")

        assert report["schema_version"] == 1  # 加欄不 bump（下游 fail-closed 鎖 1）
        rows = {row["run_id"]: row for row in report["runs"]}
        for run_id in ("report-fixer", "report-quitter"):
            assert rows[run_id]["encounter"] == FIXTURE.name
            assert rows[run_id]["end_reason"] == "commit"
            assert rows[run_id]["protocol_failed"] is False

    def test_protocol_failure_distinguishable_from_capability(
        self, tmp_path
    ) -> None:
        """連 3 回合 parse error → failed:protocol 終局；report 列必須可辨。"""
        runs_root = tmp_path / "runs"
        make_run(
            tmp_path,
            runs_root,
            run_id="report-jibberish",
            model="scripted:jibberish",
            replies=["這不是合法指令", "這不是合法指令", "這不是合法指令"],
            suite_results=[BASE],
            evaluator_statuses={HIDDEN: "passed", COMPAT: "passed"},
            file_changes=(),
            expected_clear=0,
        )
        report = run_report(runs_root, tmp_path / "out")

        (row,) = report["runs"]
        assert row["clear"] == 0
        assert row["end_reason"] == "failed:protocol"
        assert row["protocol_failed"] is True
