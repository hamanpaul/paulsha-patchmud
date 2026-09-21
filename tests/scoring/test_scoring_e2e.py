"""Application integration with real persistence and fake external execution."""

from copy import deepcopy
import json
import pytest

from patchmud.scoring.cli import CATEGORIES, parse_args, run_scoring


@pytest.fixture(autouse=True)
def _fake_environment(monkeypatch):
    monkeypatch.setattr('patchmud.scoring.cli.execution_environment', lambda: {
        'sandbox_runtime': {'python': 'test', 'pytest': 'test'}, 'bwrap': 'test',
    })


def _suite():
    cases = []
    for category in CATEGORIES:
        for depth in (1, 2, 3):
            cases.append({
                'id': f'{category}-{depth}', 'category': category, 'depth': depth,
                'title': f'{category} depth {depth}', 'prompt': 'Public requirements and evidence.',
                'requirements': ['Observe the actual result.'], 'allowed_paths': ['src/**'],
                'max_turns': depth*8, 'wall_seconds': depth*600, 'stages': [],
                'test_argv': ['python3', '-m', 'pytest', '-q'], 'case_hash': f'{category}{depth}',
                'public_files': {'src/main.py': 'print(1)\n'},
                'rubric': {key: {'instructions': key, 'criteria': [f'{key}:{i}' for i in range(5)]}
                           for key in ('fulfillment', 'evidence', 'constraints', 'verification')},
                'fixture_dir': '/must-not-be-exposed/private',
                'anchors': {'reference': {'final_report': 'PRIVATE_REFERENCE_SENTINEL'}},
            })
    return {'id': 'engineering-v1', 'version': '1', 'suite_hash': 'suite-hash',
            'rubric_version': 'rubric-v1', 'cases': cases}


def _profile(**requested):
    return {'requested': requested, 'resolved': deepcopy(requested),
            'observed': {'model': None, 'effort': None},
            'harness_version': 'test-runtime-1', 'protocol_version': 'controlled-engineering-v1',
            'execution_mode': 'completion-only', 'capability': {'cacheable': True}}


class FakeJudge:
    def __init__(self, model):
        self.model = model

    def evaluate(self, case, execution):
        dimensions = {
            key: {'type': 'score', 'score': 3, 'confidence': 1,
                  'legend': dict(enumerate(rubric['criteria'])),
                  'probabilities': {str(i): int(i == 3) for i in range(5)}}
            for key, rubric in case['rubric'].items()
        }
        return {'status': 'scored', 'model': self.model, 'score': 75., 'dimensions': dimensions,
                'evidence_refs': ['ev-1'], 'usage': {'input_tokens': 10, 'output_tokens': 5},
                'wall_ms': 2, 'request_hash': 'test-judge-request', 'error': None}


def _options(tmp_path, *, base=True, extra=()):
    args = ['--output-dir', str(tmp_path)]
    if base:
        args += ['--base', '--harness', 'agy', '--model', 'gemini-3.8-flash', '--effort', 'high']
    args += ['--target', '--harness', 'codex', '--model', 'gpt-5.6-luna', '--effort', 'max']
    return parse_args([*args, *extra])


def _dependencies(calls, *, failing_case=None):
    def execute(case, adapter, **kwargs):
        calls.append((adapter, case['id']))
        return {'case_id': case['id'], 'status': 'error' if case['id'] == failing_case else 'completed',
                'end_reason': 'provider_error' if case['id'] == failing_case else 'commit',
                'error': 'provider unavailable' if case['id'] == failing_case else None,
                'turns': 2, 'wall_ms': 4,
                'transcript': [{'role': 'assistant', 'content': 'ACTION: COMMIT\nREPORT:\nDone.'}],
                'events': [{'evidence_id': 'ev-1', 'kind': 'test', 'status': 'passed'}],
                'final_report': 'Done with evidence.', 'final_diff': '',
                'test_results': [{'status': 'passed'}],
                'usage': [{'input_tokens': 20, 'output_tokens': 3}]}
    return dict(suite_loader=lambda name: _suite(), profile_resolver=_profile,
                adapter_factory=lambda profile, **kwargs: profile['requested']['harness'],
                case_executor=execute, judge_factory=FakeJudge, progress=lambda message: None)


def test_paired_run_reuses_only_base_and_preserves_detailed_public_history(tmp_path):
    calls = []
    first = run_scoring(_options(tmp_path), **_dependencies(calls))
    assert len(calls) == 36
    assert all(record['status'] == 'complete' for record in first)
    assert all(record['summary']['total'] == 75 for record in first)
    assert first[-1]['comparison']['reused'] is False
    assert first[-1]['comparison']['compatible'] is True
    assert first[-1]['comparison']['total']['delta'] == 0
    second = run_scoring(_options(tmp_path), **_dependencies(calls))
    assert len(calls) == 54
    assert second[0]['run_id'] == first[0]['run_id']
    assert second[-1]['comparison']['reused'] is True
    assert second[-1]['comparison']['total']['delta'] == 0
    assert len(list((tmp_path/'runs').glob('*/run.json'))) == 3
    report = (tmp_path/'models-score.md').read_text()
    assert 'repair-1' in report and 'Observe the actual result.' in report
    for path in (tmp_path/'runs').glob('*/run.json'):
        raw = path.read_text()
        assert 'PRIVATE_REFERENCE_SENTINEL' not in raw and '/must-not-be-exposed' not in raw
    run_scoring(_options(tmp_path, extra=['--refresh-base']), **_dependencies(calls))
    assert len(calls) == 90


