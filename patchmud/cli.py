"""patchmud CLI 進入點。

子命令（validate-deck / score-diff / run / play / pilot / replay / report）依
docs/superpowers/plans/2026-07-16-patchmud-mvp.md 逐 task 落地。

`validate-deck`（Task 20，spec §4.2）：`patchmud validate-deck <deck_dir>`——
deck CI 驗證閘：對目錄下每個含 `card.yaml` 的 encounter 子目錄逐一驗證
card schema（`load_card`：必填欄位、`expected_paths ⊆ allowed_paths`、
public/hidden 路徑不重疊）、card 引用的 probe 檔案存在、`repo/` 可安裝可跑
（baseline：regression/compat probes 綠、MAIN probes 紅——issue 可重現）、
套用 `hidden/reference.patch`（production、嚴格模式）後 public probes 全綠、
hidden evaluator（獨立 checkout，§9.1）critical gate 成立且全部 rubric/
critical probe 綠，並以量測所得 wall_ms 覆寫 `hidden/reference_timings.yaml`
（deck CI 產物，僅供 evaluator 使用，永不進 run）。執行 candidate code 的
唯一 seam 是 `IsolationRunner`；namespace 能力不足一律 fail-closed 拒絕
執行。

`score-diff`（Task 8，milestone A 收口）：給定 encounter + 手工 diff，離線
跑完整評分——全部 public probes（§5.2 終局語意）+ hidden evaluator（§9）——
落盤 `result.yaml`（PowerReport、gates、probe outcomes、Clear、Economy）並
產出私有封存（§12.1）。執行 candidate code 的唯一 seam 是 `IsolationRunner`；
namespace 能力不足一律 fail-closed 拒絕執行。

`run`（Task 13，milestone B 收口）：`patchmud run --encounter --model
--loadout`——真模型（或 scripted 劇本）打完一場 encounter：turn 0 baseline →
author turns（render → complete → parse → enforcer → 執行 → probe 排程 →
queue → checkpoint → event）→ 終局四觸發全套評分 → result.yaml。model spec：
`scripted:<file>`（回覆以 `-----` 行分隔；dry-run 用）、
`anthropic:<model>`（env `ANTHROPIC_API_KEY`）、
`openai:<model>[@<base_url>]`（env `OPENAI_API_KEY`，涵蓋地端 vllm/ollama）。

`play`（Task 22，spec §5.4）：`patchmud play --encounter <dir> --loadout
P0T0R0`——`HumanAdapter`（stdin/stdout）取代模型 adapter，人類以同一命令
協定親自打一場 encounter；引擎、probe、queue、評分完全同構。ledger 全
token 欄位 `NA`、成本 `NA`；run 標記 `human: true`，永不進 ranked 資料與
任何聚合指標。回覆以空行結束；Ctrl-D（EOF）視同 COMMIT 收尾。

`watch`（Task 23，spec §5.4）：`patchmud watch <run_dir> [--turn N]`——離線把
封存 `events.jsonl` 重放成逐回合 zh-TW 戰報（開場基線、行動、probe 結果、
queue 變化、flood 壓力、終局結算），`--turn N` 只輸出該回合。資料只來自
封存 events 與 result.yaml（與 replay L1 同源），不重新執行任何 probe 或
模型呼叫、不寫回 run 目錄——純視圖層。

`replay`（Task 16，spec §12.2）：`patchmud replay <run_dir> [--l2]`——L1 位元
一致重算（不執行任何 probe；runtime_efficiency 引用封存 outcome），與封存
`result.yaml` 不一致 → exit non-zero；`--l2` 於 pinned 環境（deck 重物化 SHA
必須與封存一致）自 checkpoints shadow repo 重建 final diff 後重執行全部
probes——functional/compat/robustness 必須相等、perf-only 差異落 report 不算
fail——再走 L1。

`report`（Task 17，milestone C 收口；spec §10.3、§13、報告 §11.2）：
`patchmud report --runs <glob> [--out <dir>] [--pricing <snapshot>]
[--registered <dir>]`——只讀多場 run 的落盤封存（run.yaml／result.yaml／
events.jsonl／ledger.jsonl）＋ deck card，逐 (model, loadout) 群組輸出多榜
YAML/CSV：clear rate、cost per clear（run pin 的 pricing snapshot 計價；
未 pin／snapshot 不可得 → "NA" 不假 0）、tokens per clear／QATY／EuTB
（雙欄＋disclosure cohort，F17——排名委派 metrics 層 rank_efficiency；跨
cohort 整榜 non-ranking，rows 只發布 common-observable 描述性欄位且逐列
帶 non_ranking＋note 標註，CSV 檔案層即可與排名榜區分，§13；EuTB 缺
pre-registered 預算檔 → 標記 skipped 而非假值，§19.9）、Power／Control／
FTR（τ 未校準標記
tau_uncalibrated）。§11.2 其餘榜（MTY 曲線、one-shot、Pareto、composite）
待 pilot 資料齊備另行擴充。human run 不進 ranked 榜（列入 runs_skipped）。
"""

from __future__ import annotations

import argparse
import csv
import glob as globmod
import hashlib
import importlib.util
import math
from importlib import metadata
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from patchmud.adapters.agy_cli import AgyCliAdapter
from patchmud.adapters.anthropic import AnthropicAdapter
from patchmud.adapters.base import AdapterError, ModelAdapter
from patchmud.adapters.claude_cli import ClaudeCliAdapter
from patchmud.adapters.codex_cli import CodexCliAdapter
from patchmud.adapters.human import HumanAdapter
from patchmud.adapters.openai_compat import OpenAICompatAdapter
from patchmud.adapters.scripted import ScriptedAdapter
from patchmud.authoring import AuthoringError, build_encounter, load_source
from patchmud.deck.loader import load_card
from patchmud.deck.materialize import materialize_repo
from patchmud.deck.model import DeckError, IssueCard
from patchmud.engine import narration
from patchmud.engine import render_zh_tw as zh
from patchmud.engine.loop import RunConfig, build_agent_test_runner, run_encounter
from patchmud.engine.versus import VersusEntry, render_versus, run_versus
from patchmud.engine.pilot import (
    PilotError,
    PilotReport,
    PilotRunner,
    load_models,
)
from patchmud.engine.prompts import HARNESS_PROMPT_VERSION
from patchmud.engine.schedule import (
    RunMatrix,
    ScheduleError,
    ScheduleItem,
    build_schedule,
    load_schedule,
    save_schedule,
)
from patchmud.engine.strategy import Loadout
from patchmud.evaluator.evaluate import EvaluatorError, FinalEvaluation, evaluate_final
from patchmud.evaluator.gates import compute_clear
from patchmud.evaluator.power import PowerReport
from patchmud.ledger.cost import compute_run_cost
from patchmud.ledger.pricing import PricingSnapshot
from patchmud.ledger.tokens import LedgerEntry, LedgerError
from patchmud.metrics.economy import EconomyError, RunSample, cost_per_clear
from patchmud.metrics.efficiency import (
    CohortMismatchError,
    EfficiencyError,
    EfficiencyResult,
    NotRegisteredError,
    eutb,
    load_eutb_budget,
    qaty,
    rank_efficiency,
    tokens_per_clear,
)
from patchmud.metrics.calibration import (
    CalibrationError,
    CalibrationRun,
    FrozenCalibrationError,
    calibrate,
    freeze_calibration,
)
from patchmud.metrics.flood import FloodError, flood_metrics, load_flood_coeffs
from patchmud.sandbox.isolate import DEFAULT_BWRAP_PATH, IsolationRunner
from patchmud.sandbox.probes import (
    DEFAULT_PYTEST_ARGV,
    ProbeOutcome,
    ProbeResults,
    ProbeSuite,
    smoke_probe_id,
)
from patchmud.sandbox.workspace import Workspace, WorkspaceError
from patchmud.store.replay import (
    ReexecutedProbes,
    ReplayError,
    load_ledger,
    replay_l1,
    replay_l2,
)
from patchmud.store.run_store import RunStore
from patchmud.store.schemas import RESULT_SCHEMA_VERSION, StoreError
from patchmud.store.watch import (
    LiveSpectator,
    WatchError,
    render_battle_report,
    render_turn,
)

__all__ = [
    "DeckValidationError",
    "DeckValidationReport",
    "EncounterValidation",
    "ReportError",
    "RunCliError",
    "ScoreDiffError",
    "ScoreDiffSummary",
    "build_report",
    "main",
    "pilot_cli",
    "play_cli",
    "run_cli",
    "score_diff",
    "validate_deck",
]

#: 離線評分沒有 harness prompt / pricing / schedule；欄位以 NA 佔位（非 0，§10.1）。
_OFFLINE_NA = "NA"


class ScoreDiffError(Exception):
    """score-diff 操作性失敗（輸入缺漏、隔離能力不足、diff 遭拒等）。"""


class RunCliError(Exception):
    """run 子命令操作性失敗（model spec 非法、api key 缺席等）。"""


class ReportError(Exception):
    """report 子命令操作性失敗（glob 無 run、封存缺漏、欄位非法等）。"""


@dataclass(frozen=True)
class ScoreDiffSummary:
    """score-diff 完成後的摘要（CLI 輸出用）。"""

    run_dir: Path
    archive_path: Path
    clear: int
    critical_pass: bool
    power_total: float


#: 只打 encounter 名字時的預設 deck 搜尋根（依序）。
_DEFAULT_DECK_ROOTS = ("decks/pilot-v1", "decks")


def resolve_encounter(spec: str | Path) -> Path:
    """把 encounter spec 解析成目錄：可以是路徑，或只是關卡名字。

    - 是既有含 card.yaml 的目錄 → 直接用。
    - 否則當名字，依序找 `decks/pilot-v1/<名字>`、`decks/*/<名字>`。
    - 找不到 → 列出可玩關卡並報錯。
    """
    p = Path(spec)
    if (p / "card.yaml").is_file():
        return p
    name = str(spec)
    candidates = [Path(root) / name for root in _DEFAULT_DECK_ROOTS]
    candidates += sorted(Path("decks").glob(f"*/{name}")) if Path("decks").is_dir() else []
    for cand in candidates:
        if (cand / "card.yaml").is_file():
            return cand
    available = sorted(
        d.name for d in Path("decks/pilot-v1").iterdir() if (d / "card.yaml").is_file()
    ) if Path("decks/pilot-v1").is_dir() else []
    hint = ("，可玩關卡：" + "、".join(available)) if available else ""
    raise RunCliError(f"找不到 encounter「{spec}」（非路徑也非關卡名）{hint}")


def _list_available_encounters() -> list[str]:
    encounters: list[str] = []
    for root in ("decks/pilot-v1", "decks/authored-demo", "decks"):
        p = Path(root)
        if p.is_dir():
            for d in sorted(p.iterdir()):
                if d.is_dir() and (d / "card.yaml").is_file() and d.name not in encounters:
                    encounters.append(d.name)
    return encounters


def _prompt_select_encounter() -> str | None:
    encounters = _list_available_encounters()
    if not encounters:
        print("未找到任何可用的關卡（decks/ 下無 card.yaml）", file=sys.stderr)
        return None
    print("\n【請選擇要進行的關卡】")
    for idx, enc in enumerate(encounters, 1):
        print(f"  [{idx}] {enc}")
    try:
        ans = input(f"請輸入號碼 (1-{len(encounters)}，預設 1): ").strip()
        if not ans:
            return encounters[0]
        choice = int(ans)
        if 1 <= choice <= len(encounters):
            return encounters[choice - 1]
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return encounters[0]


