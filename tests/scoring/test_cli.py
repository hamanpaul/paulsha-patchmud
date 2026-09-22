from pathlib import Path

import pytest

from patchmud.scoring.cli import parse_args, profile_fingerprint


def test_grouped_flags_cannot_bleed_between_profiles():
    options = parse_args([
        '--base', '--harness', 'agy', '--model', 'gemini-3.8-flash', '--effort', 'high',
        '--target', '--harness', 'codex', '--model', 'gpt-5.6-luna', '--effort', 'max',
        '--repeat', '3', '--output-dir', '/tmp/my-score-report',
    ])
    assert options.base == {'harness': 'agy', 'model': 'gemini-3.8-flash', 'effort': 'high'}
    assert options.target == {'harness': 'codex', 'model': 'gpt-5.6-luna', 'effort': 'max'}
    assert options.repeat == 3
    assert options.output_dir == Path('/tmp/my-score-report')


@pytest.mark.parametrize('argv', [
    ['--base', '--harness', 'agy', '--model', 'm', '--effort', 'high'],
    ['--target', '--harness', 'codex', '--model', 'm'],
    ['--target', '--harness', 'codex', '--model', 'm', '--effort', 'max', '--effort', 'high'],
    ['--target', '--harness', 'codex', '--model', 'm', '--effort', 'max', '--target'],
    ['--harness', 'codex', '--target', '--model', 'm', '--effort', 'max'],
    ['--target', '--harness', 'codex', '--model', 'm', '--effort', 'max', '--repeat', '0'],
    ['--target', '--harness', 'codex', '--model', 'm', '--effort', 'max', '--refresh-base'],
])
def test_ambiguous_or_incomplete_group_is_rejected(argv):
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2


def test_offline_listing_does_not_require_a_target():
    assert parse_args(['--list-cases']).list_cases


def test_fingerprint_changes_with_all_scientifically_relevant_inputs():
    profile = {'requested': {'model': 'm', 'effort': 'max'}, 'harness_version': 'v1'}
    suite = {'id': 'engineering-v1', 'suite_hash': 'frozen', 'rubric_version': '1',
             'cases': [{'id': 'one', 'max_turns': 8, 'wall_seconds': 600}]}
    def fp(**kwargs):
        args = dict(profile=profile, suite=suite, repeat=1, judge_model='jev-1.13.0',
                    engine_digest='engine1', environment={'python': '3.11'})
        args.update(kwargs)
        return profile_fingerprint(**args)
    original = fp()
    assert original != fp(profile={**profile, 'harness_version': 'v2'})
    assert original != fp(profile={**profile, 'requested': {'model': 'm', 'effort': 'high'}})
    assert original != fp(suite={**suite, 'suite_hash': 'different'})
    assert original != fp(suite={**suite, 'rubric_version': '2'})
    assert original != fp(suite={**suite, 'cases': [{'id': 'one', 'max_turns': 9, 'wall_seconds': 600}]})
    assert original != fp(repeat=3)
    assert original != fp(judge_model='jev-2')
    assert original != fp(judge_protocol_version='dimension-evidence-v2')
    assert original != fp(engine_digest='engine2')
    assert original != fp(environment={'python': '3.12'})
    assert original == fp(profile=dict(reversed(list(profile.items()))))