def test_infrastructure_failure_does_not_become_zero_or_complete_baseline(tmp_path):
    calls = []
    records = run_scoring(_options(tmp_path, base=False), **_dependencies(calls, failing_case='repair-2'))
    record = records[0]
    assert record['status'] == 'partial'
    assert record['summary']['total'] is None
    assert record['summary']['coverage'] == {'expected': 18, 'scored': 17}
    row = next(row for row in record['cases'] if row['case_id'] == 'repair-2')
    assert row['judgment']['score'] is None


def test_compatible_historical_target_can_serve_as_later_base(tmp_path):
    calls = []
    first_options = _options(tmp_path, base=False)
    first_options.target = {'harness': 'agy', 'model': 'gemini-3.8-flash', 'effort': 'high'}
    historical = run_scoring(first_options, **_dependencies(calls))[0]
    assert historical['role'] == 'target'
    paired = run_scoring(_options(tmp_path), **_dependencies(calls))
    assert len(calls) == 36  # 18 historical cases, then only the new target's 18.
    assert paired[0]['run_id'] == historical['run_id']
    assert paired[-1]['comparison']['base_run_id'] == historical['run_id']
    assert paired[-1]['comparison']['compatible'] is True
    assert paired[-1]['comparison']['reused'] is True


def test_pilot_cannot_publish_full_score_or_pollute_formal_cache(tmp_path):
    calls = []
    pilot = run_scoring(_options(tmp_path, extra=['--pilot']), **_dependencies(calls))
    assert len(calls) == 12
    assert all(row['phase'] == 'pilot' and row['status'] == 'partial' for row in pilot)
    assert all(row['summary']['total'] is None for row in pilot)
    assert {case['case_id'] for case in pilot[0]['cases']} == {
        'repair-1', 'diagnosis-2', 'scope-3', 'testing-1', 'recovery-2', 'audit-3'}
    formal = run_scoring(_options(tmp_path), **_dependencies(calls))
    assert len(calls) == 48
    assert formal[-1]['comparison']['reused'] is False


def test_interruption_preserves_finished_cases_without_full_score(tmp_path):
    calls = []
    deps = _dependencies(calls)
    execute = deps['case_executor']
    def interrupted(case, adapter, **kwargs):
        if len(calls) == 1:
            exc = KeyboardInterrupt()
            exc.execution_result = {
                'case_id': case['id'], 'status': 'error', 'end_reason': 'interrupted',
                'error': 'interrupted', 'transcript': [{'role': 'assistant', 'content': 'Partial analysis'}],
            }
            raise exc
        return execute(case, adapter, **kwargs)
    deps['case_executor'] = interrupted
    records = run_scoring(_options(tmp_path, base=False), **deps)
    assert len(records) == 1
    assert len(records[0]['cases']) == 2
    assert records[0]['cases'][1]['execution']['transcript'][0]['content'] == 'Partial analysis'
    assert records[0]['summary']['total'] is None
    saved = json.loads(next((tmp_path/'runs').glob('*/run.json')).read_text())
    assert 'interrupted' in saved['error']


def test_explicit_selection_of_all_cases_still_cannot_be_a_formal_baseline(tmp_path):
    selected_flags = [part for c in _suite()['cases'] for part in ('--case', c['id'])]
    records = run_scoring(_options(tmp_path, base=False, extra=selected_flags), **_dependencies([]))
    assert records[0]['phase'] == 'partial'
    assert records[0]['evaluation_complete'] is True
    assert records[0]['summary']['total'] is None
    assert records[0]['status'] == 'partial'


def test_unknown_runtime_identity_cannot_reuse_a_baseline(tmp_path):
    calls = []
    deps = _dependencies(calls)
    def unresolved(**requested):
        profile = _profile(**requested)
        profile['capability']['cacheable'] = False
        return profile
    deps['profile_resolver'] = unresolved
    first = run_scoring(_options(tmp_path), **deps)
    second = run_scoring(_options(tmp_path), **deps)
    assert len(calls) == 72
    assert first[0]['run_id'] != second[0]['run_id']
    assert second[-1]['comparison']['reused'] is False


def test_unsupported_controlled_harness_stops_before_any_model_call(tmp_path):
    calls = []
    deps = _dependencies(calls)
    def unsupported(**requested):
        profile = _profile(**requested)
        profile['capability']['controlled_supported'] = requested['harness'] != 'codex'
        return profile
    deps['profile_resolver'] = unsupported
    with pytest.raises(ValueError, match='controlled'):
        run_scoring(_options(tmp_path), **deps)
    assert calls == []
    assert not (tmp_path/'runs').exists()


def test_missing_sandbox_pytest_stops_before_any_model_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('patchmud.scoring.cli.execution_environment',
                        lambda: {'sandbox_runtime': None, 'bwrap': 'test'})
    with pytest.raises(ValueError, match='pytest'):
        run_scoring(_options(tmp_path), **_dependencies(calls))
    assert calls == []
    assert not (tmp_path/'runs').exists()


def test_judge_interruption_archives_current_execution_and_stops(tmp_path):
    calls = []
    deps = _dependencies(calls)

    class InterruptedJudge(FakeJudge):
        def evaluate(self, case, execution):
            raise KeyboardInterrupt

    deps['judge_factory'] = InterruptedJudge
    records = run_scoring(_options(tmp_path), **deps)
    assert len(calls) == 1
    assert len(records) == 1
    record = records[0]
    assert len(record['cases']) == 1
    row = record['cases'][0]
    assert row['execution']['final_report'] == 'Done with evidence.'
    assert row['execution']['transcript']
    assert row['judgment']['status'] == 'error'
    assert row['judgment']['score'] is None
    assert record['summary']['total'] is None
    saved = json.loads(next((tmp_path/'runs').glob('*/run.json')).read_text())
    assert saved['cases'][0]['execution'] == row['execution']
    assert 'interrupted' in saved['error']
