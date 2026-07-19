# 白話戰報層（Part A）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 在 versus 呈現層加一層「人話」——每關一句 bug 說明、每回合白話敘述、收尾「誰贏在哪」判詞、分數收乾淨——不動 benchmark 語意與隔離。

**Architecture:** 新增純函式模組 `patchmud/engine/narration.py`（`format_power` / `narrate_round` / `verdict` / `encounter_briefing`），全部經 `render_zh_tw.text()` 取文案；`versus.py` 改用它們並在開場印 briefing、收尾印判詞；`deck` 契約加選填 `briefing` 欄位。

**Tech Stack:** Python 3、既有 render pack、pytest。

## Global Constraints

- zh-TW 敘事一律經 `render_zh_tw.text()`，禁止硬編中文字串（AST 測試把關）。
- render 文案任何改動必須 bump `RENDER_PACK_VERSION`（本 PR：`1.4.0 → 1.5.0`）。
- 不改引擎/隔離/評分；只動呈現層與 deck 選填欄位。
- feature 分支、`changelog.d/*.md` fragment、`policy_check` 零 fail、CI 綠才 merge。

---

### Task A1: `narration.py` 純函式（format_power / narrate_round / verdict / encounter_briefing）+ render pack keys

**Files:**
- Create: `patchmud/engine/narration.py`
- Modify: `patchmud/engine/render_zh_tw.py`（新增 versus.* keys、bump 版本）
- Test: `tests/engine/test_render_narration.py`

**Interfaces:**
- Produces:
  - `format_power(value: object) -> str`（float → 1 位小數字串；非數值 → `na`）
  - `encounter_briefing(card) -> str`（有 `card.briefing` 用它，否則 `public_requirements[0].text`）
  - `narrate_round(rd: dict) -> str`（rd 鍵：`baseline,action,outcome,resolved,backlog_before,backlog_after`）
  - `verdict(entries: list[VersusEntry]) -> str`

- [ ] **Step 1: 先加 render pack keys 並 bump 版本**

在 `render_zh_tw.py` 的 `MESSAGES` 內 versus 區塊新增（並把 `RENDER_PACK_VERSION` 改 `"1.5.0"`，加註解 `#: 1.5.0：新增 versus 白話戰報 briefing/每回合敘述/verdict（Part A）。`）：

```python
    "versus.briefing": "【這關的 bug】{text}",
    "versus.model_line_v2": "  {model}｜{narration}",
    "versus.round.baseline": "開場：待辦 {after} 件",
    "versus.round.resolved": "{action}：修好了 {ids}（待辦 {before}→{after}）",
    "versus.round.patch_noop": "PATCH 套用了，但沒解決任何議題（待辦仍 {after}）",
    "versus.round.patch_failed": "PATCH 套用失敗，這刀打空了（待辦仍 {after}）",
    "versus.round.commit": "COMMIT 收場（待辦 {after}）",
    "versus.round.illegal": "{action} 被判不合法，白費一回合（待辦仍 {after}）",
    "versus.round.parse_error": "回覆讀不懂，白費一回合（待辦仍 {after}）",
    "versus.round.observed": "{action}：察看戰場，沒動手（待辦仍 {after}）",
    "versus.verdict_header": "════ 誰贏在哪 ════",
    "versus.verdict.split": "{winners} 通關、{losers} 沒有——差別在：{reason}",
    "versus.verdict.all_clear": "都通關；{fastest} 最省，只花 {turns} 回合",
    "versus.verdict.none_clear": "這關無人通關",
    "versus.reason.public_red": "改動沒通過公開測試",
    "versus.reason.critical_red": "表面看似修好，卻沒通過隱藏的關鍵測試",
    "versus.reason.other": "未達通關門檻",
```

- [ ] **Step 2: Write the failing test**

