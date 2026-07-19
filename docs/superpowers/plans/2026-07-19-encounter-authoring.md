# 出題模組（Part B）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 一個獨立模組 `patchmud/authoring/`，把「一個已解決（closed）的 bug」以結構化 `source.yaml` 變成一道凍結、可重現、驗證通過的 deck 關卡；含強制品質閘（bug 為真 + fix 為真）。

**Architecture:** `authoring/model.py`（`SourceSpec` 契約）、`authoring/loader.py`（`load_source` fail-closed）、`authoring/builder.py`（`build_encounter` 寫檔 + 品質閘，probe 執行走注入的 runner seam）。CLI `author-encounter` 佈上真 `IsolationRunner`。產物必須能過既有 `load_card` 與 `validate-deck`。

**Tech Stack:** Python 3、既有 `deck/` 契約與 `IsolationRunner`、pytest、git apply。

## Global Constraints

- 產物必須過 `patchmud.deck.loader.load_card`（fail-closed）與 `validate-deck`。
- probe 執行唯一 seam 是 `IsolationRunner`（plan invariant 3）；builder 收注入 runner，測試注入 fake。
- deck path 邊界比照 `deck/loader._require_clean_relpath`（拒絕絕對路徑、`.`/`..`、反斜線）。
- 出題者顯式標明 public/hidden（人在迴路，不自動猜）。
- feature 分支、`changelog.d/*.md` fragment、`policy_check` 零 fail、CI 綠才 merge。

---

### Task B1: `SourceSpec` 契約 + `load_source`

**Files:**
- Create: `patchmud/authoring/__init__.py`, `patchmud/authoring/model.py`, `patchmud/authoring/loader.py`
- Test: `tests/authoring/test_loader.py`

**Interfaces:**
- Produces:
  - `SourceSpec`（frozen dataclass，欄位見下）
  - `load_source(path: Path) -> SourceSpec`
  - `AuthoringError(ValueError)`

`SourceSpec` 欄位：`schema_version:int, issue_id:str, archetype:str, difficulty:str, summary:str, origin_source:str, origin_fixed_at:str|None, allowed_paths:tuple[str,...], expected_paths:tuple[str,...], buggy_repo:tuple[tuple[str,str],...], reference_patch:str, public_tests:tuple[tuple[str,str],...], hidden_tests:tuple[tuple[str,str],...], requirements:tuple[tuple[str,str],...], rubric_functional:int`（`tuple[tuple[path,content]]` 保序不可變）。

- [ ] **Step 1: Write failing test**

```python
# tests/authoring/test_loader.py
import pytest, yaml
from patchmud.authoring import load_source, AuthoringError

MINIMAL = {
    "schema_version": 1, "issue_id": "demo-v1", "archetype": "parser-edge",
    "difficulty": "easy", "summary": "值裡含 = 會被切爛",
    "origin": {"source": "closed bug: example.com/#1"},
    "allowed_paths": ["src/**", "tests/agent/**"], "expected_paths": ["src/x.py"],
    "buggy_repo": {"src/x.py": "def f(): pass\n"},
    "reference_patch": "diff --git a/src/x.py b/src/x.py\n",
    "public_tests": {"tests/public/test_x.py": "def test_x(): assert True\n"},
    "hidden_tests": {"test_cr1_x.py": "def test_cr1(): assert True\n"},
}

def _write(tmp_path, data):
    p = tmp_path / "source.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return p

def test_loads_minimal(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    assert spec.issue_id == "demo-v1"
    assert spec.summary == "值裡含 = 會被切爛"
    assert dict(spec.buggy_repo)["src/x.py"].startswith("def f")
    assert dict(spec.hidden_tests)["test_cr1_x.py"]

def test_missing_field_fails(tmp_path):
    data = {k: v for k, v in MINIMAL.items() if k != "reference_patch"}
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))

def test_path_traversal_rejected(tmp_path):
    data = dict(MINIMAL); data["buggy_repo"] = {"../evil.py": "x\n"}
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))

def test_hidden_test_name_must_be_bare(tmp_path):
    # hidden_tests 的 key 是檔名，不得帶目錄（builder 會放進 hidden/）
    data = dict(MINIMAL); data["hidden_tests"] = {"sub/x.py": "y\n"}
    with pytest.raises(AuthoringError):
        load_source(_write(tmp_path, data))
```

- [ ] **Step 2: Run — expect FAIL**（`ModuleNotFoundError: patchmud.authoring`）

Run: `python3 -m pytest tests/authoring/test_loader.py -q`

- [ ] **Step 3: 實作 model.py + loader.py + __init__.py**

