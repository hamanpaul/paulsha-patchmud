from contextlib import contextmanager
from pathlib import Path

from patchmud.sandbox.isolate import Execution
from patchmud.scoring.native_runner import NativeAgentAdapter, capture_workspace, execute_native_case, prepare_home


def case_fixture(tmp_path):
    fixture = tmp_path/'fixture'
    (fixture/'repo/src').mkdir(parents=True)
    (fixture/'repo/src/value.py').write_text('value = 1\n')
    (fixture/'hidden').mkdir()
    (fixture/'hidden/reference.txt').write_text('PRIVATE_ANSWER')
    return {'id': 'native-case', 'fixture_dir': str(fixture), 'depth': 2,
            'prompt': 'Repair value using native tools.', 'requirements': ['Preserve API.'],
            'allowed_paths': ['src/**', 'tests/agent/**'], 'max_turns': 16,
            'wall_seconds': 1200, 'stages': [{'after_turn': 8, 'message': 'NEW_STAGE_REQUIREMENT'}],
            'rubric': {}, 'test_argv': ['python3','-m','pytest','-q']}


class TestRunner:
    __test__ = False
    def __init__(self, worktree):
        self.worktree = worktree
    def run(self, argv, cwd, timeout_s):
        return Execution(0, '1 passed', '', 2, 1, False)


def test_native_tools_and_stages_preserve_workspace_and_evidence(tmp_path):
    prompts, sessions = [], []
    @contextmanager
    def session_factory(profile, worktree, state_root):
        class Session:
            def run_phase(self, prompt, timeout_s, session_id=None):
                prompts.append(prompt)
                sessions.append(session_id)
                assert not (worktree/'hidden').exists()
                (worktree/'src/value.py').write_text('value = 2\n')
                return {'status': 'completed', 'end_reason': 'completed', 'error': None,
                        'native_session_id': 'session-1', 'final_report': 'Verified native change.',
                        'transcript': [{'role':'assistant','content':'Verified native change.'}],
                        'events': [{'kind':'native_tool','action':'shell','status':'passed','output':'1 passed'}],
                        'native_events': [{'type':'command_execution'}], 'usage': {'input_tokens':10}, 'wall_ms':2}
        yield Session()
    result = execute_native_case(case_fixture(tmp_path), NativeAgentAdapter({'requested': {'harness':'codex'}}),
                                 session_factory=session_factory, runner_factory=TestRunner)
    assert result['status'] == 'completed'
    assert sessions == [None, 'session-1']
    assert 'NEW_STAGE_REQUIREMENT' not in prompts[0]
    assert 'NEW_STAGE_REQUIREMENT' in prompts[1]
    assert all('ACTION: PATCH' not in prompt for prompt in prompts)
    assert 'value = 2' in result['final_diff']
    assert result['test_results'][0]['status'] == 'passed'
    assert result['usage'][0]['raw']['input_tokens'] == 10
    assert len(result['phase_results']) == 2
    assert any(event['kind'] == 'native_tool' for event in result['events'])


def test_native_interrupt_preserves_diff_and_stops_phases(tmp_path):
    calls=[]
    @contextmanager
    def session_factory(profile, worktree, state_root):
        class Session:
            def run_phase(self, prompt, timeout_s, session_id=None):
                calls.append(prompt)
                (worktree/'src/value.py').write_text('value = 3\n')
                raise KeyboardInterrupt
        yield Session()
    result=execute_native_case(case_fixture(tmp_path), NativeAgentAdapter({'requested': {'harness':'codex'}}),
                               session_factory=session_factory, runner_factory=TestRunner)
    assert len(calls) == 1
    assert result['end_reason'] == 'interrupted'
    assert result['interrupted'] is True
    assert 'value = 3' in result['final_diff']


def test_capture_never_follows_symlinks_or_executes_candidate_git(tmp_path):
    work=tmp_path/'work'
    (work/'.git').mkdir(parents=True)
    (work/'.git/config').write_text('[core]\nfsmonitor = should-never-run\n')
    outside=tmp_path/'private.txt'
    outside.write_text('PRIVATE_SENTINEL')
    (work/'leak').symlink_to(outside)
    capture=capture_workspace(work, {})
    assert 'PRIVATE_SENTINEL' not in str(capture)
    assert capture['symlinks'] == ['leak']