```python
# tests/engine/test_render_narration.py
from types import SimpleNamespace
from patchmud.engine.narration import (
    format_power, narrate_round, verdict, encounter_briefing,
)
from patchmud.engine.versus import VersusEntry


class TestFormatPower:
    def test_rounds_to_one_decimal(self):
        assert format_power(27.714285714285715) == "27.7"
        assert format_power(97.0) == "97.0"
    def test_non_number_is_na(self):
        assert format_power("NA") == "NA"
        assert format_power(None) == "NA"
        assert format_power(True) == "NA"  # bool 不是分數


class TestEncounterBriefing:
    def test_uses_briefing_when_present(self):
        card = SimpleNamespace(briefing="值裡有 = 會被切爛", public_requirements=())
        assert encounter_briefing(card) == "值裡有 = 會被切爛"
    def test_falls_back_to_first_requirement(self):
        req = SimpleNamespace(text="只在第一個 = 切分")
        card = SimpleNamespace(briefing=None, public_requirements=(req,))
        assert encounter_briefing(card) == "只在第一個 = 切分"


class TestNarrateRound:
    def test_baseline(self):
        assert "待辦 1" in narrate_round(
            {"baseline": True, "backlog_after": 1})
    def test_resolved(self):
        s = narrate_round({"baseline": False, "action": "PATCH",
                           "outcome": "executed", "resolved": ["MAIN-1"],
                           "backlog_before": 1, "backlog_after": 0})
        assert "修好了" in s and "MAIN-1" in s
    def test_patch_noop(self):
        s = narrate_round({"baseline": False, "action": "PATCH",
                           "outcome": "executed", "resolved": [],
                           "backlog_before": 1, "backlog_after": 1})
        assert "沒解決" in s
    def test_patch_failed(self):
        s = narrate_round({"baseline": False, "action": "PATCH",
                           "outcome": "error", "resolved": [],
                           "backlog_before": 1, "backlog_after": 1})
        assert "打空" in s
    def test_commit(self):
        s = narrate_round({"baseline": False, "action": "COMMIT",
                           "outcome": "executed", "resolved": [],
                           "backlog_before": 0, "backlog_after": 0})
        assert "COMMIT" in s
    def test_parse_error(self):
        s = narrate_round({"baseline": False, "action": None,
                           "outcome": "parse_error", "resolved": [],
                           "backlog_before": 1, "backlog_after": 1})
        assert "讀不懂" in s


def _entry(model, clear, turns, power, main_green=True, crit=True):
    return VersusEntry(model=model, events=[], result={
        "clear": clear, "turns": turns, "power": {"total": power},
        "main_public_green": main_green, "gates": {"critical_pass": crit},
    })


class TestVerdict:
    def test_split_reason_critical(self):
        s = verdict([_entry("a", 1, 2, 97.0),
                     _entry("b", 0, 2, 27.7, main_green=True, crit=False)])
        assert "隱藏" in s
    def test_split_reason_public(self):
        s = verdict([_entry("a", 1, 2, 97.0),
                     _entry("b", 0, 2, 20.0, main_green=False, crit=False)])
        assert "公開測試" in s
    def test_all_clear_names_fastest(self):
        s = verdict([_entry("slow", 1, 3, 97.0), _entry("fast", 1, 2, 97.0)])
        assert "fast" in s and "2" in s
    def test_none_clear(self):
        assert "無人通關" in verdict([_entry("a", 0, 2, 10.0)])
```

- [ ] **Step 3: Run test — expect ImportError (narration 未建)**

Run: `python3 -m pytest tests/engine/test_render_narration.py -q`
Expected: FAIL（`ModuleNotFoundError: patchmud.engine.narration`）

- [ ] **Step 4: 實作 `narration.py`**

