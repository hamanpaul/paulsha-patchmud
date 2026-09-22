"""Bounded real-process integration for CLI descendant cleanup; no model/API."""

import os
from pathlib import Path
import sys
import time

import pytest

from patchmud.adapters.base import AdapterError
from patchmud.adapters.cli_base import build_subprocess_runner
from patchmud.sandbox.isolate import IsolationRunner, build_bwrap_argv


def _live(pid: int) -> bool:
    stat = Path(f'/proc/{pid}/stat')
    if not stat.exists():
        return False
    # A reparented zombie is terminated; container init may reap it later.
    try:
        return stat.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


@pytest.mark.skipif(os.name != 'posix' or not Path('/proc').is_dir(), reason='Linux process-group integration')
def test_cli_timeout_terminates_descendant_process(tmp_path):
    pidfile = tmp_path/'child.pid'
    script = (
        'import subprocess,sys,time\n'
        'from pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
        'Path(sys.argv[1]).write_text(str(child.pid))\n'
        'time.sleep(60)\n'
    )
    runner = build_subprocess_runner(timeout_s=1)
    try:
        with pytest.raises(AdapterError):
            runner([sys.executable, '-c', script, str(pidfile)])
        assert pidfile.exists(), 'child must start so the cleanup assertion is meaningful'
        child_pid = int(pidfile.read_text())
        deadline = time.monotonic() + 2
        while _live(child_pid) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not _live(child_pid), 'timed-out model CLI left a running descendant'
    finally:
        if pidfile.exists():
            child_pid = int(pidfile.read_text())
            if _live(child_pid):
                os.kill(child_pid, 9)


def test_candidate_interruption_reaps_process_before_propagating(monkeypatch, tmp_path):
    events = []
    class FakeProcess:
        pid = 12345
        def communicate(self, timeout=None):
            events.append(('communicate', timeout))
            if timeout is not None:
                raise KeyboardInterrupt
            return '', ''
    monkeypatch.setattr('patchmud.sandbox.isolate.subprocess.Popen', lambda *a, **k: FakeProcess())
    monkeypatch.setattr('patchmud.sandbox.isolate._kill_process_group', lambda pid: events.append(('kill', pid)))
    with pytest.raises(KeyboardInterrupt):
        IsolationRunner(tmp_path).run(['python3', '-m', 'pytest'], cwd=tmp_path, timeout_s=10)
    assert events == [('communicate', 10), ('kill', 12345), ('communicate', None)]


def test_installed_engine_is_masked_after_toolchain_mounts():
    package = Path('/usr/local/lib/python3.11/site-packages/patchmud')
    argv = build_bwrap_argv(Path('/work/repo'), [Path('/usr')], ['python3'],
                            masked_paths=[package])
    mask_at = argv.index(str(package))
    assert argv[mask_at - 1] == '--tmpfs'
    assert mask_at > argv.index('--ro-bind')
    # A source checkout outside mounted toolchains is already invisible.
    outside = Path('/workspace/operator/project/patchmud')
    argv = build_bwrap_argv(Path('/work/repo'), [Path('/usr')], ['python3'],
                            masked_paths=[outside])
    assert str(outside) not in argv


def test_git_and_fixture_tests_are_readonly_with_only_agent_tests_writable():
    repo = Path('/work/repo')
    argv = build_bwrap_argv(repo, [Path('/usr')], ['python3'],
                            protected_paths=[repo/'.git', repo/'tests'],
                            writable_paths=[repo/'tests/agent'])
    bind_at = argv.index('--bind')
    git_at = argv.index(str(repo/'.git'))
    tests_at = argv.index(str(repo/'tests'))
    agent_at = argv.index(str(repo/'tests/agent'))
    assert argv[git_at - 1] == '--ro-bind'
    assert argv[tests_at - 1] == '--ro-bind'
    assert argv[agent_at - 1] == '--bind'
    assert bind_at < git_at < tests_at < agent_at
