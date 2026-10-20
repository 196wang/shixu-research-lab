"""Fixed H-BZ-STRUCT-002 retrospective absolute-net-change study.

The original frozen registrations, data and feature code are read-only. The CLI
requires a separate execution registration, verified synthetic tests, and an
exclusive execution receipt. There are no parameter or period CLI overrides.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics

import bazi_features
import calendar_adapter
from calendar_validation import independent_day_label
import legacy_scorer
import masked_study


HERE = Path(__file__).resolve().parent
VERSION = '0.8.0'
HYPOTHESIS = 'H-BZ-STRUCT-002'
PROFILE = 'BTC-BZ-V01'
FEATURE = 'BZ-S02'
POLICY = 'BZ-CALENDAR-CIVIL-00-V01'
PERIODS = ({'start': '2020-01-01', 'end': '2024-12-31'},
           {'start': '2025-01-01', 'end': '2026-09-26'})
NATAL = {'year': '戊子', 'month': '甲子', 'day': '戊申', 'hour': '辛酉'}
BRANCHES = '子丑寅卯辰巳午未申酉戌亥'
OUTCOME = 'abs(close / open - 1)'
DECISIONS = {
    'positive': 'supported_positive_difference',
    'negative': 'supported_negative_difference',
    'inside': 'no_material_difference_at_chosen_margin',
    'uncertain': 'uncertain',
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    def reject(token):
        raise ValueError('nonfinite JSON number: ' + token)
    return json.loads(path.read_text(encoding='utf-8-sig'), parse_constant=reject)


def json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def validate_protocol(protocol: dict) -> None:
    expected = {'id': HYPOTHESIS, 'version': '0.1.0', 'profile': PROFILE,
                'featureId': FEATURE, 'featureVersion': '0.1.0', 'asset': 'BTC',
                'symbol': 'BTCUSDT', 'venue': 'Binance spot',
                'calendarPolicyId': POLICY, 'outcome': 'y = ' + OUTCOME,
                'practicalMarginBp': 10, 'independentHoldout': False,
                'marketValidated': False, 'marketExecutionEnabled': False}
    for name, value in expected.items():
        if protocol.get(name) != value or type(protocol.get(name)) is not type(value):
            raise ValueError('fixed protocol mismatch: ' + name)
    if protocol.get('periods') != list(PERIODS):
        raise ValueError('fixed periods changed')
    for name, value in {'file': 'daily_input.json', 'perPeriodMinimumCoverage': 0.95,
                        'minimumObservedDaysPerGroup': 100}.items():
        if protocol.get('data', {}).get(name) != value:
            raise ValueError('fixed data gate changed: ' + name)
    for name, value in {'blockLengthCalendarDays': 30, 'replicates': 10000,
                        'seedResetEachPeriod': 20260927, 'sensitivityGrid': False}.items():
        if protocol.get('inference', {}).get(name) != value:
            raise ValueError('fixed bootstrap setting changed: ' + name)


def structural_group_for_day(day: str) -> dict:
    """Evaluate frozen BZ-S02 at the fixed local midnight; independently audit it."""
    legacy_scorer.parse_date(day)
    at = day + 'T00:00:00+08:00'
    values = calendar_adapter.calculate_relations(PROFILE, at)
    calendar, relationships = values['calendar'], values['relationships']
    if calendar['policy']['id'] != POLICY or calendar['atResearchTimezone'] != at:
        raise ValueError('calendar policy or analysis midnight changed')
    jdn, independent_label = independent_day_label(day)
    actual_label = calendar['pillars']['day']
    if actual_label != independent_label:
        raise ValueError('day label disagrees with independent formula: ' + day)
    natal = {t['slot'].split('.')[1]: t['pillar'] for t in relationships['tokens']
             if t['slot'].startswith('natal.')}
    if natal != NATAL:
        raise ValueError('registered BTC natal profile changed')
    feature_result = bazi_features.calculate_features(relationships, calendar)
    if feature_result['version'] != '0.1.0':
        raise ValueError('feature implementation version changed')
    feature = next(f for f in feature_result['features'] if f['id'] == FEATURE)
    if type(feature['value']) is not bool:
        raise ValueError('six-clash feature must be a Boolean')
    # Independent six-branch-distance audit; neither feature pair tables nor its
    # pair detector are reused here. Duplicate natal branches never weight a day.
    day_index = BRANCHES.index(independent_label[1])
    expected_positions = ['natal.' + key for key, pillar in NATAL.items()
                          if (BRANCHES.index(pillar[1]) - day_index) % 12 == 6]
    expected_group = bool(expected_positions)
    actual_positions = [edge['slots'][0] for edge in feature['evidence']]
    if feature['value'] != expected_group or sorted(actual_positions) != sorted(expected_positions):
        raise ValueError('feature disagrees with independent six-clash audit: ' + day)
    return {'date': day, 'evaluatedAt': at, 'dayGanzhi': actual_label,
            'civilDateJDN': jdn, 'group': int(feature['value']),
            'featureId': FEATURE, 'clashEdges': feature['evidence'],
            'independentDayAndGroupVerified': True}


def build_grid(document: dict, start: str, end: str) -> list[dict]:
    """One ordered calendar slot per date; old stem grouping is not propagated."""
    first, last = legacy_scorer.parse_date(start), legacy_scorer.parse_date(end)
    if last < first:
        raise ValueError('period end before start')
    index = {}
    for row in document['dailyRows']:
        if not isinstance(row, dict):
            raise ValueError('input row must be an object')
        day = legacy_scorer.parse_date(row.get('date')).isoformat()
        if day in index:
            raise ValueError('duplicate input date: ' + day)
        index[day] = row
    grid = []
    for day in legacy_scorer.date_range(first, last):
        label = day.isoformat()
        if label not in index:
            raise ValueError('calendar grid row absent; do not compress gaps: ' + label)
        row = index[label]
        structure = structural_group_for_day(label)
        if row.get('dayGanzhi') != structure['dayGanzhi']:
            raise ValueError('input day label disagrees with independent formula: ' + label)
        checked = legacy_scorer.validate_row(row, label)
        # Only the frozen price/missing validation is reused. Its 金水日 group,
        # group label and direction summary are deliberately discarded.
        value = abs(checked['return']) if checked['valid'] else None
        grid.append({**structure, 'mask': int(checked['valid']),
                     'absoluteReturn': value,
                     'errors': checked['errors'],
                     'sourceErrors': row.get('sourceErrors', []),
                     'sourceRefs': row.get('sourceArchives', []),
                     'open': row.get('open'), 'close': row.get('close'),
                     'hourCount': row.get('hourCount')})
    validate_grid(grid)
    return grid


def validate_grid(grid: list[dict]) -> None:
    if not isinstance(grid, list) or not grid:
        raise ValueError('nonempty calendar grid required')
    previous = None
    for row in grid:
        day = legacy_scorer.parse_date(row['date'])
        if previous is not None and day != previous + timedelta(days=1):
            raise ValueError('nonconsecutive or duplicate calendar date')
        previous = day
        if type(row['group']) is not int or row['group'] not in (0, 1):
            raise ValueError('group must be Boolean encoded as integer 0 or 1')
        if type(row['mask']) is not int or row['mask'] not in (0, 1):
            raise ValueError('mask must be integer 0 or 1')
        value = row['absoluteReturn']
        if row['mask']:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError('observed absolute return must be finite and nonnegative')
        elif value is not None:
            raise ValueError('missing outcome must be null; no imputation')


def bootstrap_absolute(grid: list[dict]) -> dict:
    validate_grid(grid)
    # The frozen algorithm names its numeric slot `return`; here that internal
    # slot contains y=abs(C/O-1), never the signed return or the old group.
    internal = [{'date': r['date'], 'group': r['group'], 'mask': r['mask'],
                 'return': r['absoluteReturn']} for r in grid]
    result = masked_study.masked_bootstrap(internal, replicates=10000, seed=20260927, block=30)
    return {**result, 'method': 'moving_block_calendar_grid_paired_G_mask_absolute_net_change',
            'outcome': OUTCOME, 'minimumValidReplicates': 9900,
            'internalNumericFieldMeaning': 'legacy return slot contains absoluteReturn, not signed return',
            'startSampling': 'randrange(N-30+1), independently for every block including truncated final block',
            'rng': 'Python random.Random Mersenne Twister', 'seedResetEachPeriod': True}


def decision_from_interval(ci_bp: list[float], margin: float = 10) -> str:
    if (not isinstance(ci_bp, (list, tuple)) or len(ci_bp) != 2 or
            any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in ci_bp) or
            ci_bp[0] > ci_bp[1] or margin != 10):
        raise ValueError('invalid interval or changed practical margin')
    lower, upper = ci_bp
    if lower > margin:
        return DECISIONS['positive']
    if upper < -margin:
        return DECISIONS['negative']
    if lower >= -margin and upper <= margin:
        return DECISIONS['inside']
    return DECISIONS['uncertain']


def summarize_period(grid: list[dict], start: str, end: str) -> dict:
    validate_grid(grid)
    if grid[0]['date'] != start or grid[-1]['date'] != end:
        raise ValueError('grid boundaries disagree with period')
    observed = [r for r in grid if r['mask']]
    groups, missing = {}, {}
    for group in (0, 1):
        rows = [r for r in grid if r['group'] == group]
        values = [r['absoluteReturn'] for r in rows if r['mask']]
        mean = statistics.mean(values) if values else None
        groups[str(group)] = {'n': len(values), 'meanAbsoluteReturn': mean,
                              'meanAbsoluteReturnBp': mean * 10000 if mean is not None else None}
        missing[str(group)] = {'expected': len(rows), 'observed': len(values),
                               'missing': len(rows) - len(values),
                               'missingRate': 1 - len(values) / len(rows) if rows else None}
    delta = (groups['1']['meanAbsoluteReturn'] - groups['0']['meanAbsoluteReturn']
             if groups['0']['n'] and groups['1']['n'] else None)
    coverage = len(observed) / len(grid)
    gates = []
    if coverage < 0.95:
        gates.append('coverage_below_95_percent')
    if any(groups[str(g)]['n'] < 100 for g in (0, 1)):
        gates.append('fewer_than_100_observed_days_in_a_group')
    if gates:
        bootstrap = {'available': False, 'ci95': None, 'ci95Bp': None,
                     'replicatesRequested': 10000, 'replicatesValid': 0,
                     'replicatesInvalid': 0, 'replicatesNotRun': 10000,
                     'randomDrawCount': 0, 'unavailableReasons': gates}
        decision = 'insufficient_data'
    else:
        bootstrap = bootstrap_absolute(grid)
        decision = (decision_from_interval(bootstrap['ci95Bp']) if bootstrap['available']
                    else 'uncertainty_unavailable')
    return {'start': start, 'end': end, 'expectedDays': len(grid),
            'observedDays': len(observed), 'missingDays': len(grid) - len(observed),
            'coverage': coverage, 'groups': groups, 'missingByGroup': missing,
            'missingByReason': dict(Counter(e for r in grid if not r['mask'] for e in r.get('errors', []))),
            'delta': delta, 'deltaBp': delta * 10000 if delta is not None else None,
            'bootstrap': bootstrap, 'gateErrors': gates, 'decision': decision,
            'dataGatePassed': not gates, 'marketValidated': False,
            'independentHoldout': False, 'outcome': OUTCOME,
            'practicalMarginBp': 10,
            'interpretation': 'G1减G0的收开绝对净变动差异；正负差异不表示价格涨跌方向'}


def overall_decision(periods: list[dict]) -> tuple[str, str]:
    if len(periods) != 2:
        raise ValueError('exactly two registered periods required')
    decisions = [p['decision'] for p in periods]
    if all(d == DECISIONS['positive'] for d in decisions):
        return ('consistently_positive_difference',
                '两段历史的绝对变动差异达到同侧预设门槛，仍需独立验证。')
    if all(d == DECISIONS['negative'] for d in decisions):
        return ('consistently_negative_difference',
                '两段历史的绝对变动差异达到同侧预设门槛，仍需独立验证。')
    if all(d == DECISIONS['inside'] for d in decisions):
        return ('both_within_chosen_margin',
                '两段区间均落在预设门槛内，未发现达到10bp的实质差异。')
    if any(d in ('insufficient_data', 'uncertainty_unavailable') for d in decisions):
        return ('inconclusive_due_to_data_or_uncertainty',
                '至少一期未通过数据或区间门槛，无法判定两段历史是否一致。')
    if not any(d in (DECISIONS['positive'], DECISIONS['negative']) for d in decisions):
        return ('no_consistent_directional_support',
                '按预设标准，未发现两段历史一致支持六冲日绝对变动差异的证据。')
    return ('no_consistent_directional_support', '两段历史未形成一致的差异证据。')


def run(document: dict, protocol: dict) -> tuple[dict, list[dict]]:
    """Score only the fixed contract; caller must pass verification before real data."""
    validate_protocol(protocol)
    metadata = document.get('metadata', {})
    for name, expected in {'timezone': 'Asia/Shanghai', 'symbol': 'BTCUSDT',
                           'marketType': 'spot', 'exchange': 'Binance', 'interval': '1h',
                           'researchDataType': 'real_market', 'calendarValidated': True}.items():
        if metadata.get(name) != expected:
            raise ValueError('pinned market metadata mismatch: ' + name)
    if metadata.get('calendarEngine', {}).get('id') != POLICY:
        raise ValueError('pinned input calendar policy mismatch')
    periods, grids = [], []
    for period in PERIODS:
        grid = build_grid(document, period['start'], period['end'])
        grids.append({**period, 'rows': grid})
        periods.append(summarize_period(grid, **period))
    conclusion, headline = overall_decision(periods)
    result = {
        'schemaVersion': 1, 'version': VERSION, 'scorerVersion': VERSION,
        'status': 'exploration_completed', 'hypothesisId': HYPOTHESIS,
        'hypothesisVersion': '0.1.0', 'featureId': FEATURE, 'featureVersion': '0.1.0',
        'runAtUtc': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
        'profile': PROFILE, 'profilePillars': NATAL,
        'calendarPolicyId': POLICY, 'featureEvaluatedAt': 'each date 00:00:00+08:00',
        'outcome': OUTCOME, 'outcomeMeaning': '收开绝对净变动，不是日内振幅或波动率',
        'estimand': '(mean(y[G1]) - mean(y[G0])) * 10000', 'unit': 'bp',
        'groupMeaning': {'0': '原局四支与流日无六冲', '1': '原局四支与流日至少一条六冲'},
        'groupIndependentCrosscheck': 'BTC原局子子申酉；G1流日支仅寅、卯、午；每天等权只计一次',
        'periods': periods, 'overallDecision': conclusion, 'headline': headline,
        'practicalMarginBp': 10, 'prospective': False, 'independentHoldout': False,
        'priorMarketDataAndOtherStudyResultsAlreadySeen': True,
        'marketValidated': False, 'marketDirection': None, 'marketExecutionEnabled': False,
        'tradingBacktest': False, 'forwardMonitoringStarted': False,
        'limitations': [
            '给定BTC四柱的条件研究；原盘出生时区与日界尚未证实。',
            '历史行情及其他假设结果已看过；两段均非独立留出集。',
            '缺失日保留日历位置且不填值；已观察日之外的变化仍未知。',
            '差异为绝对净变动，不是涨跌信号，也不是日内最高最低振幅。',
            '10bp是预先固定的研究门槛，不代表交易成本或可获得的利润。',
            '观察性关联不能建立因果，也不验证整个八字理论。',
            '此条件等价于寅卯午日的12日循环分组；不是独立机制识别。',
        ],
    }
    return result, grids


def _verify_hash_manifest(base: Path, filename: str) -> dict:
    registry = read_json(base / filename)
    hashes = registry.get('inputHashes')
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('missing registration hashes: ' + filename)
    for name, expected in hashes.items():
        target = (base / name).resolve()
        if not target.is_relative_to(base.resolve()):
            raise ValueError('registered path outside package: ' + name)
        if not isinstance(expected, str) or len(expected) != 64 or digest(target) != expected:
            raise ValueError('registered input changed: ' + filename + ': ' + name)
    return registry


def verify_execution_inputs(base: Path = HERE) -> dict:
    """Fail closed on all old evidence, the new code, and the pinned runtime."""
    base = Path(base)
    old = _verify_hash_manifest(base, 'study_registration.json')
    structural = _verify_hash_manifest(base, 'structure_registration.json')
    execution = _verify_hash_manifest(base, 'execution_registration.json')
    if len(old['inputHashes']) < 18 or len(structural['inputHashes']) < 12:
        raise ValueError('old registration evidence incomplete')
    hashes = execution['inputHashes']
    required = {'study_registration.json', 'structure_registration.json',
                'structure_study_protocol.json', 'structure_scorer.py',
                'test_structure_scorer.py', 'daily_input.json', 'masked_study.py',
                'legacy_scorer.py', 'bazi_features.py', 'bazi_engine.py',
                'calendar_adapter.py', 'calendar_validation.py', 'profiles.json'}
    if not required <= hashes.keys():
        raise ValueError('execution registration missing required code/data hashes')
    runtime = list((base / 'vendor' / 'lunar_python').rglob('*.py'))
    metadata = list((base / 'vendor').glob('lunar_python-*.dist-info/METADATA'))
    if not runtime or len(metadata) != 1:
        raise ValueError('pinned lunar runtime absent or ambiguous')
    if any(p.relative_to(base).as_posix() not in hashes for p in runtime + metadata):
        raise ValueError('execution registration missing lunar runtime content hashes')
    if execution.get('hypothesisId') != HYPOTHESIS or execution.get('scoringRun') is not False:
        raise ValueError('execution registration contract mismatch')
    if execution.get('priorRegistrationSha256') != digest(base / 'structure_registration.json'):
        raise ValueError('execution registration prior hash mismatch')
    if execution.get('validation', {}).get('syntheticTestsPassed') is not True:
        raise ValueError('synthetic tests not confirmed')
    if type(execution.get('validation', {}).get('testCount')) is not int or execution['validation']['testCount'] <= 0:
        raise ValueError('synthetic test count absent')
    protocol = read_json(base / 'structure_study_protocol.json')
    validate_protocol(protocol)
    if protocol['data']['sha256'] != digest(base / 'daily_input.json'):
        raise ValueError('frozen protocol data hash changed')
    calendar = read_json(base / 'calendar_validation_evidence.json')
    tests = read_json(base / 'unit_tests.json')
    if calendar.get('status') != 'passed' or tests.get('passed') is not True:
        raise ValueError('old validation evidence not passed')
    recovery = read_json(base / 'market_recovery.json')
    for request in recovery['requests']:
        if request['status'] == 'downloaded':
            if digest(base / 'recovery_raw' / request['savedFile']) != request['sha256']:
                raise ValueError('market recovery response changed')
    return {'protocol': protocol, 'execution': execution,
            'oldRegistrationFileCount': len(old['inputHashes']),
            'structureRegistrationFileCount': len(structural['inputHashes']),
            'executionRegistrationFileCount': len(hashes),
            'runtimeFilesVerified': len(runtime) + len(metadata),
            'remainingIncompleteDays': recovery['remainingIncompleteDays']}


def main() -> None:
    # No existing output is touched. A failed/partial execution cannot silently
    # restart; the frozen stopping rule requires an explicit new version.
    outputs = ('structure_results.json', 'structure_grid.json', 'execution_started.json')
    if any((HERE / name).exists() for name in outputs):
        raise ValueError('execution/output already exists; retain it and use a new revision')
    verified = verify_execution_inputs()
    receipt = {'hypothesisId': HYPOTHESIS, 'startedAtUtc': datetime.now(timezone.utc).isoformat(),
               'executionRegistrationSha256': digest(HERE / 'execution_registration.json'),
               'stoppingRule': 'single fixed execution; no overwrite or automatic retry'}
    with (HERE / 'execution_started.json').open('xb') as stream:
        stream.write(json_bytes(receipt))
    try:
        document = read_json(HERE / 'daily_input.json')
        result, grids = run(document, verified['protocol'])
        if sum(p['missingDays'] for p in result['periods']) != verified['remainingIncompleteDays']:
            raise ValueError('new missing mask and old recovery totals disagree')
        old_grids = read_json(HERE / 'study_grid.json')
        for new, old in zip(grids, old_grids, strict=True):
            if [(r['date'], r['mask']) for r in new['rows']] != [(r['date'], r['mask']) for r in old]:
                raise ValueError('new calendar mask differs from frozen old mask')
        grid_document = {'schemaVersion': 1, 'hypothesisId': HYPOTHESIS,
                         'outcome': OUTCOME, 'periods': grids}
        raw_grid = json_bytes(grid_document)
        result['gridFile'] = 'structure_grid.json'
        result['gridSha256'] = hashlib.sha256(raw_grid).hexdigest()
        result['dataSha256'] = digest(HERE / 'daily_input.json')
        result['protocolSha256'] = digest(HERE / 'structure_study_protocol.json')
        result['priorRegistrationSha256'] = digest(HERE / 'structure_registration.json')
        result['executionRegistrationSha256'] = digest(HERE / 'execution_registration.json')
        result['verification'] = {k: v for k, v in verified.items() if k not in ('protocol', 'execution')}
        # Check again after computation to detect concurrent input changes.
        verify_execution_inputs()
        for name, raw in (('structure_grid.json', raw_grid), ('structure_results.json', json_bytes(result))):
            with (HERE / name).open('xb') as stream:
                stream.write(raw)
        print(json.dumps({'status': result['status'], 'overallDecision': result['overallDecision'],
                          'periods': [{k: p[k] for k in ('start', 'end', 'deltaBp', 'decision')}
                                      for p in result['periods']]}, ensure_ascii=False))
    except Exception as exc:
        failure = {**receipt, 'failedAtUtc': datetime.now(timezone.utc).isoformat(),
                   'errorType': type(exc).__name__, 'error': str(exc),
                   'retryAllowedInSameVersion': False}
        with (HERE / 'execution_failure.json').open('xb') as stream:
            stream.write(json_bytes(failure))
        raise


if __name__ == '__main__':
    main()