```python
# patchmud/engine/narration.py
"""versus 戰報白話推導：純函式，所有文案經 render pack（禁硬編中文）。"""

from __future__ import annotations

from patchmud.engine import render_zh_tw as zh

__all__ = ["format_power", "encounter_briefing", "narrate_round", "verdict"]


def format_power(value: object) -> str:
    """Power 分數顯示：四捨五入到 1 位小數；非數值（含 bool）→ na。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return zh.text("na")
    return f"{round(float(value), 1):.1f}"


def encounter_briefing(card: object) -> str:
    """一句話 bug 說明：有 card.briefing 用它，否則退回第一條 public 需求。"""
    briefing = getattr(card, "briefing", None)
    if briefing:
        return str(briefing)
    reqs = getattr(card, "public_requirements", ()) or ()
    return reqs[0].text if reqs else zh.text("na")


def narrate_round(rd: dict) -> str:
    """單一回合 → 一句人話。rd 鍵見 versus._timeline。"""
    after = rd.get("backlog_after")
    if rd.get("baseline"):
        return zh.text("versus.round.baseline", after=after)
    action = rd.get("action") or zh.text("na")
    outcome = rd.get("outcome")
    resolved = rd.get("resolved") or []
    if outcome == "parse_error":
        return zh.text("versus.round.parse_error", after=after)
    if outcome == "illegal":
        return zh.text("versus.round.illegal", action=action, after=after)
    if resolved:
        return zh.text(
            "versus.round.resolved", action=action,
            ids="、".join(resolved), before=rd.get("backlog_before"), after=after,
        )
    if action == "PATCH":
        key = "versus.round.patch_failed" if outcome == "error" else "versus.round.patch_noop"
        return zh.text(key, after=after)
    if action == "COMMIT":
        return zh.text("versus.round.commit", after=after)
    return zh.text("versus.round.observed", action=action, after=after)


def _power_num(entry) -> float:
    power = entry.result.get("power")
    if isinstance(power, dict) and isinstance(power.get("total"), (int, float)):
        return float(power["total"])
    return 0.0


def _turns(entry) -> int:
    turns = entry.result.get("turns")
    return turns if isinstance(turns, int) else 0


def _reason(result: dict) -> str:
    if not result.get("main_public_green", False):
        return zh.text("versus.reason.public_red")
    gates = result.get("gates") or {}
    if not gates.get("critical_pass", False):
        return zh.text("versus.reason.critical_red")
    return zh.text("versus.reason.other")


def _short(model: str) -> str:
    from patchmud.engine.versus import _short as short
    return short(model)


def verdict(entries: list) -> str:
    """收尾判詞：誰贏在哪。"""
    if not entries:
        return zh.text("versus.verdict.none_clear")
    cleared = [e for e in entries if e.result.get("clear") == 1]
    if not cleared:
        return zh.text("versus.verdict.none_clear")
    if len(cleared) == len(entries):
        fastest = min(entries, key=lambda e: (_turns(e), -_power_num(e)))
        return zh.text("versus.verdict.all_clear",
                       fastest=_short(fastest.model), turns=_turns(fastest))
    losers = [e for e in entries if e.result.get("clear") != 1]
    return zh.text(
        "versus.verdict.split",
        winners="、".join(_short(e.model) for e in cleared),
        losers="、".join(_short(e.model) for e in losers),
        reason=_reason(losers[0].result),
    )
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `python3 -m pytest tests/engine/test_render_narration.py -q`
Expected: PASS（全綠）

- [ ] **Step 6: Commit**

```bash
git add patchmud/engine/narration.py patchmud/engine/render_zh_tw.py tests/engine/test_render_narration.py
git commit -m "feat(versus): 白話戰報推導純函式 + render pack keys"
```

---

### Task A2: deck `briefing` 選填欄位

**Files:**
- Modify: `patchmud/deck/model.py:98-118`（IssueCard 加 `briefing`）
- Modify: `patchmud/deck/loader.py:100-134`（讀取 briefing）
- Test: `tests/deck/test_loader.py`

**Interfaces:**
- Consumes: `IssueCard`（既有）
- Produces: `IssueCard.briefing: str | None`（預設 None）

- [ ] **Step 1: Write the failing test**（加到 `tests/deck/test_loader.py`）

```python
def test_briefing_optional_present(tmp_path):
    # 既有 valid card fixture helper（沿用檔內 _write_card / minimal_card_dict）
    data = minimal_card_dict()
    data["briefing"] = "值裡含 = 會被切爛"
    card = load_card(_write_card(tmp_path, data))
    assert card.briefing == "值裡含 = 會被切爛"

def test_briefing_defaults_none(tmp_path):
    card = load_card(_write_card(tmp_path, minimal_card_dict()))
    assert card.briefing is None
