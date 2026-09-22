from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from patchmud.adapters.base import AdapterResponse
from patchmud.sandbox.isolate import Execution
from patchmud.scoring.runner import execute_case


@dataclass
class _FakeAdapter:
    replies: list[str]
    calls: list[list[dict]]

    def complete(self, messages: list[dict]) -> AdapterResponse:
        self.calls.append(messages)
        return AdapterResponse(
            text=self.replies.pop(0),
            usage_raw={"input_tokens": 1, "output_tokens": 1},
            wall_ms=1,
        )


class _FakeIsolationRunner:
    def __init__(self, worktree: Path, calls: list[tuple[list[str], float]]) -> None:
        self.worktree = Path(worktree)
        self.calls = calls

    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
        self.calls.append((list(argv), timeout_s))
        return Execution(
            exit_code=0,
            stdout="1 passed",
            stderr="",
            wall_ms=2,
            cpu_ms=1,
            timed_out=False,
        )


class _ErrorIsolationRunner(_FakeIsolationRunner):
    def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
        self.calls.append((list(argv), timeout_s))
        return Execution(
            exit_code=2,
            stdout="ERROR collecting tests/agent/test_value.py",
            stderr="ImportError: missing dependency",
            wall_ms=2,
            cpu_ms=1,
            timed_out=False,
        )


def _case(fixture_dir: Path) -> dict:
    return {
        "id": "test-controlled",
        "depth": 1,
        "max_turns": 8,
        "wall_seconds": 600,
        "fixture_dir": str(fixture_dir),
        "prompt": "Fix the fixture.",
        "requirements": ["preserve behavior"],
        "allowed_paths": ["src/**", "tests/agent/**"],
        "stages": [{"after_turn": 1, "message": "A deterministic regression clue."}],
        "test_argv": ["python3", "-m", "pytest", "-q"],
        "rubric": {"evidence": {"instructions": "Use public observations."}},
    }


def _replies() -> list[str]:
    return [
        "ACTION: LOOK",
        "ACTION: INSPECT src/value.py",
        "ACTION: WRITE_TEST\nPATCH:\n--- /dev/null\n+++ b/tests/agent/test_value.py\n@@ -0,0 +1 @@\n+def test_value(): pass\n",
        "ACTION: RUN_TEST",
        "ACTION: PATCH\nPATCH:\n--- a/src/value.py\n+++ b/src/value.py\n@@ -1 +1 @@\n-old\n+new\n",
        "ACTION: ROLLBACK",
        "ACTION: PATCH\nPATCH:\n--- a/src/value.py\n+++ b/src/value.py\n@@ -1 +1 @@\n-old\n+new\n",
        "ACTION: COMMIT\nREPORT:\nFixed the value with evidence.\nSecond line.",
    ]


