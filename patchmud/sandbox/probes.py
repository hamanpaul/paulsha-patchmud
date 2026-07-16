"""三態 probe runner 與 turn-0 baseline（spec §5.2、§7、§8.1）。

- probe 判定只有 `passed / failed / error` 三態；`error`（collection、import、
  timeout、報告缺失）永不等同 `failed`（plan invariant 1，TDD valid red 依賴）。
- 任何 probe 執行前先 `workspace.restore_protected()`：以 deck 原始 bytes 還原
  保護區後才進沙箱（plan invariant 2，杜絕改測試過關）。
- 執行一律經注入的 `IsolationRunner` seam（plan invariant 3）；pytest 以
  `--junitxml` 在隔離內落地結構化報告（寫進 worktree rw bind、讀後即刪），
  解析三態與 case 級結果。
- `ProbeResults.transitions(prev)`：以 green = `passed` 判定綠→紅／紅→綠；
  `failed` 與 `error` 都是「不綠」，但三態原值完整保存在 `ProbeOutcome.status`。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from patchmud.deck.model import IssueCard
from patchmud.sandbox.isolate import Execution

__all__ = [
    "DEFAULT_PYTEST_ARGV",
    "Probe",
    "ProbeError",
    "ProbeOutcome",
    "ProbeResults",
    "ProbeSuite",
    "Transition",
]

ProbeStatus = Literal["passed", "failed", "error"]
TransitionChange = Literal["green_to_red", "red_to_green"]

#: 沙箱內的 pytest 起手式；toolchain 佈局特殊時（如 user-site pytest）可注入
#: bootstrap 形式覆寫（見 tests 的 `_sandbox_pytest_argv`）。
DEFAULT_PYTEST_ARGV: tuple[str, ...] = ("python3", "-B", "-m", "pytest")

#: junitxml 報告在 worktree 內的落點；讀後即刪，不留進 diff。
_REPORT_RELPATH = ".patchmud-probe-report.xml"

_PYTEST_FLAGS: tuple[str, ...] = ("-q", "-p", "no:cacheprovider")

_DEFAULT_TIMEOUT_S = 120.0


class ProbeError(Exception):
    """probe 套件層錯誤（未知 subset id 等），fail-closed。"""


class _Runner(Protocol):
    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution: ...


class _Workspace(Protocol):
    @property
    def worktree(self) -> Path: ...

    def restore_protected(self) -> None: ...


@dataclass(frozen=True)
class Probe:
    """一個可執行的公開診斷（spec §3）。"""

    probe_id: str
    kind: Literal["requirement", "regression", "compat", "smoke"]
    target: str | None = None
    smoke: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ProbeOutcome:
    """單一 probe 的三態判定與 case 級觀測。"""

    status: ProbeStatus
    cases_total: int
    cases_passed: int
    failure_fingerprints: tuple[str, ...]
    wall_ms: int
    cpu_ms: int


@dataclass(frozen=True)
class Transition:
    probe_id: str
    change: TransitionChange


class ProbeResults(Mapping[str, ProbeOutcome]):
    """一次 probe 執行的全部 outcome（保序、不可變視圖）。"""

    def __init__(self, outcomes: Mapping[str, ProbeOutcome]) -> None:
        self._outcomes: dict[str, ProbeOutcome] = dict(outcomes)

    def __getitem__(self, probe_id: str) -> ProbeOutcome:
        return self._outcomes[probe_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._outcomes)

    def __len__(self) -> int:
        return len(self._outcomes)

    @property
    def probe_ids(self) -> tuple[str, ...]:
        return tuple(self._outcomes)

    def transitions(self, prev: "ProbeResults") -> list[Transition]:
        """相對前次結果的綠→紅／紅→綠（green = status == "passed"）。"""
        out: list[Transition] = []
        for probe_id, cur in self._outcomes.items():
            if probe_id not in prev:
                continue
            was_green = prev[probe_id].status == "passed"
            now_green = cur.status == "passed"
            if was_green and not now_green:
                out.append(Transition(probe_id, "green_to_red"))
            elif not was_green and now_green:
                out.append(Transition(probe_id, "red_to_green"))
        return out


class ProbeSuite:
    """card 宣告的全套 public probes（requirements、regression、compat）。"""

    def __init__(
        self,
        probes: Sequence[Probe],
        runner: _Runner,
        *,
        pytest_argv: Sequence[str] = DEFAULT_PYTEST_ARGV,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._probes = tuple(probes)
        self._runner = runner
        self._pytest_argv = tuple(pytest_argv)
        self._timeout_s = timeout_s

    @classmethod
    def from_card(cls, card: IssueCard, runner: _Runner, **kwargs) -> "ProbeSuite":
        probes: list[Probe] = []
        for req in card.public_requirements:
            probes.append(Probe(probe_id=req.probe, kind="requirement", target=req.probe))
        for rp in card.regression_probes:
            if rp.path is not None:
                probes.append(Probe(probe_id=rp.path, kind="regression", target=rp.path))
            elif rp.smoke is not None:
                probes.append(
                    Probe(
                        probe_id="smoke:" + " ".join(rp.smoke),
                        kind="smoke",
                        smoke=tuple(rp.smoke),
                    )
                )
        for cp in card.compat_probes:
            probes.append(Probe(probe_id=cp.probe, kind="compat", target=cp.probe))

        deduped: dict[str, Probe] = {}
        for probe in probes:
            deduped.setdefault(probe.probe_id, probe)
        return cls(tuple(deduped.values()), runner, **kwargs)

    @property
    def probe_ids(self) -> tuple[str, ...]:
        return tuple(p.probe_id for p in self._probes)

    def run(
        self, workspace: _Workspace, subset: Iterable[str] | None = None
    ) -> ProbeResults:
        wanted: set[str] | None = None
        if subset is not None:
            wanted = set(subset)
            unknown = wanted - set(self.probe_ids)
            if unknown:
                raise ProbeError(f"未知 probe id：{', '.join(sorted(unknown))}")

        # 任何執行前先以 deck 原始 bytes 還原保護區（spec §7）
        workspace.restore_protected()

        worktree = Path(workspace.worktree)
        outcomes: dict[str, ProbeOutcome] = {}
        for probe in self._probes:
            if wanted is not None and probe.probe_id not in wanted:
                continue
            outcomes[probe.probe_id] = self._run_one(probe, worktree)
        return ProbeResults(outcomes)

    # ---- 單一 probe 執行 ----------------------------------------------------

    def _run_one(self, probe: Probe, worktree: Path) -> ProbeOutcome:
        if probe.smoke is not None:
            return self._run_smoke(probe, worktree)
        return self._run_pytest(probe, worktree)

    def _run_smoke(self, probe: Probe, worktree: Path) -> ProbeOutcome:
        assert probe.smoke is not None
        ex = self._runner.run(list(probe.smoke), cwd=worktree, timeout_s=self._timeout_s)
        if ex.timed_out:
            return _outcome("error", 1, 0, ("timeout",), ex)
        if ex.exit_code == 0:
            return _outcome("passed", 1, 1, (), ex)
        fingerprint = _last_nonempty_line(ex.stderr) or f"exit {ex.exit_code}"
        return _outcome("failed", 1, 0, (fingerprint,), ex)

    def _run_pytest(self, probe: Probe, worktree: Path) -> ProbeOutcome:
        assert probe.target is not None
        report_path = worktree / _REPORT_RELPATH
        argv = [
            *self._pytest_argv,
            *_PYTEST_FLAGS,
            f"--junitxml={_REPORT_RELPATH}",
            probe.target,
        ]
        try:
            ex = self._runner.run(argv, cwd=worktree, timeout_s=self._timeout_s)
            xml_text = (
                report_path.read_text(encoding="utf-8") if report_path.exists() else None
            )
        finally:
            report_path.unlink(missing_ok=True)

        if ex.timed_out:
            return _outcome("error", 0, 0, ("timeout",), ex)
        if xml_text is None:
            fingerprint = _last_nonempty_line(ex.stderr) or f"exit {ex.exit_code}"
            return _outcome("error", 0, 0, (fingerprint,), ex)
        return _parse_junitxml(xml_text, ex)


def _outcome(
    status: ProbeStatus,
    cases_total: int,
    cases_passed: int,
    fingerprints: tuple[str, ...],
    ex: Execution,
) -> ProbeOutcome:
    return ProbeOutcome(
        status=status,
        cases_total=cases_total,
        cases_passed=cases_passed,
        failure_fingerprints=fingerprints,
        wall_ms=ex.wall_ms,
        cpu_ms=ex.cpu_ms,
    )


def _parse_junitxml(xml_text: str, ex: Execution) -> ProbeOutcome:
    """junitxml → 三態與 case 級結果；解析不了一律 error（fail-closed）。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return _outcome("error", 0, 0, ("junitxml 解析失敗",), ex)

    cases_total = 0
    cases_passed = 0
    error_fingerprints: list[str] = []
    failure_fingerprints: list[str] = []
    for case in root.iter("testcase"):
        cases_total += 1
        errors = case.findall("error")
        failures = case.findall("failure")
        skipped = case.findall("skipped")
        if errors:
            error_fingerprints.extend(_fingerprint(e) for e in errors)
        elif failures:
            failure_fingerprints.extend(_fingerprint(f) for f in failures)
        elif not skipped:
            cases_passed += 1

    if error_fingerprints:
        status: ProbeStatus = "error"
    elif failure_fingerprints:
        status = "failed"
    elif cases_total == 0:
        status = "error"  # 收集不到任何 case，無法宣稱綠
    elif ex.exit_code == 0:
        status = "passed"
    else:
        status = "error"  # 報告全綠但 exit 非零：狀態不可信，fail-closed

    return _outcome(
        status,
        cases_total,
        cases_passed,
        tuple(error_fingerprints + failure_fingerprints),
        ex,
    )


def _fingerprint(elem: ET.Element) -> str:
    """異常類型＋斷言訊息首行（junitxml message 首行；退回 body 首行）。"""
    for source in (elem.get("message"), elem.text):
        if source and source.strip():
            return source.strip().splitlines()[0]
    return "unknown failure"


def _last_nonempty_line(text: str) -> str | None:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return None