```

> 若 `tests/deck/test_loader.py` 尚無 `minimal_card_dict` / `_write_card`，沿用該檔既有建構 valid card 的 helper（讀檔頂端；名稱以實際為準）。

- [ ] **Step 2: Run test — expect FAIL**

Run: `python3 -m pytest tests/deck/test_loader.py -k briefing -q`
Expected: FAIL（`IssueCard` 無 `briefing` / `AttributeError`）

- [ ] **Step 3: 實作**

`model.py` 的 `IssueCard` 在 `reference_cost` 之後（同為選填、有預設）加：

```python
    reference_cost: Decimal | None = None
    briefing: str | None = None
```

`loader.py` 的 `_build_card` 回傳 `IssueCard(...)` 末尾加參數：

```python
        reference_cost=reference_cost,
        briefing=(str(data["briefing"]) if data.get("briefing") is not None else None),
```

- [ ] **Step 4: Run test — expect PASS**

Run: `python3 -m pytest tests/deck/test_loader.py -k briefing -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patchmud/deck/model.py patchmud/deck/loader.py tests/deck/test_loader.py
git commit -m "feat(deck): card 選填 briefing 欄位"
```

---

### Task A3: versus 併入白話（timeline 擴充、model 行、briefing 行、verdict 段）

**Files:**
- Modify: `patchmud/engine/versus.py`（`_timeline` / `_model_line` / `_score_row` / `render_versus`）
- Modify: `patchmud/cli.py`（`_cmd_versus` 傳 briefing；`run --live` 分數格式）
- Test: `tests/engine/test_versus.py`（更新斷言）

**Interfaces:**
- Consumes: `narration.format_power / narrate_round / verdict / encounter_briefing`
- Produces: `render_versus(entries, encounter=None, briefing=None) -> str`

- [ ] **Step 1: 更新/新增 versus 測試**

```python
# tests/engine/test_versus.py 內（沿用既有 _mk_entry / 事件 fixtures）
def test_render_shows_briefing_and_verdict():
    entries = [_winner_entry(), _loser_entry()]  # 既有或新建 helper
    out = render_versus(entries, encounter="parser-edge-v1",
                        briefing="值裡含 = 會被切爛")
    assert "【這關的 bug】值裡含 = 會被切爛" in out
    assert "誰贏在哪" in out
    # 分數 1 位小數，不得出現長浮點
    assert "27.714285" not in out

def test_model_line_is_plain_language():
    entries = [_loser_entry()]
    out = render_versus(entries, encounter="x")
    assert "backlog" not in out  # 不再露黑話
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python3 -m pytest tests/engine/test_versus.py -q`
Expected: FAIL（briefing/verdict 尚未輸出）

- [ ] **Step 3: 實作 versus.py 變更**

`_timeline` 改為每回合帶完整鍵：

```python
def _timeline(events: list[dict]) -> list[dict]:
    rounds: list[dict] = []
    prev_open: set[str] = set()
    prev_backlog = 0
    for event in events:
        etype = event.get("type")
        if etype not in (_BASELINE, _TURN):
            continue
        queue = event.get("queue") or {}
        open_items = queue.get("open_items") or []
        open_ids = {str(i.get("item_id")) for i in open_items if isinstance(i, dict)}
        backlog = queue.get("b_t", len(open_ids))
        if etype == _BASELINE:
            rounds.append({"baseline": True, "action": None, "outcome": None,
                           "resolved": [], "backlog_before": backlog,
                           "backlog_after": backlog})
        else:
            rounds.append({"baseline": False, "action": event.get("action"),
                           "outcome": event.get("outcome"),
                           "resolved": sorted(prev_open - open_ids),
                           "backlog_before": prev_backlog, "backlog_after": backlog})
        prev_open, prev_backlog = open_ids, backlog
    return rounds
```

`_model_line` 改用白話：

```python
def _model_line(model: str, timeline: list[dict], r: int) -> str:
    if r >= len(timeline):
        return zh.text("versus.model_done", model=_short(model))
    return zh.text("versus.model_line_v2", model=_short(model),
                   narration=narration.narrate_round(timeline[r]))
