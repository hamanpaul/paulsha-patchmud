"""Run native coding CLIs inside a disposable public workspace."""
from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
import difflib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Callable, Mapping

from patchmud.deck.materialize import materialize_repo
from patchmud.sandbox.isolate import IsolationRunner
from patchmud.sandbox.workspace import HARNESS_CONFIG_NAMES
from .runner import _classify_test_execution
from .native_validation import prepare_public_validation

NATIVE_PROTOCOL_VERSION = 'native-engineering-v1'
NATIVE_EXECUTION_MODE = 'native-coding-agent'
NATIVE_POLICY = {
    'protocol': NATIVE_PROTOCOL_VERSION,
    'tools': 'native-cli',
    'network': 'provider-and-native-tools',
    'home': 'isolated-auth-only',
    'phases': 'sequential-native-conversation-equal-wall-shares-v1',
    'turn_limit': None,
    'validation_reserve_seconds': 30,
}
_IGNORED_DIRS = {'.git', '__pycache__', '.pytest_cache'}
_MAX_FILE_BYTES = 4 * 1024 * 1024


def _file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _runtime_bundle(executable, harness):
    """Identify installed native helpers as part of the runtime fingerprint."""
    executable = Path(executable)
    package_root = executable.parent.parent
    if harness == 'codex' and (package_root/'codex-package.json').is_file():
        return {'root': str(package_root), 'files': [
            {'path': path.relative_to(package_root).as_posix(), 'sha256': _file_hash(path)}
            for path in sorted(package_root.rglob('*')) if path.is_file()]}
    return {'root': str(executable.parent), 'files': [
        {'path': executable.name, 'sha256': _file_hash(executable)}]}


@dataclass
class NativeAgentAdapter:
    profile: dict
    timeout_s: float = 600


def resolve_native_profile(harness, model, effort, *, catalog=None):
    from .runner import resolve_profile
    profile = resolve_profile(harness, model, effort, catalog=catalog)
    harness = profile['resolved']['harness']
    capability = profile['capability']
    if harness == 'codex':
        auth_root = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))).expanduser()
        authenticated = (auth_root/'auth.json').is_file()
    else:
        authenticated = (Path.home()/'.gemini/antigravity-cli/antigravity-oauth-token').is_file()
    profile.update(execution_mode=NATIVE_EXECUTION_MODE, protocol_version=NATIVE_PROTOCOL_VERSION,
                   execution_policy=dict(NATIVE_POLICY),
                   harness_version=f'{harness}-cli-native-v1:{capability["executable_version"] or "unknown"}')
    if capability['runtime_identity_known']:
        capability['runtime_bundle'] = _runtime_bundle(capability['executable'], harness)
    if harness == 'agy' and catalog is None and authenticated and capability['runtime_identity_known']:
        capability['model_verified'] = _agy_catalog_confirms(profile)
        capability['catalog'] = 'native-models-command' if capability['model_verified'] else 'native-models-unconfirmed'
    supported = bool(capability['runtime_identity_known'] and capability['model_verified'] and authenticated)
    capability.update(native_supported=supported, native_tools_allowed=True,
                      authentication_available=authenticated, cacheable=supported)
    return profile


def _agy_catalog_confirms(profile):
    """Query the installed, authenticated CLI before any model execution."""
    with tempfile.TemporaryDirectory(prefix='patchmud-native-catalog-') as temp:
        root = Path(temp)
        worktree = root/'worktree'
        worktree.mkdir()
        with NativeSession(profile, worktree, root/'state') as session:
            execution = session.runner.run([session.executable, 'models'], cwd=worktree, timeout_s=45)
            if execution.exit_code != 0 or execution.timed_out:
                return False
            models = {line.split()[0] for line in execution.stdout.splitlines() if line.strip()}
            return profile['resolved']['model'] in models


