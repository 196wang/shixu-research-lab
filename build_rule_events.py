"""Freeze the rule review first, then run once. No network, trades or old scoring."""
import argparse
import json
from pathlib import Path

from historical_chart import HERE, load_history, read_json
from rule_events import (PROTOCOL, REGISTER_FILE, STARTED_FILE, RESULT_FILE,
                         RuleEventIntegrityError, compute_result, digest, input_hashes, now, verify_registration)


def write_exclusive(path, data):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':'))
        stream.write('\n')


def freeze(base=HERE):
    base = Path(base)
    if any((base / file).exists() for file in (REGISTER_FILE, STARTED_FILE, RESULT_FILE)):
        raise RuleEventIntegrityError('已有注册/执行/结果，不允许覆盖；变更需新版本')
    hashes = input_hashes(base)
    registration = {'id': 'REG-BZ-EVENT-REVIEW-001-V01', 'registeredAtUtc': now(),
                    'protocol': PROTOCOL, 'inputHashes': hashes, 'scoringRun': False,
                    'notProspectivePreregistration': True, 'priorHistoryAlreadyViewed': True,
                    'executionPolicy': 'single fixed descriptive run; no parameter search; never overwrite receipts'}
    registration['registrationSha256'] = digest(registration)
    write_exclusive(base / REGISTER_FILE, registration)
    return {'registered': True, 'registrationSha256': registration['registrationSha256'], 'inputCount': len(hashes)}


def run_once(base=HERE):
    base = Path(base)
    if any((base / file).exists() for file in (STARTED_FILE, RESULT_FILE, 'rule_events_failure.json')):
        raise RuleEventIntegrityError('本版本已开始或完成执行，禁止重复评分/覆盖；失败也需保留回执')
    registration = verify_registration(base)
    write_exclusive(base / STARTED_FILE, {'startedAtUtc': now(), 'registrationSha256': registration['registrationSha256'],
                                        'protocolId': PROTOCOL['id'], 'singleFixedRun': True})
    try:
        result = compute_result(load_history(base), read_json(base / 'rule_event_catalog.json'), registration)
        verify_registration(base)
        write_exclusive(base / RESULT_FILE, result)
    except Exception as exc:
        write_exclusive(base / 'rule_events_failure.json', {'failedAtUtc': now(), 'errorType': type(exc).__name__,
                                                         'error': str(exc), 'retryAllowed': False})
        raise
    return {'completed': True, 'dailyCount': len(result['daily']), 'summaryCount': len(result['summary']),
            'contentSha256': result['contentSha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'run'))
    parser.add_argument('--base-dir', type=Path, default=HERE)
    args = parser.parse_args()
    try:
        print(json.dumps((freeze if args.action == 'freeze' else run_once)(args.base_dir), ensure_ascii=False))
    except (RuleEventIntegrityError, ValueError, OSError) as exc:
        parser.exit(2, str(exc) + '\n')


if __name__ == '__main__':
    main()