def _prompt_select_models() -> str:
    presets = [
        ("sonnet,haiku", "Sonnet vs Haiku（經典速度與能力對決）"),
        ("sonnet,opus", "Sonnet vs Opus（旗艦模型對決）"),
        ("haiku,fable", "Haiku vs Fable（輕量與實驗模型對決）"),
        ("sonnet,sol,flash", "Claude vs GPT vs Gemini（跨家旗艦對決）"),
    ]
    print("\n【請選擇對戰模型組合】")
    if not has_anthropic_credentials():
        print("⚠️ 未偵測到 Anthropic 憑證（ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN）")
        print("   選項 [1]-[3] 需雲端 API Key（或系統已登入的 claude CLI）。")
        print("   codex 別名（spark/luna/terra/sol）與 agy 別名（flash/pro）走各自 CLI 的登入態，不需 Anthropic Key。")
        print(f"   若都沒有，請選擇 [{len(presets) + 1}] 使用地端模型 (openai:...) 或 scripted 劇本。\n")
    for idx, (spec, desc) in enumerate(presets, 1):
        print(f"  [{idx}] {spec} — {desc}")
    print(f"  [{len(presets) + 1}] 自訂 / 地端免 Key 模型（如 openai:llama3@http://localhost:11434/v1）")
    try:
        ans = input(f"請選擇號碼 (1-{len(presets) + 1}，預設 1): ").strip()
        if not ans:
            return presets[0][0]
        # 若使用者直接輸入含逗號的模型字串（如 "haiku,opus" 或 "4, haiku,opus"）
        if "," in ans:
            cleaned = ans.lstrip("0123456789, ").strip()
            if cleaned:
                return cleaned
        if ans.isdigit():
            choice = int(ans)
            if 1 <= choice <= len(presets):
                return presets[choice - 1][0]
            if choice == len(presets) + 1:
                print("\n💡 【地端/免 Key 模型提示】：可用 `openai:<model>@http://localhost:11434/v1`（如 Ollama/vLLM）")
                custom = input("請輸入模型（逗號分隔，例如 openai:llama3@http://localhost:11434/v1）: ").strip()
                return custom if custom else presets[0][0]
        return ans
    except (ValueError, KeyboardInterrupt, EOFError):
        pass
    return presets[0][0]


def _interactive_menu() -> int:
    print("════════════════════════════════════════")
    print("    🎮 歡迎來到 PatchMUD 互動選單 🎮")
    print("════════════════════════════════════════")
    print(" [1] 🎮 人類親自挑戰關卡 (Human Play)")
    print(" [2] ⚔️ 看 AI 模型並排對戰 (Versus)")
    print(" [3] 🤖 看單一 AI 模型單挑關卡 (Single Run)")
    print(" [4] 🍿 離線觀戰重播 (Watch Battle Report)")
    print(" [5] 🔍 驗證 Deck 關卡品質 (Validate Deck)")
    print(" [0] 🚪 離開 (Exit)\n")
    try:
        choice = input("請選擇操作 (0-5): ").strip()
    except (KeyboardInterrupt, EOFError):
        return 0
    if choice == "1":
        enc = _prompt_select_encounter()
        if enc:
            return _cmd_play([enc])
    elif choice == "2":
        enc = _prompt_select_encounter()
        models = _prompt_select_models()
        if enc and models:
            return _cmd_versus([enc, "--models", models])
    elif choice == "3":
        enc = _prompt_select_encounter()
        print("\n請選擇模型（Anthropic: sonnet / haiku / opus / fable，"
              "codex: spark / luna / terra / sol，agy: flash / pro）：")
        try:
            m = input("模型名稱 [預設 sonnet]: ").strip() or "sonnet"
        except (KeyboardInterrupt, EOFError):
            m = "sonnet"
        if enc:
            return _cmd_run([enc, "--model", m, "--live"])
    elif choice == "4":
        runs_dir = Path("runs")
        available = (
            sorted([d.name for d in runs_dir.iterdir() if d.is_dir()])
            if runs_dir.is_dir()
            else []
        )
        if not available:
            print("目前沒有任何已落盤的 run 目錄 (runs/)，請先執行遊戲對局！", file=sys.stderr)
            return 1
        print("\n【請選擇要觀戰的 Run 目錄】")
        for idx, r in enumerate(available, 1):
            print(f"  [{idx}] {r}")
        try:
            ans = input(f"請輸入號碼 (1-{len(available)}，預設 {len(available)}): ").strip()
            idx = int(ans) if ans else len(available)
            if 1 <= idx <= len(available):
                return _cmd_watch([str(runs_dir / available[idx - 1])])
        except (ValueError, KeyboardInterrupt, EOFError):
            pass
    elif choice == "5":
        return _cmd_validate_deck(["decks/pilot-v1"])
    return 0