def test_minimal_home_copies_auth_only_and_redacts_values(tmp_path):
    source=tmp_path/'source'
    (source/'.codex').mkdir(parents=True)
    (source/'.codex/auth.json').write_text('{"tokens":{"access_token":"SYNTHETIC_AUTH_SENTINEL_123456"}}')
    (source/'.codex/config.toml').write_text('DO_NOT_COPY_PERSONAL_CONFIG')
    home=tmp_path/'isolated'
    redact=prepare_home('codex', home, source_home=source)
    assert (home/'.codex/auth.json').is_file()
    assert not (home/'.codex/config.toml').exists()
    assert 'SYNTHETIC_AUTH_SENTINEL_123456' not in redact({'output':'token=SYNTHETIC_AUTH_SENTINEL_123456'})['output']


def test_agy_home_stages_credentials_without_old_conversations(tmp_path):
    source = tmp_path/'source'
    auth = source/'.gemini/antigravity-cli'
    auth.mkdir(parents=True)
    (auth/'antigravity-oauth-token').write_text('{"token":"SYNTHETIC_AGY_AUTH_123456"}')
    (source/'.gemini/oauth_creds.json').write_text('{"refresh_token":"SYNTHETIC_REFRESH_123456"}')
    (auth/'conversations.db').write_text('OLD_PRIVATE_CONVERSATIONS')
    (auth/'settings.json').write_text('PERSONAL_SETTINGS')
    home = tmp_path/'isolated'
    redact = prepare_home('agy', home, source_home=source)
    staged = home/'.gemini/antigravity-cli'
    assert (staged/'antigravity-oauth-token').is_file()
    assert (home/'.gemini/oauth_creds.json').is_file()
    assert not (staged/'conversations.db').exists()
    assert not (staged/'settings.json').exists()
    assert redact('SYNTHETIC_AGY_AUTH_123456 SYNTHETIC_REFRESH_123456') == '[redacted] [redacted]'


def test_staged_run_requires_conversation_identity(tmp_path):
    calls = []
    @contextmanager
    def session_factory(*args):
        class Session:
            def run_phase(self, prompt, timeout_s, session_id=None):
                calls.append(prompt)
                return {'status':'completed', 'final_report':'First result', 'native_session_id':None}
        yield Session()
    result = execute_native_case(case_fixture(tmp_path), NativeAgentAdapter({}),
                                 session_factory=session_factory, runner_factory=TestRunner)
    assert len(calls) == 1
    assert result['status'] == 'error'
    assert result['end_reason'] == 'native_session_missing'


def test_native_timeout_still_has_final_evidence_and_is_judgeable(tmp_path):
    @contextmanager
    def session_factory(profile, worktree, state_root):
        class Session:
            def run_phase(self, prompt, timeout_s, session_id=None):
                (worktree/'src/value.py').write_text('value = 7\n')
                return {'status':'budget_exhausted', 'native_session_id':'session-1',
                        'events':[{'kind':'native_tool','output':'partial tool output'}]}
        yield Session()
    result = execute_native_case(case_fixture(tmp_path), NativeAgentAdapter({}),
                                 session_factory=session_factory, runner_factory=TestRunner)
    assert result['status'] == 'budget_exhausted'
    assert result['error'] is None
    assert 'value = 7' in result['final_diff']
    assert result['test_results'][0]['status'] == 'passed'


def test_native_agy_profile_checks_catalog_without_model_inference(monkeypatch, tmp_path):
    from patchmud.scoring import native_runner
    auth = tmp_path/'.gemini/antigravity-cli'
    auth.mkdir(parents=True)
    (auth/'antigravity-oauth-token').write_text('{}')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    def profile(*args, **kwargs):
        return {'requested':{'harness':'agy'},'resolved':{'harness':'agy'},
                'capability':{'runtime_identity_known':True,'model_verified':False,'executable_version':'1.2.7',
                              'executable':'/workspace/agy'}}
    monkeypatch.setattr('patchmud.scoring.runner.resolve_profile', profile)
    monkeypatch.setattr(native_runner, '_runtime_bundle', lambda *args: {})
    monkeypatch.setattr(native_runner, '_agy_catalog_confirms', lambda profile: True)
    assert native_runner.resolve_native_profile('AGY','gemini-3.8-flash','high')['capability']['native_supported']
    monkeypatch.setattr(native_runner, '_agy_catalog_confirms', lambda profile: False)
    assert not native_runner.resolve_native_profile('AGY','gemini-3.8-flash','high')['capability']['native_supported']