`model.py`：`AuthoringError(ValueError)` + `@dataclass(frozen=True) class SourceSpec` 具上列欄位。
`loader.py`：讀 yaml → 檢查必填欄位（`schema_version, issue_id, archetype, difficulty, summary, origin, allowed_paths, expected_paths, buggy_repo, reference_patch, public_tests, hidden_tests`）；`schema_version==1`；對 `buggy_repo`/`public_tests` 的 key、`allowed_paths`/`expected_paths`、hidden_tests 檔名做路徑檢查（重用一份 `_clean_relpath`，hidden 檔名另檢「不得含 `/`」）；`requirements` 缺省 `(("MAIN-1", summary),)`；`rubric_functional` 缺省 60。回 `SourceSpec`。
`__init__.py`：匯出 `SourceSpec, load_source, build_encounter, AuthoringError`。

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/authoring/test_loader.py -q`

- [ ] **Step 5: Commit**

```bash
git add patchmud/authoring/ tests/authoring/test_loader.py
git commit -m "feat(authoring): SourceSpec 契約與 load_source"
```

---

### Task B2: `build_encounter` 寫檔 + 品質閘

**Files:**
- Create: `patchmud/authoring/builder.py`
- Test: `tests/authoring/test_builder.py`

**Interfaces:**
- Consumes: `SourceSpec`；`patchmud.deck.loader.load_card`
- Produces: `build_encounter(spec: SourceSpec, dest_dir: Path, *, runner, now: str) -> Path`
  - `runner` seam：`runner.probe_green(repo_files: dict[str,str], apply_patch: str | None, probe_relpath: str) -> bool`——在給定檔案樹（可選套 patch）上跑單一 probe，回是否 GREEN。CLI 佈真 `IsolationRunner`；測試注入 fake。

**品質閘（全過才凍結，否則 `AuthoringError`）：**
- reference.patch `git apply --check` 乾淨套用。
- 每條 hidden probe：buggy（未套）RED 且 套 patch 後 GREEN。
- 每條 public probe：buggy RED 且 套 patch 後 GREEN。

- [ ] **Step 1: Write failing test（注入 fake runner）**

```python
# tests/authoring/test_builder.py
import pytest
from pathlib import Path
from patchmud.authoring import load_source, build_encounter, AuthoringError
from patchmud.deck.loader import load_card
# 沿用 test_loader.MINIMAL + _write

class FakeRunner:
    """依 (probe, 是否套 patch) 決定綠紅；預設模擬「未套紅、套了綠」。"""
    def __init__(self, buggy_red=True, patched_green=True, apply_ok=True):
        self.buggy_red, self.patched_green, self.apply_ok = buggy_red, patched_green, apply_ok
    def probe_green(self, repo_files, apply_patch, probe_relpath):
        return self.patched_green if apply_patch else (not self.buggy_red)

