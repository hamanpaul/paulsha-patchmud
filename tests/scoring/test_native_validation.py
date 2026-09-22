from pathlib import Path

import pytest

from patchmud.scoring.native_validation import prepare_public_validation


HARNESS_NAMES = (
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "setup.cfg",
    "pyproject.toml",
    "sitecustomize.py",
    "usercustomize.py",
)


def _fixture(tmp_path: Path) -> tuple[dict, Path, Path]:
    fixture = tmp_path / "fixture"
    repo = fixture / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/value.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "src/deleted.py").write_text("obsolete = True\n", encoding="utf-8")
    (repo / "tests/test_public.py").write_text(
        "def test_public():\n    assert True\n", encoding="utf-8"
    )
    (repo / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
    (repo / "tests/agent").mkdir()
    (repo / "tests/agent/original.py").write_text(
        "def test_original():\n    assert True\n", encoding="utf-8"
    )
    case = {"fixture_dir": str(fixture), "test_argv": ["python3", "-m", "pytest", "-q"]}

    candidate = tmp_path / "candidate"
    (candidate / "src").mkdir(parents=True)
    (candidate / "tests/agent").mkdir(parents=True)
    return case, candidate, tmp_path / "validation"


def test_prepare_public_validation_trusts_original_tests_and_configs(tmp_path: Path):
    case, candidate, destination = _fixture(tmp_path)
    (candidate / "src/value.py").write_text("value = 2\n", encoding="utf-8")
    (candidate / "tests/test_public.py").write_text(
        "def test_public():\n    assert False\n", encoding="utf-8"
    )
    (candidate / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n", encoding="utf-8")
    (candidate / "tests/agent/test_new.py").write_text(
        "def test_new():\n    assert True\n", encoding="utf-8"
    )
    (candidate / "tests/agent/conftest.py").write_text(
        "def pytest_collection_modifyitems(*args):\n    args[1].items.clear()\n",
        encoding="utf-8",
    )
    (candidate / "sitecustomize.py").write_text("raise RuntimeError('candidate hook')\n", encoding="utf-8")

    public, argv = prepare_public_validation(case, candidate, destination)

    assert public == destination
    assert argv == ["/usr/bin/python3", "-I", "-m", "pytest", "-q", "--ignore=tests/agent"]
    assert (public / "src/value.py").read_text(encoding="utf-8") == "value = 2\n"
    assert not (public / "src/deleted.py").exists()
    assert (public / "tests/test_public.py").read_text(encoding="utf-8").endswith("assert True\n")
    assert (public / "pytest.ini").read_text(encoding="utf-8") == "[pytest]\naddopts = -q\n"
    assert (public / "tests/agent/test_new.py").is_file()
    assert not (public / "tests/agent/conftest.py").exists()
    assert not (public / "sitecustomize.py").exists()


def test_prepare_public_validation_does_not_follow_symlinks_or_candidate_git(tmp_path: Path):
    case, candidate, destination = _fixture(tmp_path)
    outside = tmp_path / "private.txt"
    outside.write_text("PRIVATE_SENTINEL\n", encoding="utf-8")
    (candidate / "src/link.py").symlink_to(outside)
    (candidate / ".git/hooks").mkdir(parents=True)
    (candidate / ".git/hooks/post-checkout").write_text(
        "raise SystemExit('must not run')\n", encoding="utf-8"
    )
    (candidate / ".pytest_cache").mkdir()
    (candidate / ".pytest_cache/secret").write_text("CACHE_SENTINEL\n", encoding="utf-8")

    public, argv = prepare_public_validation(case, candidate, destination)

    assert not (public / "src/link.py").exists()
    assert "PRIVATE_SENTINEL" not in "\n".join(
        path.read_text(encoding="utf-8")
        for path in public.rglob("*")
        if path.is_file() and ".git" not in path.parts
    )
    assert not (public / ".git/hooks/post-checkout").exists()
    assert not (public / ".pytest_cache/secret").exists()
    assert argv[-1] == "--ignore=tests/agent"


def test_prepare_public_validation_rejects_non_pytest_command(tmp_path: Path):
    case, candidate, destination = _fixture(tmp_path)
    case["test_argv"] = ["python3", "-c", "print('unsafe')"]

    with pytest.raises(ValueError, match="pytest"):
        prepare_public_validation(case, candidate, destination)