def test_codex_runtime_fingerprint_includes_code_mode_helper(tmp_path):
    from patchmud.scoring.native_runner import _runtime_bundle
    (tmp_path/'bin').mkdir()
    (tmp_path/'bin/codex').write_text('native-cli')
    (tmp_path/'bin/codex-code-mode-host').write_text('helper-v1')
    (tmp_path/'codex-package.json').write_text('{}')
    first = _runtime_bundle(tmp_path/'bin/codex', 'codex')
    assert {x['path'] for x in first['files']} == {'bin/codex','bin/codex-code-mode-host','codex-package.json'}
    (tmp_path/'bin/codex-code-mode-host').write_text('helper-v2')
    assert _runtime_bundle(tmp_path/'bin/codex', 'codex') != first


def test_final_test_interrupt_preserves_partial_test_evidence(tmp_path):
    @contextmanager
    def session_factory(*args):
        class Session:
            def run_phase(self, prompt, timeout_s, session_id=None):
                return {'status':'completed', 'native_session_id':'session-1', 'final_report':'Implemented.'}
        yield Session()
    class InterruptedTestRunner(TestRunner):
        def run(self, argv, cwd, timeout_s):
            exc = KeyboardInterrupt()
            exc.execution_result = Execution(-9, 'one passed before cancellation', 'partial stderr', 10, 1, False)
            raise exc
    result = execute_native_case(case_fixture(tmp_path), NativeAgentAdapter({}),
                                 session_factory=session_factory, runner_factory=InterruptedTestRunner)
    assert result['end_reason'] == 'interrupted'
    assert result['test_results'][0]['stdout'] == 'one passed before cancellation'
    assert result['test_results'][0]['status'] == 'error'
    assert result['test_results'][0]['reason'] == 'interrupted'
    assert any(e['evidence_id'] == 'final-public-tests' for e in result['events'])


def test_agy_native_session_frames_single_stdin_message(tmp_path):
    import json
    from patchmud.scoring.native_runner import NativeSession
    profile = {'resolved':{'harness':'agy','model':'gemini-3.8-flash-high','effort':'high'}}
    session = NativeSession(profile, tmp_path/'worktree', tmp_path/'state')
    session.executable = '/opt/patchmud-native/bin/agy'
    seen = []
    class Runner:
        def run(self, argv, cwd, timeout_s, input_text):
            seen.append(input_text)
            return Execution(0, '', '', 0, 0, False)
    session.runner = Runner()
    session.run_phase('A prompt\nwith multiple lines', 10)
    assert seen[0].endswith('\n')
    assert json.loads(seen[0]) == {'event':'user','message':{'content':'A prompt\nwith multiple lines'}}


def test_public_validation_excludes_candidate_pytest_configuration(tmp_path):
    case = case_fixture(tmp_path)
    public_tests = Path(case['fixture_dir'])/'repo/tests'
    public_tests.mkdir()
    (public_tests/'test_public.py').write_text('def test_public():\n    assert False\n')
    workspaces = []
    @contextmanager
    def session_factory(profile, worktree, state_root):
        workspaces.append(worktree)
        class Session:
            def run_phase(self, prompt, timeout_s, session_id=None):
                (worktree/'tests/agent/conftest.py').write_text(
                    'def pytest_collection_modifyitems(items):\n    items[:] = []\n')
                return {'status':'completed','native_session_id':'session-1','final_report':'Claims tests pass.'}
        yield Session()
    class IndependentRunner(TestRunner):
        def run(self, argv, cwd, timeout_s):
            assert cwd != workspaces[0]
            assert not (cwd/'tests/agent/conftest.py').exists()
            assert 'assert False' in (cwd/'tests/test_public.py').read_text()
            assert '-I' in argv and '--ignore=tests/agent' in argv
            return Execution(1, '1 failed', '', 10, 1, False)
    result = execute_native_case(case, NativeAgentAdapter({}),
                                 session_factory=session_factory, runner_factory=IndependentRunner)
    assert result['status'] == 'completed'
    assert result['test_results'][0]['status'] == 'failed'
    assert 'pytest_collection_modifyitems' in result['final_diff']


def test_public_stage_phase_matches_execution_phase_number(tmp_path):
    from patchmud.scoring.native_runner import native_public_case
    from patchmud.scoring.cases import load_case

    case = load_case('scope-changing-boundary')
    public = native_public_case(case)
    assert [stage['phase'] for stage in public['stages']] == [2, 3]