def _package_version() -> str:
    """CLI 顯示用版號；不硬編，避免與 ``VERSION`` 漂移。

    先讀 repo 根的 ``VERSION``（`pyproject.toml` 的 dynamic version 也讀它，
    是單一真實來源）；source tree 或 editable install 都命中這條。正式安裝時
    ``VERSION`` 不在 package 內，才退回已安裝的 distribution metadata——反過來
    的順序在 editable install 下會取到安裝當時的舊版號。兩者都取不到回
    ``unknown``：顯示字串不值得讓 CLI 失敗。
    """
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        declared = version_file.read_text(encoding="utf-8").strip()
    except OSError:
        declared = ""
    if declared:
        return declared
    try:
        return metadata.version("paulsha-patchmud")
    except metadata.PackageNotFoundError:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] in ("menu", "--interactive", "-i"):
        if sys.stdin.isatty() or (args and args[0] in ("menu", "--interactive", "-i")):
            return _interactive_menu()
        print(
            f"patchmud {_package_version()} — 子命令：validate-deck / author-encounter / "
            "score-diff / run / versus / play / watch / replay / report / pilot；其餘見 "
            "docs/superpowers/plans/2026-07-16-patchmud-mvp.md"
        )
        return 0
    if args[0] == "validate-deck":
        return _cmd_validate_deck(args[1:])
    if args[0] == "author-encounter":
        return _cmd_author_encounter(args[1:])
    if args[0] == "score-diff":
        return _cmd_score_diff(args[1:])
    if args[0] == "run":
        return _cmd_run(args[1:])
    if args[0] == "versus":
        return _cmd_versus(args[1:])
    if args[0] == "play":
        return _cmd_play(args[1:])
    if args[0] == "watch":
        return _cmd_watch(args[1:])
    if args[0] == "replay":
        return _cmd_replay(args[1:])
    if args[0] == "report":
        return _cmd_report(args[1:])
    if args[0] == "pilot":
        return _cmd_pilot(args[1:])
    if args[0] == "calibrate":
        return _cmd_calibrate(args[1:])
    print(f"patchmud: 子命令尚未實作：{args[0]}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# validate-deck 子命令（spec §4.2；plan Task 20 acceptance gate）
# ---------------------------------------------------------------------------

_TIMINGS_SCHEMA_VERSION = 1


class DeckValidationError(Exception):
    """validate-deck 目錄層級操作性失敗（找不到 encounter、隔離能力不足等）。"""


@dataclass(frozen=True)
class EncounterValidation:
    """單一 encounter 的驗證結果（`checks` 為依序完成的檢查名稱，供除錯）。"""

    encounter_id: str
    passed: bool
    checks: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class DeckValidationReport:
    deck_dir: Path
    encounters: tuple[EncounterValidation, ...]

    @property
    def all_passed(self) -> bool:
        return bool(self.encounters) and all(e.passed for e in self.encounters)


def _cmd_validate_deck(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud validate-deck",
        description=(
            "Deck CI：驗證目錄下每個 encounter 的 card schema、probe 檔案存在、"
            "hidden probes 在 reference.patch 下全綠，並量測 "
            "reference_timings.yaml（spec §4.2）。"
        ),
    )
    parser.add_argument("deck_dir", type=Path, help="deck 目錄（含多個 encounter 子目錄）")
    parser.add_argument(
        "--no-write-timings",
        action="store_true",
        help="不覆寫 reference_timings.yaml（預設量測後覆寫，deck CI 產物語意）",
    )
    ns = parser.parse_args(argv)

    try:
        report = validate_deck(ns.deck_dir, write_timings=not ns.no_write_timings)
    except (
        DeckValidationError,
        DeckError,
        WorkspaceError,
        EvaluatorError,
        ScoreDiffError,
    ) as exc:
        print(f"validate-deck 失敗：{exc}", file=sys.stderr)
        return 2

    passed = sum(1 for e in report.encounters if e.passed)
    total = len(report.encounters)
    for e in report.encounters:
        status = "PASS" if e.passed else "FAIL"
        print(f"[{status}] {e.encounter_id}")
        if not e.passed:
            print(f"    {e.error}")
    print(f"validate-deck：{passed}/{total} PASS")
    return 0 if report.all_passed else 1


# ---------------------------------------------------------------------------
# author-encounter 子命令（Part B：結構化出題）
# ---------------------------------------------------------------------------


def _authoring_validate(encounter_dir: Path) -> None:
    """出題品質閘：把單一新關 symlink 進 temp deck，跑真 validate_deck。

    重用 pilot 題同一套 CI——baseline 未套 patch 時 MAIN 紅（bug 可重現）、
    regression 綠；套 reference.patch 後 public 與 hidden 全綠。任一關 FAIL
    一律 raise AuthoringError（呼叫端 fail-closed）。
    """
    encounter_dir = Path(encounter_dir).resolve()
    with tempfile.TemporaryDirectory(prefix="patchmud-author-") as tmp:
        deck = Path(tmp) / "deck"
        deck.mkdir()
        (deck / encounter_dir.name).symlink_to(
            encounter_dir, target_is_directory=True
        )
        report = validate_deck(deck, write_timings=True)
    if not report.all_passed:
        detail = "；".join(
            f"{e.encounter_id}: {e.error}"
            for e in report.encounters
            if not e.passed
        )
        raise AuthoringError(detail)


def _cmd_author_encounter(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud author-encounter",
        description=(
            "把一個已解決（closed）的 bug 以結構化 source.yaml 凍結成 deck 關卡："
            "寫出 repo/hidden/card/provenance，並經品質閘（bug 可重現 + fix 為真）"
            "驗證後凍結。品質閘走與 validate-deck 相同的隔離 CI。"
        ),
    )
    parser.add_argument("source", type=Path, help="source.yaml（出題輸入）")
    parser.add_argument(
        "--into",
        type=Path,
        required=True,
        help="deck 目錄根（新關寫入 <into>/<issue_id>）",
    )
    ns = parser.parse_args(argv)

    try:
        spec = load_source(ns.source)
        encounter_dir = build_encounter(
            spec,
            ns.into,
            validate=_authoring_validate,
            now=time.strftime("%Y-%m-%d"),
        )
    except (
        AuthoringError,
        DeckValidationError,
        DeckError,
        WorkspaceError,
        EvaluatorError,
        ScoreDiffError,
    ) as exc:
        print(f"author-encounter 失敗：{exc}", file=sys.stderr)
        return 2

    print(f"[OK] 新關已凍結：{encounter_dir}")
    print(
        "     品質閘通過：bug 可重現（baseline MAIN 紅）、fix 為真"
        "（reference.patch 下 public/hidden 全綠）。"
    )
    return 0


def validate_deck(
    deck_dir: Path,
    *,
    bwrap_path: str = DEFAULT_BWRAP_PATH,
    write_timings: bool = True,
) -> DeckValidationReport:
    """對 deck 目錄下每個 encounter 逐一驗證（spec §4.2）。

    每個 encounter：schema／probe 檔案存在（`load_card` 已涵蓋必填欄位、
    `expected_paths ⊆ allowed_paths`、public/hidden 不重疊）→ baseline
    （未套 patch）跑全部 public probes（regression/compat 必須綠、MAIN
    必須 `failed`——代表 repo 可安裝可跑且 issue 可重現）→ 套用
    `hidden/reference.patch`（production、嚴格模式）→ 套用後 public probes
    全綠 → hidden evaluator（獨立 checkout，§9.1）critical gate 成立且
    全部 rubric/critical probe 綠（"hidden 全綠"）→ 以量測所得 wall_ms
    覆寫 `reference_timings.yaml`（deck CI 產物，僅供 evaluator 使用，永不
    進 run）。執行 candidate code 的唯一 seam 是 `IsolationRunner`
    （plan invariant 3）；namespace 能力不足一律 fail-closed 拒絕執行（§7）。
    """
    deck_dir = Path(deck_dir).resolve()
    if not deck_dir.is_dir():
        raise DeckValidationError(f"deck 目錄不存在：{deck_dir}")

    encounter_dirs = sorted(
        p for p in deck_dir.iterdir() if p.is_dir() and (p / "card.yaml").is_file()
    )
    if not encounter_dirs:
        raise DeckValidationError(
            f"deck 目錄沒有任何 encounter（card.yaml）：{deck_dir}"
        )

    toolchain = _toolchain_paths()
    _require_isolation(bwrap_path, toolchain)
    pytest_argv = _sandbox_pytest_argv()
    ruff_argv = _ruff_argv(toolchain)

    encounters = tuple(
        _validate_one_encounter(
            encounter_dir,
            bwrap_path=bwrap_path,
            toolchain=toolchain,
            pytest_argv=pytest_argv,
            ruff_argv=ruff_argv,
            write_timings=write_timings,
        )
        for encounter_dir in encounter_dirs
    )
    return DeckValidationReport(deck_dir=deck_dir, encounters=encounters)


def _validate_one_encounter(
    encounter_dir: Path,
    *,
    bwrap_path: str,
    toolchain: tuple[Path, ...],
    pytest_argv: tuple[str, ...],
    ruff_argv: tuple[str, ...],
    write_timings: bool,
) -> EncounterValidation:
    encounter_id = encounter_dir.name
    checks: list[str] = []
    try:
        card = load_card(encounter_dir / "card.yaml")
        checks.append("schema")

        _check_provenance_file(encounter_dir)
        checks.append("provenance")

        _check_probe_files_exist(card, encounter_dir)
        checks.append("probe_files_exist")

        reference_patch_path = encounter_dir / "hidden" / "reference.patch"
        if not reference_patch_path.is_file():
            raise DeckError(f"hidden/reference.patch 不存在：{reference_patch_path}")
        diff_text = reference_patch_path.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory(prefix="patchmud-validate-") as tmp:
            tmp_path = Path(tmp)
            frozen = materialize_repo(encounter_dir, tmp_path / "worktree")
            checks.append("materialize")

            workspace = Workspace(
                frozen=frozen,
                encounter_dir=encounter_dir,
                shadow_dir=tmp_path / "shadow",
            )
            runner = IsolationRunner(frozen.path, toolchain, bwrap_path=bwrap_path)
            suite = ProbeSuite.from_card(card, runner, pytest_argv=pytest_argv)

            baseline = suite.run(workspace)
            _check_baseline_outcomes(card, baseline)
            checks.append("baseline")

            applied = workspace.apply_patch(diff_text, kind="production")
            if applied.rejected:
                raise DeckError(f"reference.patch 無法套用：{applied.reason}")
            checks.append("reference_patch_applies")

            after = suite.run(workspace)
            _check_all_public_green(card, after)
            checks.append("public_probes_green_under_reference")

            final_diff = workspace.cumulative_diff()
            evaluation = evaluate_final(
                card,
                frozen,
                final_diff,
                lambda checkout: IsolationRunner(
                    checkout, toolchain, bwrap_path=bwrap_path
                ),
                encounter_dir=encounter_dir,
                pytest_argv=pytest_argv,
                ruff_argv=ruff_argv,
            )
            _check_hidden_green(evaluation)
            checks.append("hidden_probes_green_under_reference")

            if write_timings:
                _write_reference_timings(card, encounter_dir, evaluation)
                checks.append("reference_timings_measured")

        return EncounterValidation(encounter_id, True, tuple(checks))
    except (DeckError, WorkspaceError, EvaluatorError) as exc:
        return EncounterValidation(encounter_id, False, tuple(checks), str(exc))


_PROVENANCE_REQUIRED_FIELDS = (
    "schema_version",
    "issue_id",
    "archetype_source",
    "published_at",
    "variant_notes",
    "frozen_at",
)


def _check_provenance_file(encounter_dir: Path) -> None:
    path = encounter_dir / "provenance.yaml"
    if not path.is_file():
        raise DeckError(f"provenance.yaml 不存在：{path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DeckError(f"provenance.yaml 解析失敗：{exc}") from exc
    if not isinstance(data, dict):
        raise DeckError("provenance.yaml 頂層必須是 mapping")
    missing = [k for k in _PROVENANCE_REQUIRED_FIELDS if k not in data]
    if missing:
        raise DeckError(f"provenance.yaml 缺欄位：{', '.join(missing)}")
    # F5：frozen deck drift 偵測——provenance 若 pin 了 content_sha256，
    # 重算 encounter 內容 hash 並比對；改動 repo/src、hidden 測資或 card 而
    # 未同步更新 pin 一律 fail（避免 frozen deck 靜默漂移）。
    pinned = data.get("content_sha256")
    if pinned is not None:
        actual = encounter_content_sha256(encounter_dir)
        if actual != pinned:
            raise DeckError(
                f"frozen deck 漂移：{encounter_dir.name} 內容 hash 與 provenance "
                f"content_sha256 不符（pinned={pinned[:12]}… actual={actual[:12]}…）"
            )


#: content hash 排除項：快取產物、reference_timings（validate-deck 會覆寫）。
_CONTENT_HASH_EXCLUDE_NAMES = frozenset({"reference_timings.yaml"})


def encounter_content_sha256(encounter_dir: Path) -> str:
    """encounter 的內容指紋（F5）：card.yaml + repo/** + hidden/**（排除快取與
    reference_timings.yaml）的 sha256，綁死 frozen deck 的授權狀態。"""
    encounter_dir = Path(encounter_dir)
    targets: list[Path] = []
    card = encounter_dir / "card.yaml"
    if card.is_file():
        targets.append(card)
    for sub in ("repo", "hidden"):
        root = encounter_dir / sub
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.name in _CONTENT_HASH_EXCLUDE_NAMES:
                continue
            targets.append(path)
    digest = hashlib.sha256()
    for path in sorted(targets, key=lambda p: p.relative_to(encounter_dir).as_posix()):
        rel = path.relative_to(encounter_dir).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _check_probe_files_exist(card: IssueCard, encounter_dir: Path) -> None:
    missing: list[str] = []
    for req in card.public_requirements:
        if not _probe_target_exists(encounter_dir, req.probe):
            missing.append(req.probe)
    for cr in card.critical_requirements:
        if not _probe_target_exists(encounter_dir, cr.hidden_probe):
            missing.append(cr.hidden_probe)
    for rp in card.regression_probes:
        if rp.path is not None and not _probe_target_exists(encounter_dir, rp.path):
            missing.append(rp.path)
    for cp in card.compat_probes:
        if not _probe_target_exists(encounter_dir, cp.probe):
            missing.append(cp.probe)
    rubric = card.power_rubric
    for path in (
        tuple(g.probe for g in rubric.functional.groups)
        + rubric.robustness.probes
        + rubric.compatibility.probes
        + rubric.runtime_efficiency.probes
    ):
        if not _probe_target_exists(encounter_dir, path):
            missing.append(path)
    if missing:
        raise DeckError(f"probe 檔案不存在：{', '.join(sorted(set(missing)))}")


def _probe_target_exists(encounter_dir: Path, path: str) -> bool:
    target = (
        encounter_dir / path
        if path.startswith("hidden/")
        else encounter_dir / "repo" / path
    )
    return target.exists()


def _check_baseline_outcomes(card: IssueCard, baseline: ProbeResults) -> None:
    for req in card.public_requirements:
        status = baseline[req.probe].status if req.probe in baseline else None
        if status != "failed":
            raise DeckError(
                "baseline（未套 patch）MAIN probe 必須為 failed（issue 可重現）："
                f"{req.probe} 實際 {status!r}"
            )
    for rp in card.regression_probes:
        probe_id = rp.path if rp.path is not None else smoke_probe_id(rp.smoke)
        _require_green(baseline, probe_id, "baseline regression probe")
    for cp in card.compat_probes:
        _require_green(baseline, cp.probe, "baseline compat probe")


def _check_all_public_green(card: IssueCard, results: ProbeResults) -> None:
    for req in card.public_requirements:
        _require_green(results, req.probe, "reference patch 下 MAIN probe")
    for rp in card.regression_probes:
        probe_id = rp.path if rp.path is not None else smoke_probe_id(rp.smoke)
        _require_green(results, probe_id, "reference patch 下 regression probe")
    for cp in card.compat_probes:
        _require_green(results, cp.probe, "reference patch 下 compat probe")


def _require_green(results: ProbeResults, probe_id: str, label: str) -> None:
    if not _is_green(results, probe_id):
        status = results[probe_id].status if probe_id in results else "missing"
        raise DeckError(f"{label} 未綠：{probe_id}（{status}）")


def _check_hidden_green(evaluation: FinalEvaluation) -> None:
    if evaluation.gates.run_invalid:
        raise DeckError("hidden evaluator 判定 run_invalid（偵測到越界存取）")
    if not evaluation.gates.critical_pass:
        raise DeckError("reference patch 下 critical requirements 未全過")
    for probe_id, outcome in evaluation.probe_outcomes.items():
        if outcome.status != "passed":
            raise DeckError(
                f"reference patch 下 hidden/rubric probe 未綠：{probe_id}"
                f"（{outcome.status}）"
            )


def _write_reference_timings(
    card: IssueCard, encounter_dir: Path, evaluation: FinalEvaluation
) -> None:
    """`reference_timings.yaml`：deck CI 產物，僅供 evaluator 使用，永不進 run（§4.2）。"""
    probes = card.power_rubric.runtime_efficiency.probes
    if not probes:
        return
    timings = {
        probe_id: float(evaluation.probe_outcomes[probe_id].wall_ms)
        for probe_id in probes
        if probe_id in evaluation.probe_outcomes
    }
    path = encounter_dir / "hidden" / "reference_timings.yaml"
    path.write_text(
        yaml.safe_dump(
            {"schema_version": _TIMINGS_SCHEMA_VERSION, "timings_ms": timings},
            sort_keys=True,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# score-diff 子命令
# ---------------------------------------------------------------------------


def _cmd_score_diff(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud score-diff",
        description="離線評分：給定 encounter 與手工 diff，產出 result.yaml 與私有封存。",
    )
    parser.add_argument("--encounter", required=True, type=Path, help="encounter 目錄")
    parser.add_argument("--diff", required=True, type=Path, help="diff 檔（可為空檔 = baseline）")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"), help="run 目錄根")
    parser.add_argument("--run-id", default=None, help="run 識別字串（預設自動產生）")
    ns = parser.parse_args(argv)

    try:
        summary = score_diff(ns.encounter, ns.diff, ns.runs_root, run_id=ns.run_id)
    except (ScoreDiffError, DeckError, EvaluatorError, StoreError, WorkspaceError, LedgerError) as exc:
        print(f"score-diff 失敗：{exc}", file=sys.stderr)
        return 2

    print(f"run 目錄：{summary.run_dir}")
    print(f"私有封存：{summary.archive_path}")
    print(f"critical_pass={summary.critical_pass} clear={summary.clear} power_total={summary.power_total}")
    return 0


def score_diff(
    encounter_dir: Path,
    diff_path: Path,
    runs_root: Path,
    *,
    run_id: str | None = None,
    bwrap_path: str = DEFAULT_BWRAP_PATH,
) -> ScoreDiffSummary:
    """離線評分 walking skeleton（plan Task 8；spec §5.2、§9、§12.1）。

    流程：load card → materialize frozen repo → 套 diff（Workspace 嚴格模式）
    → 全部 public probes → hidden evaluator（獨立 checkout）→ Clear 唯一公式
    → result.yaml → archive_private。
    """
    encounter_dir = Path(encounter_dir).resolve()
    card = load_card(encounter_dir / "card.yaml")

    diff_path = Path(diff_path)
    if not diff_path.is_file():
        raise ScoreDiffError(f"diff 檔不存在：{diff_path}")
    diff_text = diff_path.read_text(encoding="utf-8")
    diff_sha256 = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    toolchain = _toolchain_paths()
    pytest_argv = _sandbox_pytest_argv()
    ruff_argv = _ruff_argv(toolchain)
    _require_isolation(bwrap_path, toolchain)

    if run_id is None:
        run_id = f"score-diff-{card.issue_id}-{time.strftime('%Y%m%d%H%M%S')}"

    runs_root = Path(runs_root)
    with tempfile.TemporaryDirectory(prefix="patchmud-score-") as tmp:
        tmp_path = Path(tmp)
        frozen = materialize_repo(encounter_dir, tmp_path / "worktree")

        store = RunStore.create(
            {
                "run_id": run_id,
                "frozen_sha": frozen.sha,
                "pricing_hash": _OFFLINE_NA,
                "harness_prompt_version": _OFFLINE_NA,
                "schedule_ref": _OFFLINE_NA,
                "encounter_dir": str(encounter_dir),
            },
            runs_root,
        )

        # 套用手工 diff：走 Workspace 嚴格模式（保護區 / harness 設定檔拒收）
        workspace = Workspace(
            frozen=frozen, encounter_dir=encounter_dir, shadow_dir=tmp_path / "shadow"
        )
        if diff_text.strip():
            applied = workspace.apply_patch(diff_text, kind="production")
            if applied.rejected:
                raise ScoreDiffError(f"diff 遭拒：{applied.reason}")

        # 終局語意（§5.2）：全部 public probes ＋ hidden evaluator 全套
        public_runner = IsolationRunner(frozen.path, toolchain, bwrap_path=bwrap_path)
        suite = ProbeSuite.from_card(card, public_runner, pytest_argv=pytest_argv)
        public_results = suite.run(workspace)

        evaluation = evaluate_final(
            card,
            frozen,
            diff_text,
            lambda checkout: IsolationRunner(checkout, toolchain, bwrap_path=bwrap_path),
            encounter_dir=encounter_dir,
            pytest_argv=pytest_argv,
            ruff_argv=ruff_argv,
        )

        main_public_green = all(
            _is_green(public_results, req.probe) for req in card.public_requirements
        )
        # 離線評分不經回合協定，終局必非 failed:protocol
        clear = compute_clear(
            critical_pass=evaluation.gates.critical_pass,
            main_public_green=main_public_green,
            protocol_failed=False,
        )
        economy, economy_reason = _economy_na(card)

        store.append_event(
            {
                "type": "score_diff",
                "diff_sha256": diff_sha256,
                "public_probes": {
                    probe_id: outcome.status
                    for probe_id, outcome in public_results.items()
                },
            }
        )
        store.write_result(
            _build_result(
                run_id=run_id,
                diff_sha256=diff_sha256,
                clear=clear,
                main_public_green=main_public_green,
                evaluation=evaluation,
                public_results=public_results,
                economy=economy,
                economy_reason=economy_reason,
            )
        )

        archive_path = runs_root / f"{run_id}-private.tar"
        store.archive_private(archive_path)

    return ScoreDiffSummary(
        run_dir=store.run_dir,
        archive_path=archive_path,
        clear=clear,
        critical_pass=evaluation.gates.critical_pass,
        power_total=evaluation.power.total,
    )


# ---------------------------------------------------------------------------
# run 子命令（Task 13，milestone B 收口）
# ---------------------------------------------------------------------------


def _cmd_run(argv: list[str]) -> int:
    if (not argv or "--model" not in argv) and sys.stdin.isatty():
        enc = _prompt_select_encounter()
        if enc:
            argv = [enc, "--model", "sonnet", "--live"]
    parser = argparse.ArgumentParser(
        prog="patchmud run",
        description="回合制對局：模型（或 scripted 劇本）打完一場 encounter。",
    )
    parser.add_argument(
        "encounter", help="關卡名字（如 input-validation-v1）或 encounter 目錄路徑"
    )
    parser.add_argument(
        "--model",
        required=True,
        help=(
            "模型：別名 sonnet/haiku/opus/fable（Anthropic）、"
            "spark/luna/terra/sol（codex CLI）、flash/pro（agy CLI），或完整 spec "
            "anthropic:<model> / codex:<model> / agy:<model> / "
            "openai:<model>[@url] / scripted:<file>"
        ),
    )
    parser.add_argument(
        "--loadout", default="P0T0R0", help="forced loadout（預設 P0T0R0 = SOLO）"
    )
    parser.add_argument("--runs-root", type=Path, default=Path("runs"), help="run 目錄根")
    parser.add_argument("--run-id", default=None, help="run 識別字串（預設自動產生）")
    parser.add_argument(
        "--live",
        action="store_true",
        help="即時旁觀：每回合一完成就印出 zh-TW 戰報（真模型對局時邊玩邊看）",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="live 每回合之間停頓秒數（給觀眾時間閱讀；預設 0）",
    )
    ns = parser.parse_args(argv)

    try:
        encounter_dir = resolve_encounter(ns.encounter)
        result = run_cli(
            encounter_dir,
            ns.model,
            ns.loadout,
            ns.runs_root,
            run_id=ns.run_id,
            live=ns.live,
            delay=ns.delay,
        )
    except (
        RunCliError,
        ScoreDiffError,
        AdapterError,
        DeckError,
        EvaluatorError,
        StoreError,
        WorkspaceError,
        LedgerError,
        ValueError,
    ) as exc:
        print(f"run 失敗：{exc}", file=sys.stderr)
        return 2

    if ns.live:
        # live 終局結算（Power / Economy / Control 摘要，與逐回合戰報同一視圖面）
        print(_render_live_settlement(result))
    print(
        f"end_reason={result.end_reason} clear={result.clear} "
        f"turns={result.turns_used} power_total={result.evaluation.power.total}"
    )
    return 0


# ---------------------------------------------------------------------------
# versus 子命令：多模型並排對戰視圖（各自隔離 run）
# ---------------------------------------------------------------------------


def _cmd_versus(argv: list[str]) -> int:
    if (not argv or "--models" not in argv) and sys.stdin.isatty():
        enc = _prompt_select_encounter()
        models = _prompt_select_models()
        if enc and models:
            argv = [enc, "--models", models]
    parser = argparse.ArgumentParser(
        prog="patchmud versus",
        description=(
            "並排對戰：多個模型各自打同一關（隔離 run），跑完同步並排呈現每回合"
            "白話戰報，收尾記分板與「誰贏在哪」判詞。隔離不可動搖——共用戰場會使"
            " benchmark 失效。"
        ),
    )
    parser.add_argument(
        "encounter", help="關卡名字（如 input-validation-v1）或 encounter 目錄路徑"
    )
    parser.add_argument(
        "--models",
        required=True,
        help="逗號分隔的模型（可用別名），如 sonnet,haiku,opus 或跨家 sonnet,sol,flash",
    )
    parser.add_argument(
        "--loadout", default="P0T0R0", help="forced loadout（預設 P0T0R0 = SOLO）"
    )
    parser.add_argument("--runs-root", type=Path, default=Path("runs"), help="run 目錄根")
    ns = parser.parse_args(argv)

    model_specs = [m.strip() for m in ns.models.split(",") if m.strip()]
    if len(model_specs) < 2:
        print("versus 需要至少 2 個模型（--models a,b[,c]）", file=sys.stderr)
        return 2

    try:
        encounter_dir = resolve_encounter(ns.encounter)
        briefing = narration.encounter_briefing(load_card(encounter_dir / "card.yaml"))

        def run_one(enc, spec, loadout, runs_root, run_id):
            return run_cli(enc, spec, loadout, runs_root, run_id=run_id)

        entries = run_versus(
            encounter_dir, model_specs, ns.loadout, ns.runs_root, run_one=run_one
        )
    except (
        RunCliError,
        ScoreDiffError,
        AdapterError,
        DeckError,
        EvaluatorError,
        StoreError,
        WorkspaceError,
        LedgerError,
        ValueError,
    ) as exc:
        print(f"versus 失敗：{exc}", file=sys.stderr)
        return 2

    print(render_versus(entries, encounter=encounter_dir.name, briefing=briefing))
    return 0


def _render_live_settlement(result) -> str:
    """live 收尾：終局結算摘要（純視圖，資料取自 RunResult）。"""
    ev = result.evaluation
    gates = ev.gates
    return zh.text(
        "run.live_settlement",
        clear_label=zh.text("run.clear_yes") if result.clear else zh.text("run.clear_no"),
        end_reason=result.end_reason,
        power=narration.format_power(ev.power.total),
        functional=ev.power.functional,
        critical_pass=zh.text("yes") if gates.critical_pass else zh.text("no"),
    )


def run_cli(
    encounter_dir: Path,
    model_spec: str,
    loadout_spec: str,
    runs_root: Path,
    *,
    run_id: str | None = None,
    bwrap_path: str = DEFAULT_BWRAP_PATH,
    live: bool = False,
    delay: float = 0.0,
):
    """`patchmud run` 串線：真佈線（IsolationRunner／ProbeSuite／evaluator）
    交給 `run_encounter`（spec §5、§6；plan Task 13）。

    ``live=True``：掛 `LiveSpectator`，每回合一完成即印 zh-TW 戰報（旁觀者
    邊玩邊看；純視圖、不影響評分資料流）。``delay`` 為回合間停頓秒數。
    """
    encounter_dir = Path(encounter_dir).resolve()
    card = load_card(encounter_dir / "card.yaml")
    loadout = Loadout.from_string(loadout_spec)
    adapter = _build_adapter(model_spec)

    spectator = LiveSpectator(delay=delay).feed if live else None

    if run_id is None:
        run_id = f"run-{card.issue_id}-{time.strftime('%Y%m%d%H%M%S')}"
    return _wire_and_run_encounter(
        card,
        encounter_dir,
        adapter,
        loadout,
        runs_root,
        run_id,
        bwrap_path=bwrap_path,
        # 封存展開後的完整 spec，不是使用者打的別名：別名表是會演進的間接層
        # （`opus` 曾指向 claude-opus-4-8，現指向 claude-opus-5），封存若只記
        # 別名，事後無從得知當時實際跑的是哪個模型，違反可重播的前提。
        record_extra={"model": normalize_model_spec(model_spec)},
        spectator=spectator,
    )


def play_cli(
    encounter_dir: Path,
    loadout_spec: str,
    runs_root: Path,
    *,
    run_id: str | None = None,
    bwrap_path: str = DEFAULT_BWRAP_PATH,
    input_fn=None,
    output_fn=print,
):
    """`patchmud play` 串線：`HumanAdapter`（stdin/stdout）取代模型 adapter，
    人類以同一命令協定親自打一場 encounter（spec §5.4；plan Task 22）。

    引擎、probe、queue、評分與 `run` 完全同構；差異只有：
    - ledger 全 token 欄位 NA、成本 NA（``usage_raw = {}``）；
    - run.yaml 與 result.yaml 標記 ``human: true``——human run 永不進
      ranked 資料與任何聚合指標（metrics 層 ``HumanRunExcluded``）。
    ``input_fn`` / ``output_fn`` 可注入（測試用 scripted input_fn）；預設
    stdin 多行讀取（空行結束一則回覆；Ctrl-D 視同 COMMIT 收尾）。
    """
    encounter_dir = Path(encounter_dir).resolve()
    card = load_card(encounter_dir / "card.yaml")
    loadout = Loadout.from_string(loadout_spec)
    adapter = HumanAdapter(
        input_fn=input_fn if input_fn is not None else _stdin_reply,
        output_fn=output_fn,
    )

    if run_id is None:
        run_id = f"play-{card.issue_id}-{time.strftime('%Y%m%d%H%M%S')}"
    output_fn(zh.text("play.banner"))
    return _wire_and_run_encounter(
        card,
        encounter_dir,
        adapter,
        loadout,
        runs_root,
        run_id,
        bwrap_path=bwrap_path,
        record_extra={"model": "human", "human": True},
        human=True,
    )


def _wire_and_run_encounter(
    card: IssueCard,
    encounter_dir: Path,
    adapter: ModelAdapter,
    loadout,
    runs_root: Path,
    run_id: str,
    *,
    bwrap_path: str,
    record_extra: dict,
    human: bool = False,
    spectator=None,
):
    """run／play 共用真佈線：materialize → store → workspace →
    IsolationRunner/ProbeSuite/evaluator → `run_encounter`。"""
    toolchain = _toolchain_paths()
    pytest_argv = _sandbox_pytest_argv()
    ruff_argv = _ruff_argv(toolchain)
    _require_isolation(bwrap_path, toolchain)

    runs_root = Path(runs_root)
    with tempfile.TemporaryDirectory(prefix="patchmud-run-") as tmp:
        tmp_path = Path(tmp)
        frozen = materialize_repo(encounter_dir, tmp_path / "worktree")

        store = RunStore.create(
            {
                "run_id": run_id,
                "frozen_sha": frozen.sha,
                "pricing_hash": _OFFLINE_NA,
                "harness_prompt_version": HARNESS_PROMPT_VERSION,
                "schedule_ref": _OFFLINE_NA,
                "encounter_dir": str(encounter_dir),
                "loadout": loadout.name,
                **record_extra,
            },
            runs_root,
        )

        # checkpoints 落在 run 目錄（spec §3：shadow bare repo 供離線重播）
        workspace = Workspace(
            frozen=frozen,
            encounter_dir=encounter_dir,
            shadow_dir=store.run_dir / "checkpoints",
        )
        runner = IsolationRunner(frozen.path, toolchain, bwrap_path=bwrap_path)
        config = RunConfig(
            workspace=workspace,
            probe_suite=ProbeSuite.from_card(card, runner, pytest_argv=pytest_argv),
            evaluate=lambda final_diff: evaluate_final(
                card,
                frozen,
                final_diff,
                lambda checkout: IsolationRunner(
                    checkout, toolchain, bwrap_path=bwrap_path
                ),
                encounter_dir=encounter_dir,
                pytest_argv=pytest_argv,
                ruff_argv=ruff_argv,
            ),
            run_agent_tests=build_agent_test_runner(runner, pytest_argv=pytest_argv),
            spectator=spectator,
        )
        return run_encounter(card, adapter, loadout, config, store, human=human)


def _stdin_reply() -> str:
    """人類回覆讀取器：讀 stdin 多行直到空行（一則回覆可含 PATCH diff）。

    起頭空行忽略；已有內容後的**真空行**（``""``）結束本則回覆——只含
    空白的行是 unified diff 的合法內容（空 context 行為單一空格），必須
    原樣保留；EOF（Ctrl-D）於無內容時上拋（`HumanAdapter` 視同 COMMIT
    收尾）、有內容時視同回覆結束。
    """
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            if not lines:
                raise
            break
        if line == "":
            if lines:
                break
            continue
        lines.append(line)
    return "\n".join(lines)


def _cmd_play(argv: list[str]) -> int:
    if not argv and sys.stdin.isatty():
        enc = _prompt_select_encounter()
        if enc:
            argv = [enc]
    parser = argparse.ArgumentParser(
        prog="patchmud play",
        description=(
            "人類親自對局：HumanAdapter（stdin/stdout）取代模型 adapter，"
            "以同一命令協定打完一場 encounter（spec §5.4）。"
            "回覆以空行結束；Ctrl-D 視同 COMMIT。run 標記 human: true，"
            "永不進 ranked 資料。"
        ),
    )
    parser.add_argument(
        "encounter", help="關卡名字（如 input-validation-v1）或 encounter 目錄路徑"
    )
    parser.add_argument(
        "--loadout", default="P0T0R0", help="forced loadout（預設 P0T0R0 = SOLO）"
    )
    parser.add_argument("--runs-root", type=Path, default=Path("runs"), help="run 目錄根")
    parser.add_argument("--run-id", default=None, help="run 識別字串（預設自動產生）")
    ns = parser.parse_args(argv)

    try:
        encounter_dir = resolve_encounter(ns.encounter)
        result = play_cli(encounter_dir, ns.loadout, ns.runs_root, run_id=ns.run_id)
    except (
        RunCliError,
        ScoreDiffError,
        AdapterError,
        DeckError,
        EvaluatorError,
        StoreError,
        WorkspaceError,
        LedgerError,
        ValueError,
    ) as exc:
        print(f"play 失敗：{exc}", file=sys.stderr)
        return 2

    print(
        f"end_reason={result.end_reason} clear={result.clear} "
        f"turns={result.turns_used} power_total={result.evaluation.power.total}"
    )
    return 0


_SCRIPT_DELIMITER = "-----"


#: 模型別名 → 完整 model spec（指令只打 sonnet / sol / flash 這類短名）。
#:
#: 三家 provider 的短別名共用同一個命名空間，彼此不得重複。codex 與 agy 走
#: 各自 CLI 的 OAuth 登入態，與 Anthropic 憑證無關（issue #14）。
_MODEL_ALIASES = {
    # Anthropic（API key 或 OAuth bearer；缺憑證時 fallback 到 claude CLI）
    "sonnet": "anthropic:claude-sonnet-5",
    "haiku": "anthropic:claude-haiku-4-5",
    "opus": "anthropic:claude-opus-5",
    "fable": "anthropic:claude-fable-5",
    # OpenAI（codex CLI headless，~/.codex/auth.json）
    "spark": "codex:gpt-5.3-codex-spark",
    "luna": "codex:gpt-5.6-luna",
    "terra": "codex:gpt-5.6-terra",
    "sol": "codex:gpt-5.6-sol",
    # Google Gemini（agy CLI headless，~/.antigravitycli）
    "flash": "agy:gemini-3.6-flash",
    "pro": "agy:gemini-3.1-pro",
}

#: CLI-based adapter 的 effort 一律固定 high（issue #14）：ranked run 之間的
#: 推理預算必須可比，不隨使用者的 CLI 設定漂移。
CLI_EFFORT = "high"


def has_claude_cli() -> bool:
    """檢查系統是否有安裝並可執行的 `claude` CLI。"""
    return shutil.which("claude") is not None


def has_codex_cli() -> bool:
    """檢查系統是否有安裝並可執行的 `codex` CLI。"""
    return shutil.which("codex") is not None


def has_agy_cli() -> bool:
    """檢查系統是否有安裝並可執行的 `agy` CLI。"""
    return shutil.which("agy") is not None


def normalize_model_spec(spec: str) -> str:
    """把別名展開成完整 model spec：`sonnet` → `anthropic:claude-sonnet-5`；
    （若無 Anthropic API Key 且有系統 claude CLI，無縫自動轉換成 `claude:claude-sonnet-5`）；
    `anthropic:sonnet` → `anthropic:claude-sonnet-5`。其餘原樣。

    `codex:` / `agy:` 別名自帶 CLI 登入態，不受 Anthropic 憑證狀態影響，
    也不得被 claude CLI fallback 劫持。"""
    if spec == "claude" or spec.startswith("claude:"):
        return spec
    if spec in _MODEL_ALIASES:
        return _fallback_to_claude_cli(_MODEL_ALIASES[spec])
    kind, sep, rest = spec.partition(":")
    if kind == "anthropic" and sep and rest in _MODEL_ALIASES:
        return _fallback_to_claude_cli(_MODEL_ALIASES[rest])
    return spec


def _fallback_to_claude_cli(target: str) -> str:
    """anthropic spec 在缺憑證且有 claude CLI 時改走 CLI；其餘 provider 原樣。"""
    kind, _, model_id = target.partition(":")
    if kind != "anthropic":
        return target
    if not has_anthropic_credentials() and has_claude_cli():
        return f"claude:{model_id}"
    return target


def has_anthropic_credentials() -> bool:
    """檢查環境變數是否有 ANTHROPIC_API_KEY 或 ANTHROPIC_AUTH_TOKEN。"""
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _build_adapter(spec: str) -> ModelAdapter:
    """model spec → adapter；HTTP adapter 的憑證一律取自 env（不進 CLI）。"""
    spec = normalize_model_spec(spec)
    kind, _, rest = spec.partition(":")
    if kind == "scripted":
        script = Path(rest)
        if not rest or not script.is_file():
            raise RunCliError(f"scripted 劇本檔不存在：{rest!r}")
        replies = _split_script(script.read_text(encoding="utf-8"))
        if not replies:
            raise RunCliError(f"scripted 劇本檔沒有任何回覆：{script}")
        return ScriptedAdapter(replies)
    if kind == "claude":
        model_id = rest if rest else "claude-sonnet-5"
        if not has_claude_cli():
            raise RunCliError("系統未安裝 `claude` CLI（找不到 `claude` 可執行檔）")
        return ClaudeCliAdapter(model=model_id)
    if kind == "codex":
        if not rest:
            raise RunCliError("codex spec 缺 model id：codex:<model>")
        if not has_codex_cli():
            raise RunCliError(
                "系統未安裝 `codex` CLI（找不到 `codex` 可執行檔）。\n"
                "  安裝後以 `codex login` 建立 OAuth 登入態即可，不需 OPENAI_API_KEY。"
            )
        return CodexCliAdapter(model=rest, effort=CLI_EFFORT)
    if kind == "agy":
        if not rest:
            raise RunCliError("agy spec 缺 model id：agy:<model>")
        if not has_agy_cli():
            raise RunCliError(
                "系統未安裝 `agy` CLI（找不到 `agy` 可執行檔）。\n"
                "  安裝並登入後即可使用，不需 API key。"
            )
        return AgyCliAdapter(model=rest, effort=CLI_EFFORT)
    if kind == "anthropic":
        if not rest:
            raise RunCliError("anthropic spec 缺 model id：anthropic:<model>")
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        if not has_anthropic_credentials():
            if has_claude_cli():
                return ClaudeCliAdapter(model=rest)
            raise RunCliError(
                "未設定 Anthropic 憑證：\n"
                "  1. 設 ANTHROPIC_API_KEY，或用 OAuth——`ant auth login` 後 `set -a; eval \"$(ant auth print-credentials --env)\"; set +a`\n"
                "  2. 改用其他家的 CLI 登入態（同樣不需 API key）：\n"
                "     patchmud versus <關卡> --models sol,flash   # codex / agy 別名\n"
                "  3. 若無任何雲端登入，可用免 Key 地端模型（如 Ollama）：\n"
                "     patchmud versus <關卡> --models openai:llama3@http://localhost:11434/v1,openai:qwen2.5@http://localhost:11434/v1\n"
                "  4. 或用離線劇本模式：\n"
                "     patchmud versus <關卡> --models scripted:script1.txt,scripted:script2.txt"
            )
        return AnthropicAdapter(rest, api_key, auth_token=auth_token)
    if kind == "openai":
        if not rest:
            raise RunCliError("openai spec 缺 model id：openai:<model>[@<base_url>]")
        model, _, base_url = rest.partition("@")
        kwargs: dict = {}
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAICompatAdapter(
            model, os.environ.get("OPENAI_API_KEY", ""), **kwargs
        )
    raise RunCliError(f"未知 model spec：{spec!r}")


def _split_script(text: str) -> list[str]:
    """scripted 劇本：回覆以獨立一行 `-----` 分隔（回覆內容逐 byte 保留）。"""
    replies: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == _SCRIPT_DELIMITER:
            replies.append("\n".join(current))
            current = []
        else:
            current.append(line)
    replies.append("\n".join(current))
    return [reply for reply in replies if reply.strip()]


# ---------------------------------------------------------------------------
# watch 子命令（Task 23，spec §5.4）
# ---------------------------------------------------------------------------


def _cmd_watch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud watch",
        description=(
            "離線觀戰：把封存 events.jsonl 重放成逐回合 zh-TW 戰報"
            "（只讀封存資料，不重新執行任何 probe 或模型呼叫，spec §5.4）。"
        ),
    )
    parser.add_argument("run_dir", type=Path, help="run 目錄（runs/<run_id>）")
    parser.add_argument(
        "--turn", type=int, default=None, help="只輸出第 N 回合的戰報段落"
    )
    ns = parser.parse_args(argv)

    try:
        store = RunStore.open(ns.run_dir)  # fail-closed：run.yaml schema、event seq
        events = store.load_events()
        if ns.turn is not None:
            print(render_turn(events, ns.turn))
        else:
            print(render_battle_report(events, _load_watch_result(ns.run_dir)))
    except (WatchError, StoreError) as exc:
        print(f"watch 失敗：{exc}", file=sys.stderr)
        return 2
    return 0


def _load_watch_result(run_dir: Path) -> dict:
    """result.yaml 讀取（只讀；schema fail-closed，與 report 載入同一契約）。"""
    result_path = Path(run_dir) / "result.yaml"
    if not result_path.is_file():
        raise StoreError(f"run 未完成（缺 result.yaml），無法出全場戰報：{run_dir}")
    result = yaml.safe_load(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise StoreError(f"result.yaml 內容必須是 mapping：{result_path}")
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise StoreError(
            f"result.yaml schema_version 不符：{result.get('schema_version')!r}"
        )
    return result


# ---------------------------------------------------------------------------
# replay 子命令（Task 16，spec §12.2）
# ---------------------------------------------------------------------------


def _cmd_replay(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud replay",
        description=(
            "兩級 replay：預設 L1 位元一致重算（不執行 probe）；"
            "--l2 於 pinned 環境重執行全部 probes 後走 L1（spec §12.2）。"
        ),
    )
    parser.add_argument("run_dir", type=Path, help="run 目錄（runs/<run_id>）")
    parser.add_argument(
        "--l2",
        action="store_true",
        help="L2：pinned 環境重執行全部 probes（functional/compat/robustness 必須相等，perf 容忍帶）",
    )
    ns = parser.parse_args(argv)

    try:
        if ns.l2:
            report = replay_l2(ns.run_dir, _pinned_reexecute)
        else:
            report = replay_l1(ns.run_dir)
    except (
        ReplayError,
        ScoreDiffError,
        DeckError,
        EvaluatorError,
        StoreError,
        WorkspaceError,
        FloodError,
        LedgerError,
    ) as exc:
        print(f"replay 失敗：{exc}", file=sys.stderr)
        return 2

    print(
        f"replay {report.level}: identical={report.identical} "
        f"diffs={len(report.diffs)} perf_deviations={len(report.perf_deviations)}"
    )
    for diff in report.diffs:
        print(
            f"  diff {diff.field}: archived={diff.archived!r} "
            f"recomputed={diff.recomputed!r}"
        )
    for dev in report.perf_deviations:
        print(
            f"  perf {dev.section}/{dev.probe_id}: "
            f"status {dev.archived_status}->{dev.reexecuted_status} "
            f"wall_ms {dev.archived_wall_ms}->{dev.reexecuted_wall_ms}"
        )
    # L1 輸出與封存 result.yaml 不一致 → exit non-zero（spec §12.2）
    return 0 if report.identical else 1


def _pinned_reexecute(
    run_dir: Path, *, bwrap_path: str = DEFAULT_BWRAP_PATH
) -> ReexecutedProbes:
    """L2 真佈線：pinned 環境重執行全部 probes（spec §12.2）。

    deck 重物化的 frozen SHA 必須與 run.yaml 封存完全一致（deck 漂移 →
    拒絕）；final worktree 自 checkpoints shadow bare repo 的最末 checkpoint
    重建（`git fetch` 進 frozen clone 後 `git diff <frozen>..<checkpoint>`），
    diff 於原始 run 已逐回合過 workspace 路徑規則，此處不重新裁決、直接
    `git apply`；public suite 與 hidden evaluator 全部經 IsolationRunner
    重新執行。
    """
    run_dir = Path(run_dir)
    store = RunStore.open(run_dir)
    events = store.load_events()
    record = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
    encounter_dir = Path(record["encounter_dir"])
    card = load_card(encounter_dir / "card.yaml")

    checkpoints = [
        event["checkpoint"]
        for event in events
        if isinstance(event.get("checkpoint"), str) and event["checkpoint"]
    ]
    if not checkpoints:
        raise ReplayError("events 無 checkpoint，無法重建 final worktree（L2）")
    checkpoints_dir = run_dir / "checkpoints"
    if not checkpoints_dir.is_dir():
        raise ReplayError(
            f"run 目錄缺 checkpoints/ shadow repo，無法 L2 重執行：{run_dir}"
        )

    toolchain = _toolchain_paths()
    pytest_argv = _sandbox_pytest_argv()
    ruff_argv = _ruff_argv(toolchain)
    _require_isolation(bwrap_path, toolchain)

    with tempfile.TemporaryDirectory(prefix="patchmud-replay-") as tmp:
        tmp_path = Path(tmp)
        frozen = materialize_repo(encounter_dir, tmp_path / "worktree")
        if frozen.sha != record.get("frozen_sha"):
            raise ReplayError(
                "deck 重物化 SHA 與封存不符（deck 已漂移，pinned 重執行不成立）："
                f"{frozen.sha} != {record.get('frozen_sha')!r}"
            )
        final_diff = _checkpoint_diff(checkpoints_dir, frozen, checkpoints[-1])

        workspace = Workspace(
            frozen=frozen, encounter_dir=encounter_dir, shadow_dir=tmp_path / "shadow"
        )
        if final_diff.strip():
            _replay_apply(final_diff, workspace.worktree)

        runner = IsolationRunner(frozen.path, toolchain, bwrap_path=bwrap_path)
        suite = ProbeSuite.from_card(card, runner, pytest_argv=pytest_argv)
        public = suite.run(workspace)
        evaluation = evaluate_final(
            card,
            frozen,
            final_diff,
            lambda checkout: IsolationRunner(checkout, toolchain, bwrap_path=bwrap_path),
            encounter_dir=encounter_dir,
            pytest_argv=pytest_argv,
            ruff_argv=ruff_argv,
        )
        return ReexecutedProbes(
            public={probe_id: public[probe_id] for probe_id in public},
            evaluator={
                probe_id: evaluation.probe_outcomes[probe_id]
                for probe_id in evaluation.probe_outcomes
            },
        )


def _replay_git_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
    }


def _checkpoint_diff(checkpoints_dir: Path, frozen, checkpoint_sha: str) -> str:
    """checkpoint shadow repo → final diff（fetch 進 frozen clone 後 diff）。"""
    fetch = subprocess.run(
        [
            "git",
            "-C",
            str(frozen.path),
            "fetch",
            "--quiet",
            str(checkpoints_dir),
            "refs/heads/checkpoints",
        ],
        env=_replay_git_env(),
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        raise ReplayError(f"checkpoints fetch 失敗：{fetch.stderr.strip()[:200]}")
    diff = subprocess.run(
        ["git", "-C", str(frozen.path), "diff", frozen.sha, checkpoint_sha],
        env=_replay_git_env(),
        capture_output=True,
        text=True,
    )
    if diff.returncode != 0:
        raise ReplayError(f"checkpoint diff 失敗：{diff.stderr.strip()[:200]}")
    return diff.stdout


def _replay_apply(diff: str, worktree: Path) -> None:
    res = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=str(worktree),
        input=diff,
        env=_replay_git_env(),
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise ReplayError(f"final diff 重建後無法套用：{res.stderr.strip()[:200]}")


# ---------------------------------------------------------------------------
# report 子命令（Task 17，milestone C 收口；spec §10.3、§13、報告 §11.2）
# ---------------------------------------------------------------------------

REPORT_SCHEMA_VERSION = 1

#: pre-registered EuTB 預算檔的預設位置（§10.4；Task 19 calibrate 產出）。
DEFAULT_REGISTERED_DIR = Path("analysis/registered")
_EUTB_BUDGET_FILE = "eutb_budget.yaml"

#: 有列資料的榜各出一份 CSV；skipped 榜不出假 CSV。
_BOARD_ORDER = (
    "clear_rate",
    "cost_per_clear",
    "tokens_per_clear",
    "qaty",
    "eutb",
    "power",
    "control",
    "ftr",
)


class _SkipRun(Exception):
    """單場 run 不進 ranked 榜（列入 runs_skipped，不中止整份 report）。"""


@dataclass(frozen=True)
class _ReportRun:
    """單場 run 的 report 聚合視圖（只讀封存；invariant 4）。"""

    run_id: str
    model: str
    loadout: str
    clear: int
    power_total: float
    cost: Decimal | None
    cost_reason: str | None
    work_tokens: int | None
    observable_tokens: int
    control: float
    ftr: float
    tau_uncalibrated: bool
    flood_create_tokens: int
    flood_repair_tokens: int


def _cmd_report(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud report",
        description=(
            "多榜研究 report（報告 §11.2）：只讀多場 run 的落盤封存，"
            "輸出 YAML/CSV 到 --out 目錄。"
        ),
    )
    parser.add_argument("--runs", required=True, help="run 目錄 glob，如 'runs/*'")
    parser.add_argument("--out", type=Path, default=Path("report"), help="輸出目錄")
    parser.add_argument(
        "--pricing",
        type=Path,
        default=None,
        help="pricing snapshot 檔；只對 run.yaml pin 了相同 content hash 的 run 計價",
    )
    parser.add_argument(
        "--registered",
        type=Path,
        default=DEFAULT_REGISTERED_DIR,
        help="pre-registered 參數目錄（EuTB 預算檔 eutb_budget.yaml，§10.4）",
    )
    ns = parser.parse_args(argv)

    try:
        report = build_report(
            ns.runs, ns.out, pricing_path=ns.pricing, registered_dir=ns.registered
        )
    except (
        ReportError,
        DeckError,
        StoreError,
        ReplayError,
        FloodError,
        EconomyError,
        EfficiencyError,
        LedgerError,
    ) as exc:
        print(f"report 失敗：{exc}", file=sys.stderr)
        return 2

    print(f"report 輸出：{ns.out / 'report.yaml'}")
    print(
        f"runs_included={report['runs_included']} "
        f"runs_skipped={len(report['runs_skipped'])}"
    )
    return 0


def build_report(
    runs_glob: str,
    out_dir: Path,
    *,
    pricing_path: Path | None = None,
    registered_dir: Path = DEFAULT_REGISTERED_DIR,
) -> dict:
    """多場 run 封存 → 多榜 report（YAML + 每榜一份 CSV）。

    只讀 run 目錄封存與 deck card、絕不寫回 run 目錄（invariant 4）；
    聚合鍵為 (model, loadout)。回傳 report dict（同步落盤 report.yaml）。
    """
    snapshot = None
    if pricing_path is not None:
        snapshot = PricingSnapshot.load(pricing_path)

    runs: list[_ReportRun] = []
    skipped: list[dict] = []
    matches = sorted(globmod.glob(str(runs_glob)))
    if not matches:
        raise ReportError(f"--runs glob 無任何匹配：{runs_glob!r}")
    for match in matches:
        run_dir = Path(match)
        if not (run_dir / "run.yaml").is_file():
            # runs root 可能混有封存 tar 等非 run 目錄項目
            skipped.append({"run_id": run_dir.name, "reason": "非 run 目錄（缺 run.yaml）"})
            continue
        try:
            runs.append(_load_report_run(run_dir, snapshot))
        except _SkipRun as exc:
            skipped.append({"run_id": run_dir.name, "reason": str(exc)})
    if not runs:
        raise ReportError(f"glob 匹配 {len(matches)} 項但無任何可聚合 run：{runs_glob!r}")

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "runs_included": len(runs),
        "runs_skipped": skipped,
        "runs": [_run_row(run) for run in runs],
        "leaderboards": _build_leaderboards(runs, registered_dir),
    }

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.yaml").write_text(
        yaml.safe_dump(report, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    for name in _BOARD_ORDER:
        board = report["leaderboards"][name]
        rows = board.get("rows")
        if rows:
            _write_csv(out_dir / f"{name}.csv", rows)
    return report


# ---- run 封存載入（fail-closed） -------------------------------------------


def _load_report_run(run_dir: Path, snapshot: PricingSnapshot | None) -> _ReportRun:
    store = RunStore.open(run_dir)  # fail-closed：run.yaml schema、event seq
    record = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))

    result_path = run_dir / "result.yaml"
    if not result_path.is_file():
        raise _SkipRun("run 未完成（缺 result.yaml）")
    result = yaml.safe_load(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ReportError(f"result.yaml 內容必須是 mapping：{run_dir.name}")
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ReportError(
            f"result.yaml schema_version 不符（{run_dir.name}）："
            f"{result.get('schema_version')!r}"
        )
    if result.get("mode") != "run":
        raise _SkipRun(f"mode 非 run（{result.get('mode')!r}），不進 ranked 榜")
    if result.get("human") is True:
        raise _SkipRun("human run 不進 ranked 榜（spec §5.4）")

    clear = result.get("clear")
    if clear not in (0, 1):
        raise ReportError(f"result.yaml clear 非 0/1（{run_dir.name}）：{clear!r}")
    power = result.get("power")
    if not isinstance(power, dict) or not isinstance(
        power.get("total"), (int, float)
    ):
        raise ReportError(f"result.yaml 缺 power.total（{run_dir.name}）")

    entries = load_ledger(run_dir)
    if not entries:
        raise ReportError(f"ledger.jsonl 無任何 entry（{run_dir.name}）")
    cost, cost_reason = _run_cost(record, entries, snapshot)

    card = load_card(Path(str(record["encounter_dir"])) / "card.yaml")
    flood = flood_metrics(store.load_events(), card, load_flood_coeffs())

    return _ReportRun(
        run_id=str(record["run_id"]),
        model=str(record.get("model", _OFFLINE_NA)),
        loadout=str(result.get("loadout", record.get("loadout", _OFFLINE_NA))),
        clear=int(clear),
        power_total=float(power["total"]),
        cost=cost,
        cost_reason=cost_reason,
        work_tokens=_work_tokens_of(result, run_dir),
        observable_tokens=_observable_tokens(entries, run_dir),
        control=flood.control,
        ftr=flood.ftr,
        tau_uncalibrated=flood.tau_uncalibrated,
        flood_create_tokens=flood.flood_create_tokens,
        flood_repair_tokens=flood.flood_repair_tokens,
    )


def _run_cost(
    record: dict, entries: list[LedgerEntry], snapshot: PricingSnapshot | None
) -> tuple[Decimal | None, str | None]:
    """C_run 只以 run.yaml pin 的 snapshot 計價（§10.2）；不可得 → NA＋理由。"""
    pricing_hash = record.get("pricing_hash")
    if pricing_hash == _OFFLINE_NA:
        return None, "run 未 pin pricing snapshot（pricing_hash=NA）"
    if snapshot is None:
        return None, "未提供 --pricing snapshot，無法對 pin 的 hash 計價"
    if snapshot.content_hash != pricing_hash:
        return None, (
            "--pricing snapshot content hash 與 run.yaml pin 不符"
            f"（{snapshot.content_hash[:12]}… != {str(pricing_hash)[:12]}…）"
        )
    return compute_run_cost(entries, snapshot).total, None


def _work_tokens_of(result: dict, run_dir: Path) -> int | None:
    ledger = result.get("ledger")
    if not isinstance(ledger, dict) or "work_tokens" not in ledger:
        raise ReportError(f"result.yaml 缺 ledger.work_tokens（{run_dir.name}）")
    work = ledger["work_tokens"]
    if work == _OFFLINE_NA:
        return None
    if isinstance(work, bool) or not isinstance(work, int):
        raise ReportError(
            f"result.yaml ledger.work_tokens 非整數或 NA（{run_dir.name}）：{work!r}"
        )
    return work


def _observable_tokens(entries: list[LedgerEntry], run_dir: Path) -> int:
    """common-observable = input + output_visible（§10.1；永遠可得）。"""
    total = 0
    for entry in entries:
        if entry.output_visible is None:
            raise ReportError(
                f"ledger entry 缺 output_visible，observable 欄無法計算"
                f"（{run_dir.name} turn={entry.turn}）"
            )
        total += entry.billed_input_total + entry.output_visible
    return total


# ---- 榜組裝（純資料轉換） ---------------------------------------------------


def _build_leaderboards(runs: list[_ReportRun], registered_dir: Path) -> dict:
    groups: dict[tuple[str, str], list[_ReportRun]] = {}
    for run in runs:
        groups.setdefault((run.model, run.loadout), []).append(run)
    samples = {key: [_sample_of(run) for run in members] for key, members in groups.items()}

    boards = {
        "clear_rate": _clear_rate_board(groups),
        "cost_per_clear": _cost_board(groups, samples),
        "tokens_per_clear": _efficiency_board(samples, tokens_per_clear),
        "qaty": _efficiency_board(samples, qaty),
        "eutb": _eutb_board(samples, registered_dir),
        "power": _mean_board(groups, lambda run: run.power_total, reverse=True),
        "control": _control_board(groups),
        "ftr": _ftr_board(groups),
    }
    return boards


def _sample_of(run: _ReportRun) -> RunSample:
    return RunSample(
        clear=run.clear,
        power=run.power_total,
        cost=run.cost,
        work_tokens=run.work_tokens,
        observable_tokens=run.observable_tokens,
    )


def _group_fields(key: tuple[str, str]) -> dict:
    return {"model": key[0], "loadout": key[1]}


def _clear_rate_board(groups: dict[tuple[str, str], list[_ReportRun]]) -> dict:
    rows = []
    for key, members in groups.items():
        clears = sum(run.clear for run in members)
        rows.append(
            {
                **_group_fields(key),
                "runs": len(members),
                "clears": clears,
                "value": clears / len(members),
            }
        )
    rows.sort(key=lambda row: (-row["value"], row["model"], row["loadout"]))
    return {"status": "ok", "rows": rows}


def _cost_board(
    groups: dict[tuple[str, str], list[_ReportRun]],
    samples: dict[tuple[str, str], list[RunSample]],
) -> dict:
    """CostPerClear 榜：cost 缺漏（未 pin／snapshot 不可得）→ NA，不假 0。"""
    ranked_rows: list[tuple[Decimal, dict]] = []
    na_rows: list[dict] = []
    for key, members in groups.items():
        missing = [run for run in members if run.cost is None]
        if missing:
            na_rows.append(
                {
                    **_group_fields(key),
                    "value": _OFFLINE_NA,
                    "ranked": False,
                    "reason": missing[0].cost_reason or "run 缺 C_run",
                }
            )
            continue
        value = cost_per_clear(samples[key])
        ranked_rows.append(
            (
                value,
                {
                    **_group_fields(key),
                    "value": "inf" if not value.is_finite() else str(value),
                    "ranked": True,
                    "reason": "",
                },
            )
        )
    ranked_rows.sort(key=lambda item: (item[0], item[1]["model"], item[1]["loadout"]))
    na_rows.sort(key=lambda row: (row["model"], row["loadout"]))
    return {"status": "ok", "rows": [row for _, row in ranked_rows] + na_rows}


def _efficiency_board(
    samples: dict[tuple[str, str], list[RunSample]], metric_fn
) -> dict:
    results = {key: metric_fn(samples[key]) for key in samples}
    return _efficiency_rows(results)


def _eutb_board(
    samples: dict[tuple[str, str], list[RunSample]], registered_dir: Path
) -> dict:
    """EuTB 榜：registered 預算檔缺失 → skipped 而非假值（§19.9 fail-closed）。"""
    try:
        budget = load_eutb_budget(Path(registered_dir) / _EUTB_BUDGET_FILE)
    except NotRegisteredError as exc:
        return {"status": "skipped", "reason": str(exc)}
    results = {key: eutb(samples[key], budget) for key in samples}
    return _efficiency_rows(results)


def _efficiency_rows(results: dict[tuple[str, str], EfficiencyResult]) -> dict:
    """EfficiencyResult → 榜列（雙欄＋cohort，F17／§13）。

    排名一律委派 metrics 層 ``rank_efficiency``（方向由指標 pin，caller
    不得自選；跨 cohort 排名在該層被拒）。跨 cohort 時整榜退為
    ``non_ranking``：rows **只發布**以 input + output_visible 一致計算的
    common-observable 描述性欄位——cohort 依賴的 ``value`` 欄（full
    群組為 T^work 基礎值）一概不出——列序退為群組名稱字典序。兩分支
    的 rows 皆逐列帶 ``non_ranking`` 標註（non-ranking 另帶 ``note``），
    CSV 由 rows 直出，檔案層即可與排名榜區分（§13）。
    """
    try:
        ordered = rank_efficiency(results)
    except CohortMismatchError as exc:
        note = str(exc)
        rows = [
            {
                **_group_fields(key),
                "observable": _num(results[key].observable),
                "disclosure_cohort": results[key].disclosure_cohort,
                "non_ranking": True,
                "note": note,
            }
            for key in sorted(results)
        ]
        return {"status": "ok", "non_ranking": True, "note": note, "rows": rows}
    rows = [
        {
            **_group_fields(key),
            "value": _num(results[key].value),
            "observable": _num(results[key].observable),
            "disclosure_cohort": results[key].disclosure_cohort,
            "non_ranking": False,
        }
        for key in ordered
    ]
    return {"status": "ok", "non_ranking": False, "rows": rows}


def _mean_board(
    groups: dict[tuple[str, str], list[_ReportRun]], value_of, *, reverse: bool
) -> dict:
    rows = []
    for key, members in groups.items():
        rows.append(
            {
                **_group_fields(key),
                "runs": len(members),
                "value": statistics.fmean(value_of(run) for run in members),
            }
        )
    rows.sort(
        key=lambda row: (-row["value"] if reverse else row["value"], row["model"], row["loadout"])
    )
    return {"status": "ok", "rows": rows}


def _control_board(groups: dict[tuple[str, str], list[_ReportRun]]) -> dict:
    board = _mean_board(groups, lambda run: run.control, reverse=True)
    for row in board["rows"]:
        members = groups[(row["model"], row["loadout"])]
        # τ 未經 §10.4 estimator 校準的 Control 必須明示，不得偽裝正式值
        row["tau_uncalibrated"] = any(run.tau_uncalibrated for run in members)
    return board


def _ftr_board(groups: dict[tuple[str, str], list[_ReportRun]]) -> dict:
    board = _mean_board(groups, lambda run: run.ftr, reverse=False)
    for row in board["rows"]:
        members = groups[(row["model"], row["loadout"])]
        row["flood_create_tokens"] = sum(run.flood_create_tokens for run in members)
        row["flood_repair_tokens"] = sum(run.flood_repair_tokens for run in members)
    return board


# ---- 序列化 helpers ----------------------------------------------------------


def _num(value: float | None) -> float | str:
    """榜值序列化：NA → "NA"、無限大 → "inf"（artifact 格式英文）。"""
    if value is None:
        return _OFFLINE_NA
    if math.isinf(value):
        return "inf"
    return value


def _run_row(run: _ReportRun) -> dict:
    return {
        "run_id": run.run_id,
        "model": run.model,
        "loadout": run.loadout,
        "clear": run.clear,
        "power_total": run.power_total,
        "cost": _OFFLINE_NA if run.cost is None else str(run.cost),
        "work_tokens": _OFFLINE_NA if run.work_tokens is None else run.work_tokens,
        "observable_tokens": run.observable_tokens,
        "control": run.control,
        "ftr": run.ftr,
        "tau_uncalibrated": run.tau_uncalibrated,
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# pilot 子命令（spec §11；plan Task 18）
# ---------------------------------------------------------------------------


def _cmd_pilot(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud pilot",
        description=(
            "Forced loadout 實驗矩陣：encounters × 8 loadouts × models 依 "
            "sealed schedule 逐項執行（spec §11）。"
        ),
    )
    parser.add_argument("--deck", required=True, type=Path, help="deck 目錄")
    parser.add_argument("--models", required=True, type=Path, help="models.yaml")
    parser.add_argument("--seed", required=True, type=int, help="排程 seed（F21）")
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("runs") / "pilot",
        help="pilot 落盤根目錄（schedule.yaml / registry.jsonl / run 目錄）",
    )
    parser.add_argument(
        "--force",
        action="append",
        default=[],
        metavar="RUN_ID",
        help="顯式重跑已完成的 run_id（attempt+1；舊 run 目錄與 registry 行保留）",
    )
    ns = parser.parse_args(argv)

    try:
        report = pilot_cli(
            ns.deck, ns.models, ns.seed, ns.runs_root, force=tuple(ns.force)
        )
    except (
        PilotError,
        ScheduleError,
        RunCliError,
        ScoreDiffError,
        AdapterError,
        DeckError,
        EvaluatorError,
        StoreError,
        WorkspaceError,
        LedgerError,
        ValueError,
    ) as exc:
        print(f"pilot 失敗：{exc}", file=sys.stderr)
        return 2

    print(
        f"pilot 完成：schedule={report.schedule_sha256[:12]} "
        f"executed={len(report.executed)} skipped={len(report.skipped)}"
    )
    return 0


def pilot_cli(
    deck_dir: Path,
    models_path: Path,
    seed: int,
    runs_root: Path,
    *,
    force: tuple[str, ...] = (),
    bwrap_path: str = DEFAULT_BWRAP_PATH,
) -> PilotReport:
    """`patchmud pilot` 串線：矩陣展開 → schedule 封存（或核驗既有封存）→
    `PilotRunner`（preflight gates → registry write-ahead 冪等續跑）（spec
    §11、F21）。run 中途中斷（kill／provider 錯誤上拋）留下的 partial run
    目錄一律保留；重啟時 runner 依 registry 殘留的 started 行遞增 attempt，
    續跑落在新目錄（`<run_id>--attempt<N>`），不需手動清理、不需 --force。"""
    deck_dir = Path(deck_dir).resolve()
    encounters = (
        tuple(
            sorted(p.name for p in deck_dir.iterdir() if (p / "card.yaml").is_file())
        )
        if deck_dir.is_dir()
        else ()
    )
    if not encounters:
        raise PilotError(f"deck 目錄沒有任何 encounter（card.yaml）：{deck_dir}")
    models = load_models(Path(models_path))
    loadouts = tuple(
        f"P{p}T{t}R{r}" for p in (0, 1) for t in (0, 1) for r in (0, 1)
    )
    matrix = RunMatrix(
        encounters=encounters,
        loadouts=loadouts,
        models=tuple(entry.id for entry in models),
    )

    runs_root = Path(runs_root)
    schedule_path = runs_root / "schedule.yaml"
    built = build_schedule(matrix, seed)
    if schedule_path.exists():
        sealed = load_schedule(schedule_path)
        if sealed.sha256 != built.sha256:
            raise ScheduleError(
                "既有封存 schedule 與 --deck/--models/--seed 展開結果不符，"
                "拒絕執行（F21）"
            )
        schedule = sealed
    else:
        save_schedule(built, schedule_path)
        schedule = built

    toolchain = _toolchain_paths()
    models_by_id = {entry.id: entry for entry in models}

    def execute(item: ScheduleItem, attempt: int) -> dict:
        encounter_dir = deck_dir / item.encounter
        card = load_card(encounter_dir / "card.yaml")
        entry = models_by_id[item.model]
        adapter = _build_adapter(entry.adapter)
        effective_run_id = (
            item.run_id if attempt == 1 else f"{item.run_id}--attempt{attempt}"
        )
        result = _wire_and_run_encounter(
            card,
            encounter_dir,
            adapter,
            Loadout.from_string(item.loadout),
            runs_root,
            effective_run_id,
            bwrap_path=bwrap_path,
            record_extra={
                "model": entry.adapter,
                "model_id": entry.id,
                "schedule_ref": schedule.sha256,
            },
        )
        return {
            "run_dir": str(runs_root / effective_run_id),
            "end_reason": result.end_reason,
            "clear": result.clear,
        }

    runner = PilotRunner(
        registry_path=runs_root / "registry.jsonl",
        execute=execute,
        models=models,
        capabilities=lambda: IsolationRunner(
            Path("/tmp"), toolchain, bwrap_path=bwrap_path
        ).capabilities(),
        repo_root=_git_repo_root(),
    )
    return runner.run(schedule_path, force=force)


def _git_repo_root() -> Path:
    """estimators gate（F4）以 cwd 所在 git repo 為準；查無 repo fail-closed。"""
    res = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if res.returncode != 0 or not res.stdout.strip():
        raise PilotError("找不到 git repo root（F4 estimators gate 需要），拒絕啟動")
    return Path(res.stdout.strip())


# ---------------------------------------------------------------------------
# result.yaml 組裝（純資料轉換）
# ---------------------------------------------------------------------------


def _build_result(
    *,
    run_id: str,
    diff_sha256: str,
    clear: int,
    main_public_green: bool,
    evaluation: FinalEvaluation,
    public_results: ProbeResults,
    economy: str,
    economy_reason: str,
) -> dict:
    return {
        "run_id": run_id,
        "mode": "score-diff",
        "diff_sha256": diff_sha256,
        "clear": clear,
        "protocol_failed": False,
        "main_public_green": main_public_green,
        "gates": {
            "critical_pass": evaluation.gates.critical_pass,
            "power_cap": evaluation.gates.power_cap,
            "run_invalid": evaluation.gates.run_invalid,
        },
        "power": _power_dict(evaluation.power),
        "probes": {
            "public": _probes_dict(public_results),
            "evaluator": _probes_dict(evaluation.probe_outcomes),
        },
        "economy": economy,
        "economy_reason": economy_reason,
    }


def _power_dict(power: PowerReport) -> dict:
    breakdown = power.maintainability_breakdown
    return {
        "functional": power.functional,
        "robustness": power.robustness,
        "compatibility": power.compatibility,
        "maintainability": power.maintainability,
        "runtime_efficiency": power.runtime_efficiency,
        "total": power.total,
        "maintainability_breakdown": {
            "diff_size": breakdown.diff_size,
            "scope": breakdown.scope,
            "lint": breakdown.lint,
            "total": breakdown.total,
            "production_loc": breakdown.production_loc,
            "scope_hard_files": list(breakdown.scope_hard_files),
            "scope_soft_loc": breakdown.scope_soft_loc,
            "lint_new_diagnostics": breakdown.lint_new_diagnostics,
        },
        "perf_judgments": [
            {
                "probe_id": j.probe_id,
                "wall_ms": j.wall_ms,
                "budget_ms": j.budget_ms,
                "passed": j.passed,
                "within_budget": j.within_budget,
            }
            for j in power.perf_judgments
        ],
    }


def _probes_dict(results: ProbeResults) -> dict:
    return {probe_id: _outcome_dict(results[probe_id]) for probe_id in results}


def _outcome_dict(outcome: ProbeOutcome) -> dict:
    return {
        "status": outcome.status,
        "cases_total": outcome.cases_total,
        "cases_passed": outcome.cases_passed,
        "failure_fingerprints": list(outcome.failure_fingerprints),
        "wall_ms": outcome.wall_ms,
        "cpu_ms": outcome.cpu_ms,
    }


def _economy_na(card: IssueCard) -> tuple[str, str]:
    """milestone A 離線評分的 Economy 一律 `NA`（不得記 0，§10.1）。"""
    if card.reference_cost is None:
        return _OFFLINE_NA, "card reference_cost 為 null，Economy 無法計算"
    return _OFFLINE_NA, "離線評分無 ledger 支出，Economy 不適用"


def _is_green(results: ProbeResults, probe_id: str) -> bool:
    return probe_id in results and results[probe_id].status == "passed"


# ---------------------------------------------------------------------------
# 沙箱 toolchain 佈線（probe 執行唯一 seam 是 IsolationRunner）
# ---------------------------------------------------------------------------


def _require_isolation(bwrap_path: str, toolchain: tuple[Path, ...]) -> None:
    """namespace 能力 fail-closed：不足即拒絕執行 candidate code（spec §7）。"""
    probe = IsolationRunner(Path("/tmp"), toolchain, bwrap_path=bwrap_path)
    caps = probe.capabilities()
    if not (caps.mount_ns and caps.net_ns and caps.pid_ns):
        raise ScoreDiffError("sandbox 隔離能力不足（degraded），拒絕執行 candidate code")


def _pytest_site_dir() -> Path:
    """host pytest 的 site-packages 目錄（沙箱內以 ro-bind + sys.path 提供）。"""
    spec = importlib.util.find_spec("pytest")
    if spec is None or spec.origin is None:
        raise ScoreDiffError("找不到 pytest，probe 無法在沙箱內執行")
    return Path(spec.origin).resolve().parents[1]


def _toolchain_paths() -> tuple[Path, ...]:
    """沙箱 ro-bind 的 toolchain 路徑：/usr + pytest site dir（不在 /usr 下時）。"""
    site_dir = _pytest_site_dir()
    paths: list[Path] = [Path("/usr")]
    if not site_dir.is_relative_to(Path("/usr")):
        paths.append(site_dir)
    return tuple(paths)


def _sandbox_pytest_argv() -> tuple[str, ...]:
    """沙箱內 pytest 起手式：以 bootstrap 注入 site dir（沙箱無 HOME/user-site）。"""
    site_dir = _pytest_site_dir()
    if site_dir.is_relative_to(Path("/usr")):
        return DEFAULT_PYTEST_ARGV
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(site_dir)!r}); "
        "from pytest import console_main; sys.exit(console_main())"
    )
    return ("python3", "-B", "-c", bootstrap)


