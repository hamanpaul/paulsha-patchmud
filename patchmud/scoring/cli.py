"""Command-line orchestration for controlled, JEV-judged model scoring."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Callable
import uuid


JUDGE_MODEL = 'jev-1.13.0'
PROTOCOL_VERSION = 'controlled-engineering-v1'
CATEGORIES = ('repair', 'diagnosis', 'scope', 'testing', 'recovery', 'audit')
PILOT_DEPTHS = dict(zip(CATEGORIES, (1, 2, 3, 1, 2, 3)))


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse global options separately from strictly scoped profile groups."""
    parser = argparse.ArgumentParser(
        prog='paulsha-patchmud',
        usage='%(prog)s [options] [--base --harness H --model M --effort E] '
              '--target --harness H --model M --effort E',
        description='用固定工程情境及 JEV 為 harness × model × effort 評分。',
        epilog='每個 --base / --target 群組必須各自包含 --harness、--model、--effort。',
        allow_abbrev=False,
    )
    parser.add_argument('--suite', default='engineering-v1')
    parser.add_argument('--repeat', type=_positive, default=1)
    parser.add_argument('--refresh-base', action='store_true', help='強制重跑 base')
    parser.add_argument('--output-dir', type=Path, default=Path.home()/'.config/paulsha-patchmud')
    parser.add_argument('--list-cases', action='store_true', help='離線列出題庫與預算')
    parser.add_argument('--pilot', action='store_true', help='六題試跑；不產生正式總分或可重用基準')
    parser.add_argument('--case', action='append', default=[], help='選取 case ID；標示為部分評測')
    # The group fields intentionally do not appear in the global parser: repeated
    # argparse stores otherwise overwrite the other participant's configuration.
    options, rest = parser.parse_known_args(argv)
    profiles: dict[str, dict] = {}
    i = 0
    while i < len(rest):
        marker = rest[i]
        if marker not in ('--base', '--target'):
            parser.error(f'profile 欄位必須放在 --base 或 --target 後：{marker}')
        role = marker[2:]
        if role in profiles:
            parser.error(f'重複的 {marker}')
        i += 1
        values = {}
        while i < len(rest) and rest[i] not in ('--base', '--target'):
            flag = rest[i]
            if flag not in ('--harness', '--model', '--effort'):
                parser.error(f'未知 profile 選項：{flag}')
            key = flag[2:]
            if key in values:
                parser.error(f'{marker} 重複指定 {flag}')
            if i + 1 >= len(rest) or rest[i + 1].startswith('--'):
                parser.error(f'{flag} 缺少值')
            values[key] = rest[i + 1]
            i += 2
        missing = {'harness', 'model', 'effort'} - values.keys()
        if missing:
            parser.error(f'{marker} 缺少：{", ".join(sorted(missing))}')
        profiles[role] = values
    if 'target' not in profiles and not options.list_cases:
        parser.error('需要 --target 群組')
    if options.refresh_base and 'base' not in profiles:
        parser.error('--refresh-base 需要 --base 群組')
    if options.pilot and options.case:
        parser.error('--pilot 與 --case 不可同時使用')
    options.target = profiles.get('target')
    options.base = profiles.get('base')
    options.output_dir = options.output_dir.expanduser().resolve()
    return options


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def engine_content_digest() -> str:
    """Installed source bytes, rather than a potentially unchanged VERSION."""
    root = Path(__file__).resolve().parents[1]
    entries = []
    for path in sorted(root.rglob('*.py')):
        if 'data' not in path.relative_to(root).parts:
            entries.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return _digest(entries)