def test_execute_case_preserves_controlled_transcript_events_and_multiline_report(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo" / "src").mkdir(parents=True)
    (fixture / "hidden").mkdir()
    (fixture / "repo" / "src" / "value.py").write_text("old\n", encoding="utf-8")
    adapter = _FakeAdapter(_replies(), [])
    runner_calls: list[tuple[list[str], float]] = []

    result = execute_case(
        _case(fixture),
        adapter,
        runner_factory=lambda worktree: _FakeIsolationRunner(worktree, runner_calls),
        artifact_dir=tmp_path / "artifacts",
    )

    assert result["status"] == "completed"
    assert result["end_reason"] == "commit"
    assert result["turns"] == 8
    assert result["final_report"] == "Fixed the value with evidence.\nSecond line."
    assert len([m for m in result["transcript"] if m["role"] == "assistant"]) == 8
    assert [event["action"] for event in result["events"] if event["action"] != "STAGE"] == [
        "LOOK",
        "INSPECT",
        "WRITE_TEST",
        "RUN_TEST",
        "PATCH",
        "ROLLBACK",
        "PATCH",
        "COMMIT",
        "RUN_TEST",
    ]
    assert any(event["kind"] == "stage_evidence" for event in result["events"])
    assert result["test_results"][0]["status"] == "passed"
    assert "src/value.py" in result["final_diff"]
    assert runner_calls and all(timeout > 0 for _, timeout in runner_calls)
    initial_prompt = "\n".join(message["content"] for message in adapter.calls[0])
    assert "ACTION: PATCH\nPATCH:" in initial_prompt
    assert "tests/agent/**" in initial_prompt
    assert "Use public observations." in initial_prompt
    assert str(fixture / "hidden") not in initial_prompt


def test_execute_case_does_not_score_an_adapter_error(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo").mkdir(parents=True)
    (fixture / "hidden").mkdir()
    (fixture / "repo" / "README.txt").write_text("fixture\n", encoding="utf-8")
    adapter = _FakeAdapter([], [])

    class _ErrorAdapter:
        def complete(self, messages):
            raise RuntimeError("provider unavailable")

    result = execute_case(
        _case(fixture),
        _ErrorAdapter(),
        runner_factory=lambda worktree: _FakeIsolationRunner(worktree, []),
    )

    assert result["status"] == "error"
    assert result["error"] == "provider unavailable"
    assert result["final_report"] is None


def test_model_completion_expiry_clamps_budget_and_skips_tool_execution(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo").mkdir(parents=True)
    (fixture / "hidden").mkdir()
    (fixture / "repo" / "README.txt").write_text("fixture\n", encoding="utf-8")

    class _Clock:
        value = 0.0

        def __call__(self) -> float:
            return self.value

    clock = _Clock()
    calls: list[float] = []

    class _TimedAdapter:
        def complete_with_timeout(self, messages, timeout_s):
            calls.append(timeout_s)
            clock.value = 2.0
            return AdapterResponse("ACTION: PATCH\nPATCH:\nnot used", {}, 2000)

    runner_calls: list[tuple[list[str], float]] = []
    case = {**_case(fixture), "wall_seconds": 2}
    result = execute_case(
        case,
        _TimedAdapter(),
        runner_factory=lambda worktree: _FakeIsolationRunner(worktree, runner_calls),
        clock=clock,
    )

    assert result["status"] == "budget_exhausted"
    assert result["end_reason"] == "wall_clock"
    assert calls == [2.0]
    assert runner_calls == []


def test_adapter_timeout_keeps_partial_work_judgeable(tmp_path: Path) -> None:
    fixture = tmp_path/'fixture'
    (fixture/'repo').mkdir(parents=True)
    (fixture/'repo/README.txt').write_text('public fixture\n')
    class TimedOutAdapter:
        def complete_with_timeout(self, messages, timeout_s):
            error = TimeoutError('provider call exhausted remaining case time')
            error.timed_out = True
            raise error
    result = execute_case(_case(fixture), TimedOutAdapter(),
                          runner_factory=lambda worktree: _FakeIsolationRunner(worktree, []))
    assert result['status'] == 'budget_exhausted'
    assert result['end_reason'] == 'wall_clock'
    assert result['error'] is None
    assert result['turns'] == 1
    assert result['usage'] == [{'turn': 1, 'status': 'error', 'raw': None}]


def test_patch_scope_and_original_tests_are_immutable(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo" / "src").mkdir(parents=True)
    (fixture / "repo" / "tests").mkdir()
    (fixture / "hidden").mkdir()
    (fixture / "repo" / "src" / "value.py").write_text("old\n", encoding="utf-8")
    (fixture / "repo" / "tests" / "test_public.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    replies = [
        "ACTION: PATCH\nPATCH:\n--- a/tests/test_public.py\n+++ b/tests/test_public.py\n@@ -1 +1 @@\n-def test_ok(): pass\n+def test_ok(): assert False\n",
        "ACTION: COMMIT\nREPORT:\nNo fixture tests changed.",
    ]
    result = execute_case(
        {**_case(fixture), "max_turns": 2},
        _FakeAdapter(replies, []),
        runner_factory=lambda worktree: _FakeIsolationRunner(worktree, []),
    )

    patch_event = next(event for event in result["events"] if event["action"] == "PATCH")
    assert patch_event["status"] == "failed"
    assert "immutable" in patch_event["output"]["reason"]
    assert result["final_diff"] == ""


def test_pytest_collection_errors_are_error_status_not_failed(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo").mkdir(parents=True)
    (fixture / "hidden").mkdir()
    (fixture / "repo" / "README.txt").write_text("fixture\n", encoding="utf-8")
    calls: list[tuple[list[str], float]] = []
    result = execute_case(
        {**_case(fixture), "max_turns": 2},
        _FakeAdapter(["ACTION: RUN_TEST", "ACTION: COMMIT"], []),
        runner_factory=lambda worktree: _ErrorIsolationRunner(worktree, calls),
    )

    assert result["status"] == "completed"
    assert result["test_results"][0]["status"] == "error"


def test_rollback_recreates_writable_agent_test_mount_before_run(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo" / "tests").mkdir(parents=True)
    (fixture / "hidden").mkdir(parents=True)
    (fixture / "repo" / "tests" / "test_public.py").write_text(
        "def test_public(): pass\n", encoding="utf-8"
    )
    replies = [
        "ACTION: WRITE_TEST\nPATCH:\n--- /dev/null\n+++ b/tests/agent/test_value.py\n@@ -0,0 +1 @@\n+def test_value(): pass\n",
        "ACTION: ROLLBACK",
        "ACTION: RUN_TEST",
        "ACTION: COMMIT",
    ]

    class _MountCheckingRunner(_FakeIsolationRunner):
        def run(self, argv: list[str], cwd: Path, timeout_s: float) -> Execution:
            assert (cwd / "tests" / "agent").is_dir()
            return super().run(argv, cwd, timeout_s)

    result = execute_case(
        {**_case(fixture), "max_turns": 4},
        _FakeAdapter(replies, []),
        runner_factory=lambda worktree: _MountCheckingRunner(worktree, []),
    )

    assert result["status"] == "completed"
    assert len(result["test_results"]) == 2


def test_keyboard_interrupt_returns_archivable_partial_record(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo").mkdir(parents=True)
    (fixture / "hidden").mkdir()
    (fixture / "repo" / "README.txt").write_text("fixture\n", encoding="utf-8")

    class _Interrupted:
        def complete(self, messages):
            error = KeyboardInterrupt()
            error.usage_raw = {"input_tokens": 3}
            raise error

    result = execute_case(
        {**_case(fixture), "max_turns": 1},
        _Interrupted(),
        runner_factory=lambda worktree: _FakeIsolationRunner(worktree, []),
    )

    assert result["status"] == "error"
    assert result["end_reason"] == "interrupted"
    assert result["interrupted"] is True
    assert result["turns"] == 1
    assert result["usage"][0]["raw"] == {"input_tokens": 3}


def test_keyboard_interrupt_keeps_partial_diff_before_worktree_cleanup(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "repo" / "src").mkdir(parents=True)
    (fixture / "hidden").mkdir(parents=True)
    (fixture / "repo" / "src" / "value.py").write_text("old\n", encoding="utf-8")

    class _InterruptedAfterPatch:
        def __init__(self) -> None:
            self.turns = 0

        def complete(self, messages):
            self.turns += 1
            if self.turns == 1:
                return AdapterResponse(
                    "ACTION: PATCH\nPATCH:\n--- a/src/value.py\n+++ b/src/value.py\n@@ -1 +1 @@\n-old\n+new\n",
                    {},
                    1,
                )
            raise KeyboardInterrupt

    result = execute_case(
        {**_case(fixture), "max_turns": 2},
        _InterruptedAfterPatch(),
        runner_factory=lambda worktree: _FakeIsolationRunner(worktree, []),
    )

    assert result["status"] == "error"
    assert result["end_reason"] == "interrupted"
    assert "src/value.py" in result["final_diff"]