def _ruff_argv(toolchain: tuple[Path, ...]) -> tuple[str, ...]:
    """沙箱內可執行的 ruff argv；找不到或不在 toolchain 內 → 交給 evaluator
    fail-closed（lint 0 分），不擴充 bind allowlist。"""
    found = shutil.which("ruff")
    if found is not None:
        ruff = Path(found).resolve()
        if any(ruff.is_relative_to(root) for root in toolchain):
            return (str(ruff),)
    return ("ruff",)


# ---------------------------------------------------------------------------
# calibrate 子命令（plan Task 19；spec §10.4、F4）
# ---------------------------------------------------------------------------


def _cmd_calibrate(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="patchmud calibrate",
        description=(
            "pilot 後校準：從封存 run 產出 reference_cost/difficulty_scale/τ/"
            "EuTB 預算，寫回 deck card 並凍結進 --out（§10.4）。"
        ),
    )
    parser.add_argument("--runs", required=True, help="run 目錄 glob，如 'runs/*'")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_REGISTERED_DIR, help="registered 參數目錄"
    )
    parser.add_argument("--pricing", required=True, type=Path, help="pricing snapshot 檔")
    ns = parser.parse_args(argv)

    try:
        calibration_path, _ = calibrate_from_runs(ns.runs, ns.out, ns.pricing)
    except (
        CalibrationError,
        FrozenCalibrationError,
        ReportError,
        DeckError,
        StoreError,
        ReplayError,
        FloodError,
        LedgerError,
    ) as exc:
        print(f"calibrate 失敗：{exc}", file=sys.stderr)
        return 2

    print(f"校準凍結：{calibration_path}")
    return 0