def test_builds_valid_deck(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    dest = tmp_path / "out"
    got = build_encounter(spec, dest, runner=FakeRunner(), now="2026-07-19")
    card = load_card(got / "card.yaml")           # 產物過 deck 契約
    assert card.issue_id == "demo-v1"
    assert card.briefing == "值裡含 = 會被切爛"
    assert (got / "hidden" / "reference.patch").is_file()
    assert (got / "repo" / "src" / "x.py").is_file()
    prov = (got / "provenance.yaml").read_text(encoding="utf-8")
    assert "content_sha256" in prov and "2026-07-19" in prov

def test_gate_rejects_bug_not_real(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    with pytest.raises(AuthoringError):
        build_encounter(spec, tmp_path/"o", runner=FakeRunner(buggy_red=False), now="2026-07-19")

def test_gate_rejects_fix_not_real(tmp_path):
    spec = load_source(_write(tmp_path, MINIMAL))
    with pytest.raises(AuthoringError):
        build_encounter(spec, tmp_path/"o", runner=FakeRunner(patched_green=False), now="2026-07-19")
```

- [ ] **Step 2: Run — expect FAIL**（`build_encounter` 未實作）

- [ ] **Step 3: 實作 builder.py**

流程：寫 `repo/`（buggy_repo + 預設 `repo/conftest.py`——沿用 pilot 樣板把 `src` 掛 sys.path）、`repo/tests/public/*`（public_tests）、`hidden/reference.patch`、`hidden/<hidden test 檔>`；合成 `card.yaml`（public_requirements 綁 public 測試路徑、critical_requirements 綁 `hidden/<name>`、power_rubric 套預設權重、`briefing=summary`）；`load_card` 自驗；跑品質閘（git apply check + runner.probe_green 對 hidden/public 各跑 buggy 與 patched）；寫 `provenance.yaml`（`archetype_source=origin_source`、`variant_notes=summary`、`frozen_at=now`、`content_sha256`=deck 內容雜湊排除 provenance）。回 dest。

- [ ] **Step 4: Run — expect PASS**

Run: `python3 -m pytest tests/authoring/test_builder.py -q`

- [ ] **Step 5: Commit**

```bash
git add patchmud/authoring/builder.py tests/authoring/test_builder.py
git commit -m "feat(authoring): build_encounter 寫檔與品質閘"
```

---

### Task B3: CLI `author-encounter` + 真 runner seam

**Files:**
- Modify: `patchmud/cli.py`（新增子命令 + 真 `IsolationRunner` 佈線）
- Modify: `patchmud/engine/render_zh_tw.py`（author 結果文案；bump 版本若本 PR 未含 Part A）
- Test: `tests/authoring/test_author_cli.py`

- [ ] **Step 1: Write failing test（真 bwrap，缺能力則 skip）**

```python
# tests/authoring/test_author_cli.py — 沿用既有 real_capabilities fixture
def test_author_command_produces_playable_deck(real_capabilities, tmp_path):
    src = _min_real_source(tmp_path)   # 一份最小真 closed-bug source（bug 真紅、patch 真綠）
    rc = main(["author-encounter", str(src), "--into", str(tmp_path/"deck")])
    assert rc == 0
    deck = tmp_path / "deck" / "demo-real-v1"
    assert (deck / "card.yaml").is_file()
    # 產出的關能被 validate-deck 接受
    assert main(["validate-deck", str(tmp_path/"deck"), "--no-write-timings"]) == 0
```

- [ ] **Step 2: Run — expect FAIL**（未知子命令）

- [ ] **Step 3: 實作 CLI + 真 runner**

`_cmd_author_encounter(argv)`：`load_source` → 建真 runner（`IsolationRunner` + `ProbeRunner`，materialize repo_files 到 tmp、可選 `git apply`、跑單一 pytest probe 回綠紅；沿用 `validate-deck` 的既有 probe 執行路徑）→ `build_encounter` → 成功印白話結果（新關路徑 + 「bug 為真、fix 為真」結論，經 render pack），失敗印 `AuthoringError`。註冊子命令、更新 CLI help（R-16）。

- [ ] **Step 4: Run — expect PASS（或 skip 若無 bwrap）**

Run: `python3 -m pytest tests/authoring/test_author_cli.py -q`

- [ ] **Step 5: Commit**

```bash
git add patchmud/cli.py patchmud/engine/render_zh_tw.py tests/authoring/test_author_cli.py
git commit -m "feat(cli): author-encounter 子命令與真 runner 佈線"
```

---

### Task B4: 用本模組產一道示範新關 + 文件 + changelog

**Files:**
- Create: `decks/pilot-v1/<新關>/`（由 `author-encounter` 產出，來源標為 closed bug）
- Create: `docs/authoring/README.md`（出題流程：填 source.yaml → author-encounter → 凍結）
- Create: `changelog.d/encounter-authoring.md`
- Modify: `README.md`（Usage 加 `author-encounter`；R-18 docs 對齊）

- [ ] **Step 1: 寫一份真 `source.yaml`（來源：一個已修好的小 bug）並產關**

Run: `python3 -c "from patchmud.cli import main; main(['author-encounter','docs/authoring/examples/<x>.source.yaml','--into','decks/pilot-v1'])"`
Expected: 印「bug 為真、fix 為真」，新關落地。

- [ ] **Step 2: 回歸 + validate-deck + policy**

Run: `python3 -m pytest -q && python3 -c "from patchmud.cli import main; main(['validate-deck','decks/pilot-v1'])" && python3 -m policy_check --repo .`
Expected: 全綠、0 fail。

- [ ] **Step 3: README + 出題文件 + changelog**

`docs/authoring/README.md`：closed bug → deck 對應表、source.yaml 欄位、品質閘語意。
`README.md` Usage 加 `patchmud author-encounter <source.yaml> --into <deck_dir>`。
`changelog.d/encounter-authoring.md`：`feat(authoring): 結構化出題模組——把已解決的 closed bug 以 source.yaml 凍結成 deck 關卡，含 bug/fix 雙向品質閘；新增 author-encounter 子命令與示範關。`

- [ ] **Step 4: Commit + PR**

```bash
git add decks/pilot-v1 docs/authoring README.md changelog.d/encounter-authoring.md
git commit -m "feat(authoring): 示範關 + 出題文件 + changelog"
git push -u origin feature/encounter-authoring
```
PR title：`feat(authoring): 結構化出題模組`；body 不得有裸 `#N`。

## Self-Review

- 覆蓋：B1（SourceSpec/load_source）✓ B2（build_encounter + 品質閘）✓ B3（CLI + 真 runner）✓ B4（示範關 + 文件）✓。
- 型別一致：`SourceSpec` 欄位 ↔ loader 產出 ↔ builder 消費；`runner.probe_green(repo_files, apply_patch, probe_relpath)` 三處一致。
- 無 placeholder；品質閘規則明確（buggy RED / patched GREEN / apply check）。