def build_native_adapter(profile, *, timeout_s=600):
    if profile.get('capability', {}).get('native_supported') is not True:
        raise ValueError('native harness capability or authentication is unavailable')
    return NativeAgentAdapter(profile, timeout_s)


def native_public_case(case):
    from .cases import public_case_snapshot
    public = public_case_snapshot(case)
    public['max_turns'] = None
    public['execution_policy'] = dict(NATIVE_POLICY)
    public['stages'] = [{'phase': index, 'message': stage['message']}
                        for index, stage in enumerate(sorted(case.get('stages', []), key=lambda s: s['after_turn']), 2)]
    return public


def _secret_strings(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_secret_strings(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_secret_strings(v) for v in value)) if value else set()
    return {value} if isinstance(value, str) and len(value) >= 12 else set()


def prepare_home(harness: str, home: Path, *, source_home: Path | None = None) -> Callable:
    """Copy only provider authentication into private disposable CLI state."""
    source = Path(source_home or Path.home())
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    secrets = {os.environ['TYPESAFE_API_KEY']} if os.environ.get('TYPESAFE_API_KEY') else set()
    if harness == 'codex':
        auth_root = source/'.codex'
        if source_home is None and os.environ.get('CODEX_HOME'):
            auth_root = Path(os.environ['CODEX_HOME']).expanduser()
        auth_files = [auth_root/'auth.json']
        destination = home/'.codex'
    elif harness == 'agy':
        auth_root = source/'.gemini'
        auth_files = [auth_root/'antigravity-cli/antigravity-oauth-token']
        if (auth_root/'oauth_creds.json').is_file():
            auth_files.append(auth_root/'oauth_creds.json')
        destination = home/'.gemini'
    else:
        raise ValueError('unsupported native harness')
    if not auth_files or any(not p.is_file() for p in auth_files):
        raise ValueError(f'{harness} authentication is unavailable')
    destination.mkdir(mode=0o700)
    for path in auth_files:
        content = path.read_bytes()
        try:
            secrets.update(_secret_strings(json.loads(content)))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f'{harness} authentication is not valid JSON') from exc
        target = destination/path.relative_to(auth_root)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_bytes(content)
        target.chmod(0o600)
    if harness == 'codex' and (auth_root/'models_cache.json').is_file():
        shutil.copyfile(auth_root/'models_cache.json', destination/'models_cache.json')
    ordered = sorted(secrets, key=len, reverse=True)

    def redact(value):
        if isinstance(value, str):
            for secret in ordered:
                value = value.replace(secret, '[redacted]')
            return value
        if isinstance(value, dict):
            return {str(k): redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [redact(v) for v in value]
        return value
    return redact


def capture_workspace(worktree: Path, before: Mapping[str, str]) -> dict:
    """Read regular files without trusting or running candidate Git metadata."""
    after, manifest, symlinks = {}, [], []
    for directory, dirs, files in os.walk(worktree, followlinks=False):
        parent = Path(directory)
        for name in list(dirs):
            path = parent/name
            if name in _IGNORED_DIRS:
                dirs.remove(name)
            elif path.is_symlink():
                symlinks.append(path.relative_to(worktree).as_posix())
                dirs.remove(name)
        for name in files:
            if name.endswith('.pyc'):
                continue
            path = parent/name
            rel = path.relative_to(worktree).as_posix()
            if path.is_symlink():
                symlinks.append(rel)
                continue
            if not path.is_file():
                raise ValueError('native workspace contains an unsupported special file')
            if path.stat().st_size > _MAX_FILE_BYTES:
                raise ValueError(f'native artifact exceeds file evidence limit: {rel}')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as handle:
                data = handle.read(_MAX_FILE_BYTES + 1)
            if len(data) > _MAX_FILE_BYTES:
                raise ValueError(f'native artifact exceeds file evidence limit: {rel}')
            manifest.append({'path': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            try:
                after[rel] = data.decode('utf-8')
            except UnicodeError:
                after[rel] = f'[binary artifact sha256={hashlib.sha256(data).hexdigest()}]\n'
    changes, chunks = [], []
    for rel in sorted(set(before) | set(after)):
        old, new = before.get(rel, ''), after.get(rel, '')
        if old == new:
            continue
        changes.append(rel)
        chunks.extend(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                         fromfile=f'a/{rel}' if rel in before else '/dev/null',
                                         tofile=f'b/{rel}' if rel in after else '/dev/null'))
    for rel in sorted(symlinks):
        changes.append(rel)
        chunks.append(f'Unsupported symlink artifact: {rel}\n')
    return {'final_diff': ''.join(chunks), 'changed_paths': sorted(set(changes)),
            'files': sorted(manifest, key=lambda x: x['path']), 'symlinks': sorted(symlinks),
            'contents': after}


def _protections(worktree: Path):
    protected, writable = [], []
    tests = worktree/'tests'
    if tests.is_dir():
        (tests/'agent').mkdir(exist_ok=True)
        protected.append(tests)
        writable.append(tests/'agent')
    for path in worktree.rglob('*'):
        if path.is_file() and path.name in HARNESS_CONFIG_NAMES:
            protected.append(path)
    return tuple(dict.fromkeys(protected)), tuple(writable)


class NativeSession(AbstractContextManager):
    def __init__(self, profile, worktree, state_root):
        self.profile, self.worktree, self.state_root = profile, worktree, state_root
        self.redact = lambda value: value

    def __enter__(self):
        harness = self.profile['resolved']['harness']
        self.redact = prepare_home(harness, self.state_root/'home')
        executable = Path(self.profile['capability']['executable'])
        expected_hash = self.profile['capability'].get('executable_sha256')
        if expected_hash and _file_hash(executable) != expected_hash:
            raise ValueError('native CLI executable changed after profile resolution')
        self.executable = f'/opt/patchmud-native/bin/{harness}'
        binds = [(executable, Path(self.executable))]
        bundle = self.profile['capability'].get('runtime_bundle')
        if bundle:
            runtime_root = Path(bundle['root'])
            for item in bundle['files']:
                path = runtime_root/item['path']
                if _file_hash(path) != item['sha256']:
                    raise ValueError('native runtime helper changed after profile resolution')
            if harness == 'codex' and (runtime_root/'codex-package.json').is_file():
                binds = [(runtime_root, Path('/opt/patchmud-native'))]
        for name in ('/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf', '/etc/ssl/certs'):
            path = Path(name)
            if path.exists():
                binds.append((path.resolve(), path))
        protected, writable = _protections(self.worktree)
        env = {'HOME': '/agent-home', 'CODEX_HOME': '/agent-home/.codex',
               'XDG_CONFIG_HOME': '/agent-home/.config', 'XDG_STATE_HOME': '/agent-home/.local/state',
               'XDG_CACHE_HOME': '/agent-home/.cache', 'PATH': '/opt/patchmud-native/bin:/usr/bin:/bin',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_SYSTEM': '/dev/null',
               'GIT_AUTHOR_NAME': 'Benchmark Agent', 'GIT_AUTHOR_EMAIL': 'agent@patchmud.invalid',
               'GIT_COMMITTER_NAME': 'Benchmark Agent', 'GIT_COMMITTER_EMAIL': 'agent@patchmud.invalid'}
        self.runner = IsolationRunner(self.worktree, network_access=True, ro_bindings=binds,
                                      rw_bindings=[(self.state_root/'home', Path('/agent-home'))],
                                      sandbox_env=env, protected_paths=protected, writable_paths=writable,
                                      masked_paths=[Path(__file__).resolve().parents[1]])
        capabilities = self.runner.capabilities()
        if not (capabilities.mount_ns and capabilities.pid_ns and capabilities.net_ns):
            raise ValueError('native CLI namespace isolation is unavailable')
        return self

    def __exit__(self, *args):
        return False

    def run_phase(self, prompt, timeout_s, session_id=None):
        from .native_cli import build_native_argv, parse_native_execution
        harness = self.profile['resolved']['harness']
        argv = build_native_argv(self.profile, workspace=self.worktree,
                                 executable=self.executable, session_id=session_id)
        input_text = (json.dumps({'event': 'user', 'message': {'content': prompt}}, ensure_ascii=False)+'\n'
                      if harness == 'agy' else prompt)
        try:
            execution = self.runner.run(argv, cwd=self.worktree, timeout_s=timeout_s, input_text=input_text)
        except KeyboardInterrupt as exc:
            execution = getattr(exc, 'execution_result', None)
            if execution is not None:
                result = self.redact(parse_native_execution(harness, execution))
                result.update(status='error', end_reason='interrupted', error='interrupted by user', interrupted=True)
                return result
            raise
        return self.redact(parse_native_execution(harness, execution))


def _prompt(case: Mapping[str, Any], stage: str | None = None) -> str:
    if stage is not None:
        return ('Continue in the same repository. New authoritative task information:\n'
                + stage + '\nImplement or diagnose the updated request and report actual verification evidence.')
    rubric = json.dumps(case.get('rubric', {}), ensure_ascii=False)
    requirements = '\n'.join('- '+x for x in case.get('requirements', []))
    allowed = ', '.join(case.get('allowed_paths', [])) or '(no file changes allowed)'
    return (f"{case['prompt']}\n\nRequirements:\n{requirements}\n\nAllowed changes: {allowed}\n"
            'Use your native tools to inspect and work in this repository. Original fixture tests and '
            'harness configuration are read-only. Only change the allowed paths above; add tests under '
            'tests/agent only when those paths are explicitly allowed. Your disposable Git '
            'repository is writable for checkpoints and rollback. Do not claim checks you did not run. '
            'Finish this interaction with your result, evidence and remaining uncertainty. '
            'Additional user information may follow in this same workspace.\n\nQuality rubric:\n'+rubric)


def _test_runner(worktree):
    protected, writable = _protections(worktree)
    return IsolationRunner(worktree, protected_paths=protected, writable_paths=writable,
                           masked_paths=[Path(__file__).resolve().parents[1]])


def _record_test_result(result, execution, *, interrupted=False):
    status, reason = ('error', 'interrupted') if interrupted else _classify_test_execution(execution)
    evidence = {'status': status, 'reason': reason, 'stdout': execution.stdout,
                'stderr': execution.stderr, 'exit_code': execution.exit_code,
                'timed_out': execution.timed_out, 'wall_ms': execution.wall_ms}
    result['test_results'].append(evidence)
    result['events'].append({'evidence_id': 'final-public-tests', 'kind': 'test',
                             'action': 'FINAL_TEST', 'status': status, 'output': evidence})


def execute_native_case(case, adapter, *, session_factory=None, runner_factory=None,
                        clock=time.monotonic, artifact_dir=None):
    started = clock()
    wall = float(case['wall_seconds'])
    result = {'case_id': case['id'], 'status': 'error', 'end_reason': None, 'error': None,
              'turns': 0, 'wall_ms': 0, 'transcript': [], 'events': [], 'native_events': [],
              'final_report': None, 'final_diff': '', 'test_results': [], 'usage': [],
              'phase_results': [], 'execution_policy': dict(NATIVE_POLICY)}
    phases = [None] + [s['message'] for s in sorted(case.get('stages', []), key=lambda s: s['after_turn'])]
    reserve = min(30.0, wall * .05)
    model_budget = wall - reserve
    result['execution_policy']['validation_reserve_seconds'] = reserve
    redact = lambda value: value
    with tempfile.TemporaryDirectory(prefix='patchmud-native-') as temp:
        root, worktree = Path(temp), Path(temp)/'worktree'
        before = None
        try:
            materialize_repo(Path(case['fixture_dir']), worktree)
            _protections(worktree)
            before = capture_workspace(worktree, {})['contents']
            factory = session_factory or NativeSession
            with factory(adapter.profile, worktree, root/'state') as session:
                redact = getattr(session, 'redact', redact)
                session_id = None
                exhausted = False
                for phase, stage in enumerate(phases, 1):
                    remaining = max(0.0, model_budget * phase/len(phases) - (clock()-started))
                    if remaining <= 0:
                        exhausted = True
                        continue
                    prompt = _prompt(case, stage)
                    result['transcript'].append({'role': 'user', 'content': prompt, 'phase': phase})
                    result['turns'] = phase
                    record = session.run_phase(prompt, remaining, session_id=session_id)
                    record = redact(record)
                    result['phase_results'].append({'phase': phase, 'budget_seconds': remaining, **record})
                    result['transcript'].extend(record.get('transcript', []))
                    for event in record.get('events', []):
                        result['events'].append({**event, 'phase': phase,
                                                 'evidence_id': f'native-{phase}-{len(result["events"])+1}'})
                    result['native_events'].extend(record.get('native_events', []))
                    result['usage'].append({'phase': phase, 'raw': record.get('usage')})
                    if record.get('final_report'):
                        result['final_report'] = record['final_report']
                    session_id = record.get('native_session_id') or session_id
                    if record['status'] == 'error':
                        result.update(status='error', end_reason=record.get('end_reason', 'native_cli'),
                                      error=record.get('error'))
                        if record.get('interrupted'):
                            result['interrupted'] = True
                        break
                    if phase < len(phases) and not session_id:
                        result.update(status='error', end_reason='native_session_missing',
                                      error='native CLI did not return a conversation ID for continuation')
                        break
                    exhausted = exhausted or record['status'] == 'budget_exhausted'
                else:
                    result.update(status='budget_exhausted' if exhausted else 'completed',
                                  end_reason='wall_clock' if exhausted else 'native_completed')
            if result['status'] != 'error' and case.get('test_argv'):
                validation, argv = prepare_public_validation(case, worktree, root/'validation')
                remaining = max(0.0, wall - (clock()-started))
                if remaining:
                    try:
                        execution = (runner_factory or _test_runner)(validation).run(argv, cwd=validation, timeout_s=remaining)
                    except KeyboardInterrupt as exc:
                        partial = getattr(exc, 'execution_result', None)
                        if partial is not None:
                            _record_test_result(result, partial, interrupted=True)
                        raise
                    _record_test_result(result, execution)
        except KeyboardInterrupt:
            result.update(status='error', end_reason='interrupted', error='interrupted by user', interrupted=True)
        except Exception as exc:
            result.update(status='error', end_reason='native_execution', error=f'{type(exc).__name__}: {str(exc)[:500]}')
        finally:
            if before is not None:
                try:
                    captured = capture_workspace(worktree, before)
                    result['final_diff'] = captured['final_diff']
                    result['files_manifest'] = captured['files']
                    disallowed = [path for path in captured['changed_paths']
                                  if not any(fnmatch.fnmatchcase(path, pattern) for pattern in case.get('allowed_paths', []))]
                    result['events'].append({'evidence_id': 'final-scope-inspection', 'kind': 'scope',
                                             'action': 'INSPECT_CHANGES', 'status': 'failed' if disallowed else 'passed',
                                             'output': {'changed_paths': captured['changed_paths'], 'outside_allowed_paths': disallowed,
                                                        'symlink_artifacts': captured['symlinks']}})
                except Exception as exc:
                    result.update(status='error', end_reason='evidence_capture', error=f'{type(exc).__name__}: {str(exc)[:500]}')
            result['wall_ms'] = round(max(0.0, clock()-started)*1000)
            result = redact(result)
    if artifact_dir is not None:
        path = Path(artifact_dir)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix='.native-', dir=path)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(result, handle, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, path/'execution.json')
        finally:
            Path(temporary).unlink(missing_ok=True)
    return result