```

`_score_row` 分數改用 `format_power`：

```python
    power = narration.format_power(_power_total(result))
```

`render_versus` 簽章加 `briefing`，開場印 briefing、記分板後印 verdict：

```python
def render_versus(entries, encounter=None, briefing=None):
    ...
    lines = [zh.text("versus.header", ...), zh.text("versus.participants", names=names)]
    if briefing:
        lines.append(zh.text("versus.briefing", text=briefing))
    lines.append("")
    ...  # 回合迴圈不變（改走 _model_line 新模板）
    lines.append(zh.text("versus.scoreboard_header"))
    for entry in entries:
        lines.append(_score_row(entry))
    lines.append("")
    lines.append(zh.text("versus.verdict_header"))
    lines.append(narration.verdict(entries))
    return "\n".join(lines).rstrip()
```

檔頭加 `from patchmud.engine import narration`。

- [ ] **Step 4: CLI 佈線 briefing + run --live 分數**

`cli.py` `_cmd_versus`：`load_card(encounter_dir / "card.yaml")` → `narration.encounter_briefing(card)`，傳入 `render_versus(entries, encounter=<名字>, briefing=<briefing>)`。
`run --live` 終局結算組 `run.live_settlement` 時 `power=narration.format_power(power_total)`。

- [ ] **Step 5: Run 全套 versus + cli 測試 — expect PASS**

Run: `python3 -m pytest tests/engine/test_versus.py tests/test_friendly_cli.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patchmud/engine/versus.py patchmud/cli.py tests/engine/test_versus.py
git commit -m "feat(versus): 開場 briefing、每回合白話、收尾誰贏在哪判詞"
```

---

### Task A4: 既有 8 關補 briefing + 全套回歸 + changelog

**Files:**
- Modify: `decks/pilot-v1/*/card.yaml`（8 檔各加一行 `briefing:`）
- Create: `changelog.d/legible-battle-report.md`

- [ ] **Step 1: 為 8 關各加一句白話 briefing**

每個 `card.yaml` 頂層加（例，依各關母題；文字取自 provenance `variant_notes` 精煉）：

```yaml
briefing: 折扣碼驗證漏了長度檢查，10 個字以上的爛碼會被當成有效
```

（parser-edge：`key=value 解析把值裡的 = 一起切掉，含網址等值會損毀`；legacy-regression：`版本比較用字串序，"1.9" 會被誤判為大於 "1.10"`；state-recovery：`暫時性掃描失敗被當成真的移除，產生假的移除事件`。v2 同母題照套。）

- [ ] **Step 2: validate-deck 全綠**

Run: `python3 -c "from patchmud.cli import main; main(['validate-deck','decks/pilot-v1','--no-write-timings'])"`
Expected: 每關 OK，exit 0

- [ ] **Step 3: 全套測試 + AST 硬編中文檢查 + policy**

Run: `python3 -m pytest -q && python3 -m policy_check --repo .`
Expected: 全綠、policy 0 fail

- [ ] **Step 4: changelog fragment**

```
# changelog.d/legible-battle-report.md
feat(versus): 白話戰報層——每關一句 bug 說明、每回合人話敘述、收尾「誰贏在哪」判詞，分數收成 1 位小數；deck 新增選填 briefing 欄位，既有 8 關補上。
```

- [ ] **Step 5: Commit + PR**

```bash
git add decks/pilot-v1/*/card.yaml changelog.d/legible-battle-report.md
git commit -m "feat(deck): 8 關補 briefing + changelog"
git push -u origin feature/legible-battle-report
```
PR title：`feat(versus): 白話戰報層`；body 不得有裸 `#N`。

## Self-Review

- 覆蓋：A1（format/narrate/verdict）✓ A2（briefing 欄位）✓ A3（versus 併入）✓ A4（8 關 + 回歸）✓。
- 無 placeholder；型別一致（`narrate_round` 的 rd 鍵與 `_timeline` 產出一致）。
- render 版本 bump 1.5.0 一次涵蓋全部新 key。
