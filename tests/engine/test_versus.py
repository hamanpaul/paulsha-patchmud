"""versus 並排對戰視圖：純渲染 + 隔離 orchestration。"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import yaml

import patchmud.engine.versus as versus_module
from patchmud.engine.versus import VersusEntry, render_versus, run_versus


def _baseline(open_ids):
    items = [{"item_id": i, "type": "MAIN"} for i in open_ids]
    return {"type": "baseline", "queue": {"b_t": len(items), "open_items": items}}


def _turn(n, action, open_ids):
    items = [{"item_id": i, "type": "MAIN"} for i in open_ids]
    return {
        "type": "turn",
        "turn": n,
        "action": action,
        "queue": {"b_t": len(items), "open_items": items},
    }


def _solver():
    return VersusEntry(
        model="anthropic:sonnet",
        events=[_baseline(["MAIN-1"]), _turn(1, "PATCH", []), _turn(2, "COMMIT", [])],
        result={"clear": 1, "turns": 2, "loadout": "P0T0R0", "power": {"total": 97.0}},
    )


def _lazy():
    return VersusEntry(
        model="anthropic:haiku",
        events=[_baseline(["MAIN-1"]), _turn(1, "LOOK", ["MAIN-1"]), _turn(2, "COMMIT", ["MAIN-1"])],
        result={"clear": 0, "turns": 2, "loadout": "P0T0R0", "power": {"total": 29.0}},
    )


class TestRenderVersus:
    def test_side_by_side_rounds_and_scoreboard(self):
        out = render_versus([_solver(), _lazy()], encounter="input-validation-v1")
        # 標題用關卡名，不是 run_id
        assert "input-validation-v1" in out
        assert "sonnet" in out and "haiku" in out
        # 逐回合並排
        assert "開場（基線）" in out
        assert "回合 1" in out
        # solver 在回合 1 修好 MAIN-1（白話）；lazy 沒有
        assert "修好了 MAIN-1" in out
        # 不再露黑話 backlog
        assert "backlog" not in out
        # 記分板：兩個模型各一列，結果對比
        assert "CLEAR 通關" in out
        assert "未通關" in out
        assert "Power 97.0" in out and "Power 29.0" in out

    def test_briefing_and_verdict(self):
        out = render_versus(
            [_solver(), _lazy()],
            encounter="parser-edge-v1",
            briefing="值裡含 = 會被切爛",
        )
        # 開場一句白話 bug 說明
        assert "【這關的 bug】值裡含 = 會被切爛" in out
        # 收尾「誰贏在哪」判詞：一勝一負 → split，理由指向公開測試沒過
        assert "誰贏在哪" in out
        assert "sonnet 通關" in out and "haiku 沒有" in out
        # 分數 1 位小數，不得出現長浮點殘留
        assert "27.714285" not in out

    def test_uneven_lengths_show_done(self):
        short = VersusEntry(
            model="m-short",
            events=[_baseline(["MAIN-1"]), _turn(1, "COMMIT", ["MAIN-1"])],
            result={"clear": 0, "turns": 1, "loadout": "P0T0R0", "power": {"total": 10}},
        )
        out = render_versus([short, _solver()], encounter="enc")
        # solver 有回合 2，short 已收場 → 顯示（已收場）
        assert "已收場" in out

    def test_empty_entries(self):
        assert render_versus([]) == versus_module.zh.text("versus.scoreboard_header")


class TestRunVersus:
    def test_runs_each_model_isolated(self, tmp_path):
        """run_versus 依序跑每個模型、獨立 run_id、載入其 events/result。"""
        calls: list[tuple] = []

        def fake_run_one(enc, spec, loadout, runs_root, run_id):
            calls.append((spec, run_id))
            # 模擬 run 落盤：建 run 目錄 + result.yaml + 最小 events
            from patchmud.store.run_store import RunStore

            store = RunStore.create(
                {
                    "run_id": run_id,
                    "frozen_sha": "f" * 40,
                    "pricing_hash": "NA",
                    "harness_prompt_version": "hp",
                    "schedule_ref": "NA",
                    "encounter_dir": str(enc),
                    "loadout": loadout,
                    "model": spec,
                },
                runs_root,
            )
            store.append_event(_baseline(["MAIN-1"]))
            store.write_result(
                {"clear": 1, "turns": 0, "loadout": loadout, "power": {"total": 50}}
            )
            return None

        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "mini_encounter"
        entries = run_versus(
            fixture,
            ["scripted:a", "scripted:b"],
            "P0T0R0",
            tmp_path / "runs",
            run_one=fake_run_one,
            now="STAMP",
        )
        assert [e.model for e in entries] == ["scripted:a", "scripted:b"]
        # 每個模型獨立 run_id（含 index，彼此不撞）
        run_ids = [rid for _spec, rid in calls]
        assert len(set(run_ids)) == 2
        assert all(rid.startswith("versus-STAMP-") for rid in run_ids)


class TestRenderPackSingleSource:
    def test_no_hardcoded_chinese_in_versus_module(self):
        tree = ast.parse(inspect.getsource(versus_module))
        docstring_ids = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstring_ids.add(id(body[0].value))
        offenders = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
            and any("一" <= ch <= "鿿" for ch in node.value)
        ]
        assert not offenders, f"versus.py 硬編中文 {offenders!r}；文案須進 render_zh_tw.py"