def calibrate_from_runs(
    runs_glob: str, out_dir: Path, pricing_path: Path
) -> tuple[Path, Path]:
    """封存 run → estimator → 凍結。`--out` 缺 pre-registered estimators.yaml
    一律拒絕產出（F4）；估計、寫回 card 與 registered 檔為全有全無。"""
    out_dir = Path(out_dir)
    if not (out_dir / "estimators.yaml").is_file():
        raise CalibrationError(
            f"--out 缺 pre-registered estimators.yaml，拒絕產出校準值：{out_dir}"
        )

    snapshot = PricingSnapshot.load(Path(pricing_path))
    coeffs = load_flood_coeffs()

    matches = sorted(globmod.glob(str(runs_glob)))
    if not matches:
        raise CalibrationError(f"--runs glob 無任何匹配：{runs_glob!r}")

    runs: list[CalibrationRun] = []
    deck: dict[str, Path] = {}
    for match in matches:
        run_dir = Path(match)
        if not (run_dir / "run.yaml").is_file():
            continue
        loaded = _load_calibration_run(run_dir, snapshot, coeffs)
        if loaded is None:
            continue
        run, encounter_dir = loaded
        runs.append(run)
        deck.setdefault(run.encounter, encounter_dir)

    if not runs:
        raise CalibrationError(f"glob 匹配 {len(matches)} 項但無任何可校準 run")

    result = calibrate(runs)
    return freeze_calibration(result, out_dir, deck)