def execution_environment() -> dict:
    try:
        pytest_version = metadata.version('pytest')
    except metadata.PackageNotFoundError:
        pytest_version = None
    bwrap = shutil.which('bwrap')
    bwrap_version = None
    sandbox_runtime = None
    try:
        response = subprocess.run(
            ['/usr/bin/python3', '-I', '-c',
             'import json,platform,importlib.metadata; '
             'print(json.dumps({"python":platform.python_version(),'
             '"pytest":importlib.metadata.version("pytest")}))'],
            cwd='/', capture_output=True, text=True, timeout=5,
        )
        if response.returncode == 0:
            sandbox_runtime = json.loads(response.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    if bwrap:
        try:
            response = subprocess.run([bwrap, '--version'], capture_output=True, text=True, timeout=5)
            if response.returncode == 0:
                bwrap_version = response.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {'python': platform.python_version(), 'platform': platform.system(),
            'machine': platform.machine(), 'kernel': platform.release(),
            'pytest': pytest_version, 'bwrap': bwrap_version,
            'sandbox_runtime': sandbox_runtime}


def profile_fingerprint(*, profile: dict, suite: dict, repeat: int,
                        judge_model: str, engine_digest: str, environment: dict) -> str:
    return _digest({
        'schema_version': 1, 'profile': profile, 'suite_id': suite['id'],
        'suite_hash': suite['suite_hash'], 'rubric_version': suite['rubric_version'],
        'cases': [{'id': c['id'], 'max_turns': c['max_turns'], 'wall_seconds': c['wall_seconds']}
                  for c in suite['cases']],
        'repeat': repeat, 'judge_model': judge_model, 'protocol': PROTOCOL_VERSION,
        'engine_digest': engine_digest, 'environment': environment,
    })


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    secret = os.environ.get('TYPESAFE_API_KEY')
    if secret:
        message = message.replace(secret, '[redacted]')
    return f'{type(exc).__name__}: {message[:500]}'


def _write_progress(directory: Path, record: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.progress-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory/'progress.json')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_scoring(options: argparse.Namespace, *, suite_loader=None, profile_resolver=None,
                adapter_factory=None, case_executor=None, judge_factory=None,
                store_factory=None, progress: Callable[[str], None] = print) -> list[dict]:
    """Injectable application seam; unit tests never spawn a model or call JEV."""
    from .aggregation import aggregate_results, compare_results
    from .cases import load_suite, public_case_snapshot
    from .judge import JevJudge
    from .runner import build_scoring_adapter, execute_case, resolve_profile
    from .store import ScoreStore

    suite = (suite_loader or load_suite)(options.suite)
    all_cases = suite['cases']
    if options.pilot:
        selected = [c for c in all_cases if c['depth'] == PILOT_DEPTHS[c['category']]]
    elif options.case:
        ids = set(options.case)
        unknown = ids - {c['id'] for c in all_cases}
        if unknown:
            raise ValueError(f'未知 case：{", ".join(sorted(unknown))}')
        selected = [c for c in all_cases if c['id'] in ids]
    else:
        selected = all_cases
    if not selected:
        raise ValueError('題庫或選取結果不可為空')
    phase = 'pilot' if options.pilot else 'partial' if options.case else 'formal'
    resolver = profile_resolver or resolve_profile
    # Validate both requested profiles before any billable model execution.
    profiles = {role: resolver(**getattr(options, role)) for role in ('base', 'target')
                if getattr(options, role) is not None}
    for role, profile in profiles.items():
        if profile.get('capability', {}).get('controlled_supported') is False:
            raise ValueError(
                f'{role}: {profile["requested"]["harness"]} 不支援已驗證的 controlled '
                '統一工具模式；尚未呼叫受測模型。CLI read-only／sandbox 旗標無法保證關閉原生工具。'
            )
    factory = adapter_factory or build_scoring_adapter
    executor = case_executor or execute_case
    environment = execution_environment()
    if not environment.get('sandbox_runtime'):
        raise ValueError('沙箱 /usr/bin/python3 無法載入 pytest；尚未呼叫受測模型。')
    if not environment.get('bwrap'):
        raise ValueError('沙箱缺少可用的 bubblewrap；尚未呼叫受測模型。')
    judge = (judge_factory or JevJudge)(model=JUDGE_MODEL)
    store = (store_factory or ScoreStore)(options.output_dir)
    source_hash = engine_content_digest()
    records = []
    base_record = None
    base_reused = False
    for role in ('base', 'target'):
        if role not in profiles:
            continue
        profile = profiles[role]
        fingerprint = profile_fingerprint(profile=profile, suite=suite, repeat=options.repeat,
                                          judge_model=JUDGE_MODEL, engine_digest=source_hash,
                                          environment=environment)
        cacheable = profile.get('capability', {}).get('cacheable') is True
        if role == 'base' and cacheable and not options.refresh_base and phase == 'formal':
            cached = store.find_baseline(fingerprint)
            if cached is not None:
                base_record, base_reused = cached, True
                records.append(cached)
                progress(f'base: 重用 {cached["run_id"]}（{cached["created_at"]}）')
                continue
        run_id = f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{role}-{uuid.uuid4().hex[:12]}'
        work = options.output_dir/'.in-progress'/run_id
        record = {
            'schema_version': 1, 'run_id': run_id,
            'created_at': datetime.now(timezone.utc).isoformat(), 'role': role,
            'phase': phase, 'profile': profile, 'fingerprint': fingerprint,
            'suite': {key: suite[key] for key in ('id', 'version', 'suite_hash', 'rubric_version')},
            'judge_model': JUDGE_MODEL, 'repeat': options.repeat, 'status': 'partial',
            'engine_digest': source_hash, 'environment': environment,
            'protocol_version': profile['protocol_version'],
            'tool_cohort': {'protocol': profile['protocol_version'],
                            'execution_mode': profile['execution_mode']},
            'budget': {c['id']: {'max_turns': c['max_turns'], 'wall_seconds': c['wall_seconds']}
                       for c in all_cases},
            'cases': [], 'summary': {'total': None, 'complete': False},
            'actual_model_snapshot': None,
            'requested_case_ids': [c['id'] for c in selected],
            'identity_note': 'requested/resolved 設定不是服務端實際模型快照的證明。',
        }
        record['suite']['case_ids'] = [c['id'] for c in all_cases]
        interrupted = False
        try:
            for repetition in range(1, options.repeat + 1):
                for case in selected:
                    progress(f'{role}: {case["id"]} [{repetition}/{options.repeat}]')
                    adapter = factory(profile, timeout_s=case['wall_seconds'])
                    artifact_dir = work/f'{case["id"]}-{repetition}'
                    try:
                        execution = executor(case, adapter, artifact_dir=artifact_dir)
                    except KeyboardInterrupt as exc:
                        interrupted = True
                        execution = getattr(exc, 'execution_result', {
                            'case_id': case['id'], 'status': 'error',
                            'end_reason': 'interrupted', 'error': 'interrupted by user',
                        })
                    except Exception as exc:
                        execution = {'case_id': case['id'], 'status': 'error',
                                     'end_reason': 'execution_error', 'error': _safe_error(exc)}
                    if execution.get('end_reason') == 'interrupted':
                        interrupted = True
                    if execution['status'] == 'error':
                        judgment = {'status': 'error', 'score': None, 'dimensions': {},
                                    'model': JUDGE_MODEL, 'error': execution.get('error'), 'usage': None}
                    else:
                        try:
                            judgment = judge.evaluate(case, execution)
                        except KeyboardInterrupt:
                            interrupted = True
                            judgment = {'status': 'error', 'score': None, 'dimensions': {},
                                        'model': JUDGE_MODEL, 'error': 'JEV judging interrupted by user',
                                        'usage': None}
                        except Exception as exc:
                            judgment = {'status': 'error', 'score': None, 'dimensions': {},
                                        'model': JUDGE_MODEL, 'error': _safe_error(exc), 'usage': None}
                    record['cases'].append({'case_id': case['id'], 'case': public_case_snapshot(case),
                                            'execution': execution, 'judgment': judgment,
                                            'repetition': repetition})
                    record['summary'] = aggregate_results(record['cases'], all_cases, options.repeat)
                    _write_progress(work, record)
                    if interrupted:
                        raise KeyboardInterrupt
        except KeyboardInterrupt:
            interrupted = True
            record['error'] = 'interrupted by user; completed case evidence preserved'
            record['partial_artifacts'] = f'.in-progress/{run_id}'
        except Exception as exc:
            record['error'] = _safe_error(exc)
        record['summary'] = aggregate_results(record['cases'], all_cases, options.repeat)
        selected_summary = aggregate_results(record['cases'], selected, options.repeat)
        record['evaluation_complete'] = selected_summary['complete'] and not record.get('error')
        if phase != 'formal':
            record['summary']['total'] = None
            record['summary']['complete'] = False
        record['status'] = ('complete' if record['summary']['complete'] and phase == 'formal'
                            else 'error' if not record['cases'] else 'partial')
        if role == 'target' and base_record is not None:
            record['comparison'] = compare_results(record, base_record)
            record['comparison'].update(base_run_id=base_record['run_id'], reused=base_reused,
                                        base_created_at=base_record['created_at'])
        archive = store.save_run(record)
        progress(f'{role}: {record["status"]}; 封存 {archive}')
        records.append(record)
        store.render_report()
        # Remove only this invocation's checkpoint after the immutable store owns it.
        if work.exists() and not interrupted:
            shutil.rmtree(work)
        if role == 'base':
            base_record = record
        if interrupted:
            break
    store.render_report()
    return records


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    if options.list_cases:
        from .cases import load_suite
        suite = load_suite(options.suite)
        print(f'{suite["id"]} / {suite["suite_hash"]}')
        for case in suite['cases']:
            print(f'{case["id"]}\t{case["category"]}\tdepth={case["depth"]}\t'
                  f'{case["max_turns"]} turns / {case["wall_seconds"]}s\t{case["title"]}')
        return 0
    if not os.environ.get('TYPESAFE_API_KEY'):
        print('JEV 認證缺少 TYPESAFE_API_KEY；尚未呼叫受測模型。請由執行環境提供憑證。', file=sys.stderr)
        return 1
    try:
        records = run_scoring(options)
    except Exception as exc:
        print(f'paulsha-patchmud: {_safe_error(exc)}', file=sys.stderr)
        return 1
    print(f'報告：{options.output_dir / "models-score.md"}')
    return 0 if records and all(r.get('evaluation_complete', r['summary']['complete']) for r in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
