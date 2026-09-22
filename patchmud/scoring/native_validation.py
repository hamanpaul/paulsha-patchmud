"""Build an independent public-test checkout for native scoring.

The native agent's checkout is not trusted as a pytest test harness.  Public
validation starts from the fixture bytes and overlays candidate source files;
fixture tests and Python/pytest startup configuration stay at their trusted
versions.  This module only prepares files and an argv list.  Execution still
belongs to :class:`patchmud.sandbox.isolate.IsolationRunner`.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from patchmud.deck.materialize import materialize_repo
from patchmud.sandbox.workspace import HARNESS_CONFIG_NAMES

__all__ = ["prepare_public_validation"]


_IGNORED_DIRS = frozenset({".git", "__pycache__", ".pytest_cache"})


def _regular_files(root: Path) -> dict[str, Path]:
    """Return regular files below *root* without traversing symlinks."""

    files: dict[str, Path] = {}
    root = Path(root)
    for directory, directories, names in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        directories[:] = [
            name
            for name in directories
            if name not in _IGNORED_DIRS and not (parent / name).is_symlink()
        ]
        for name in names:
            path = parent / name
            if name.endswith(".pyc") or path.is_symlink():
                continue
            try:
                mode = path.stat(follow_symlinks=False).st_mode
            except OSError:
                continue
            if not stat.S_ISREG(mode):
                continue
            relative = path.relative_to(root).as_posix()
            files[relative] = path
    return files


def _is_fixture_test(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return bool(parts and parts[0] == "tests")


def _is_agent_test(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return len(parts) >= 2 and parts[:2] == ("tests", "agent")


def _is_harness_config(relative: str) -> bool:
    return PurePosixPath(relative).name in HARNESS_CONFIG_NAMES


def _copy_regular_file(source: Path, destination: Path) -> None:
    """Copy one candidate regular file without following a source symlink."""

    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_stream = os.fdopen(source_fd, "rb")
        source_fd = None
        destination_fd: int | None = None
        try:
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                stat.S_IMODE(source_stat.st_mode) or 0o644,
            )
            destination_stream = os.fdopen(destination_fd, "wb")
            destination_fd = None
            try:
                shutil.copyfileobj(source_stream, destination_stream)
            finally:
                destination_stream.close()
        finally:
            source_stream.close()
            if destination_fd is not None:
                os.close(destination_fd)
        os.chmod(destination, stat.S_IMODE(source_stat.st_mode))
    except BaseException:
        if source_fd is not None:
            try:
                os.close(source_fd)
            except OSError:
                pass
        raise


def _pytest_argv(case: Mapping[str, Any]) -> list[str]:
    raw = case.get("test_argv")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ValueError("public validation requires a pytest test_argv")
    original = list(raw)
    if not original or not all(isinstance(item, str) and item for item in original):
        raise ValueError("public validation requires a non-empty pytest test_argv")
    if original[0] not in {"python", "python3", "/usr/bin/python3"}:
        raise ValueError("public validation requires a Python pytest command")
    try:
        module_index = original.index("-m")
    except ValueError as exc:
        raise ValueError("public validation requires -m pytest") from exc
    if module_index + 1 >= len(original) or original[module_index + 1] != "pytest":
        raise ValueError("public validation requires -m pytest")
    pytest_args = original[module_index + 2 :]
    pytest_args = [argument for argument in pytest_args if argument != "--ignore=tests/agent"]
    return ["/usr/bin/python3", "-I", "-m", "pytest", *pytest_args, "--ignore=tests/agent"]


def prepare_public_validation(
    case: Mapping[str, Any], candidate: Path, destination: Path
) -> tuple[Path, list[str]]:
    """Materialize trusted fixture bytes and overlay candidate source files.

    The candidate checkout is read as data only; its Git metadata, cache files,
    symlinks and harness configuration are never copied or executed.  Missing
    non-test, non-harness source files retain candidate deletion semantics.
    """

    candidate = Path(candidate)
    destination = Path(destination)
    if not candidate.is_dir():
        raise ValueError(f"candidate checkout is not a directory: {candidate}")

    # The only Git operation here is materializing the trusted fixture into a
    # fresh destination.  No command is run with cwd or metadata from candidate.
    materialize_repo(Path(case["fixture_dir"]), destination)
    original_files = _regular_files(destination)
    candidate_files = _regular_files(candidate)

    # Deletions are reflected for source files, while original fixture tests
    # and all original harness files remain immutable trusted bytes.
    for relative in sorted(set(original_files) - set(candidate_files)):
        if _is_fixture_test(relative) or _is_harness_config(relative):
            continue
        path = destination / relative
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    for relative, source in sorted(candidate_files.items()):
        # New ordinary tests/agent files are evidence-only and may be copied;
        # any fixture test that already existed remains trusted.  All other
        # tests and every harness config stay at their original bytes.
        if _is_harness_config(relative) or (
            _is_fixture_test(relative)
            and (not _is_agent_test(relative) or relative in original_files)
        ):
            continue
        _copy_regular_file(source, destination / relative)

    return destination, _pytest_argv(case)