def _load_calibration_run(
    run_dir: Path, snapshot: PricingSnapshot, coeffs: object
) -> tuple[CalibrationRun, Path] | None:
    """單一封存 run → CalibrationRun（human / 非 run mode → None，不進校準）。"""
    store = RunStore.open(run_dir)  # fail-closed：run.yaml schema、event seq
    record = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))

    result_path = run_dir / "result.yaml"
    if not result_path.is_file():
        return None
    result = yaml.safe_load(result_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ReportError(f"result.yaml 內容必須是 mapping：{run_dir.name}")
    if result.get("human") is True or result.get("mode") != "run":
        return None

    clear = result.get("clear")
    if clear not in (0, 1):
        raise ReportError(f"result.yaml clear 非 0/1（{run_dir.name}）：{clear!r}")

    encounter_dir = Path(str(record["encounter_dir"]))
    card = load_card(encounter_dir / "card.yaml")

    power = result.get("power")
    breakdown = power.get("maintainability_breakdown") if isinstance(power, dict) else None
    if not isinstance(breakdown, dict) or not isinstance(
        breakdown.get("production_loc"), int
    ):
        raise ReportError(
            f"result.yaml 缺 power.maintainability_breakdown.production_loc（{run_dir.name}）"
        )
    production_loc = int(breakdown["production_loc"])

    ledger_obj = result.get("ledger")
    if not isinstance(ledger_obj, dict) or "work_tokens" not in ledger_obj:
        raise ReportError(f"result.yaml 缺 ledger.work_tokens（{run_dir.name}）")
    work_tokens = ledger_obj["work_tokens"]

    entries = load_ledger(run_dir)
    cost = compute_run_cost(entries, snapshot).total
    flood = flood_metrics(store.load_events(), card, coeffs)

    run = CalibrationRun(
        run_id=str(record["run_id"]),
        encounter=card.issue_id,
        clear=int(clear),
        cost=cost,
        final_diff_loc=production_loc,
        flood_index=float(flood.flood_index),
        work_tokens=work_tokens if isinstance(work_tokens, int) else 0,
    )
    return run, encounter_dir


if __name__ == "__main__":
    raise SystemExit(main())
