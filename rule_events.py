"""Four fixed daily features and descriptive historical windows, without trades."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import statistics
from urllib.parse import parse_qs

from bazi_engine import stem_info
from bazi_features import calculate_features
from calendar_adapter import calculate_relations
from historical_chart import ChartIntegrityError, HERE, PROFILE, SYMBOL, file_sha, load_history, read_json

VERSION = '0.11.0'
HORIZONS = (1, 3, 7, 30)
RULE_IDS = ('BZ-E01', 'BZ-E02', 'BZ-E03', 'BZ-E04')
PROTOCOL = {
    'id': 'BZ-EVENT-REVIEW-001', 'version': '0.1.0', 'profile': PROFILE, 'symbol': SYMBOL,
    'status': 'fixed_descriptive_historical_review', 'notProspectivePreregistration': True,
    'featureInstant': '00:00:00+08:00', 'calendarPolicyId': 'BZ-CALENDAR-CIVIL-00-V01',
    'rules': [
        {'id': 'BZ-E01', 'version': '0.1.0', 'condition': 'flow.day.stem in 庚辛壬癸'},
        {'id': 'BZ-E02', 'version': '0.1.0', 'condition': 'flow.day.stem in 丙丁'},
        {'id': 'BZ-E03', 'version': '0.1.0', 'condition': 'frozen BZ-S02.value is true'},
        {'id': 'BZ-E04', 'version': '0.1.0', 'condition': 'frozen BZ-S01.value is true'},
    ],
    'horizons': list(HORIZONS),
    'window': 'trigger day open to close of calendar day h; trigger day counts as day 1',
    'returnPct': '(last_close / first_open - 1) * 100',
    'maxDrawdownPct': 'min over [first_open, daily_close_1, ..., daily_close_h] of (value / running_peak - 1) * 100; non-positive',
    'drawdownScope': 'close_path_including_initial_open; no intraday high/low drawdown',
    'missingPolicy': 'any incomplete day makes the whole window missing; no filling',
    'tailPolicy': 'window end after dataset end => pending/dataset_tail; after historical period end => pending/period_boundary',
    'baseline': 'same rule non-trigger dates in exactly the same period, horizon and complete-window mask policy',
    'periods': [
        {'id': 'historical_2020_2024', 'start': '2020-01-01', 'end': '2024-12-31', 'alreadyViewed': True},
        {'id': 'historical_2025_2026', 'start': '2025-01-01', 'end': '2026-09-26', 'alreadyViewed': True},
    ],
    'summaryMetrics': ['eligible', 'scored', 'missing', 'pending', 'pendingReasons',
                       'meanReturnPct', 'medianReturnPct', 'meanMaxDrawdownPct'],
    'comparison': 'triggered mean minus non-trigger mean, percentage points; descriptive only',
    'overlapPolicy': 'overlapping windows retained and dependent; no significance or independent-sample claim',
    'futureValidation': {'reservedStart': '2026-09-28', 'status': 'not_started', 'collected': False, 'executed': False},
    'parameterSearch': False, 'significanceTesting': False, 'tradingPerformance': False,
    'marketValidated': False, 'marketDirection': None,
}
MODULE_FILES = ('rule_events.py', 'build_rule_events.py', 'test_rule_events.py', 'rule_event_catalog.json')
REGISTER_FILE = 'rule_event_registration.json'
STARTED_FILE = 'rule_events_started.json'
RESULT_FILE = 'rule_events.json'


class RuleEventIntegrityError(ChartIntegrityError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def parse_event_query(query):
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    if any(len(v) != 1 for v in values.values()):
        raise ValueError('参数不能重复')
    if set(values) != {'symbol', 'profile'}:
        raise ValueError('规则事件需要且仅接受symbol和profile参数')
    args = {key: value[0] for key, value in values.items()}
    if args != {'symbol': SYMBOL, 'profile': PROFILE}:
        raise ValueError('本批规则仅支持BTCUSDT和BTC四柱版本01')
    return args


def validate_catalog(catalog):
    rules = catalog.get('rules', [])
    if catalog.get('profile') != PROFILE or [r.get('id') for r in rules] != list(RULE_IDS):
        raise RuleEventIntegrityError('规则目录盘版本或4条规则顺序不匹配')
    if any(rule.get('version') != '0.1.0' for rule in rules):
        raise RuleEventIntegrityError('规则目录版本不匹配')


def input_hashes(base=HERE):
    base = Path(base)
    history = load_history(base)
    catalog = read_json(base / 'rule_event_catalog.json')
    validate_catalog(catalog)
    hashes = dict(history['source']['inputHashes'])
    # Pin the actual derived chart bytes, the historical metadata, and every
    # locally bundled calendar implementation already frozen in v0.8.
    old = read_json(base / 'execution_registration.json')['inputHashes']
    for name, expected in old.items():
        if name.startswith('vendor/lunar_python'):
            actual = file_sha(base / name)
            if actual != expected:
                raise RuleEventIntegrityError(f'历法依赖哈希不匹配：{name}')
            hashes[name] = actual
    for name in MODULE_FILES + ('chart_history.json', 'execution_registration.json'):
        hashes[name] = file_sha(base / name)
    # Catalog citations are immutable local evidence, not instructions.
    for source in catalog.get('sources', []):
        if source.get('path') and source.get('sha256'):
            relative = Path(source['path'])
            if relative.is_absolute() or '..' in relative.parts:
                raise RuleEventIntegrityError('规则来源路径必须位于资料包内')
            actual = file_sha(base / relative)
            if actual != source['sha256']:
                raise RuleEventIntegrityError(f'规则来源哈希不匹配：{source["path"]}')
            hashes[relative.as_posix()] = actual
    return hashes


def verify_registration(base=HERE):
    base = Path(base)
    registration = read_json(base / REGISTER_FILE)
    expected_digest = registration.get('registrationSha256')
    if digest({k: v for k, v in registration.items() if k != 'registrationSha256'}) != expected_digest:
        raise RuleEventIntegrityError('规则注册内容哈希不匹配')
    if registration.get('protocol') != PROTOCOL or registration.get('scoringRun') is not False:
        raise RuleEventIntegrityError('规则注册的指标/窗口/状态与冻结版本不一致')
    actual = input_hashes(base)
    if registration.get('inputHashes') != actual:
        wrong = [name for name in set(actual) | set(registration.get('inputHashes', {}))
                 if registration.get('inputHashes', {}).get(name) != actual.get(name)]
        raise RuleEventIntegrityError('规则注册输入哈希不匹配：' + ', '.join(sorted(wrong)[:5]))
    return registration


def matches_at(at, profile=PROFILE):
    """Trigger computation accepts no price or outcome inputs."""
    if profile != PROFILE:
        raise ValueError('本批规则仅支持BTC四柱版本01')
    relation = calculate_relations(profile, at)
    calendar = relation['calendar']
    if calendar['atResearchTimezone'][11:] != '00:00:00+08:00':
        raise ValueError('本批事件仅按北京时间每日00:00计算')
    day = calendar['pillars']['day']
    element = stem_info(day[0])['element']
    matches = []
    for identity, allowed, label in (('BZ-E01', '庚辛壬癸', '金水'), ('BZ-E02', '丙丁', '火')):
        if day[0] in allowed:
            matches.append({'ruleId': identity, 'reason': f'流日{day}的日干{day[0]}属{element}，符合日干{label}口径。',
                            'dayPillar': day, 'dayStem': day[0], 'dayStemElement': element, 'edges': []})
    structural = calculate_features(relation['relationships'], calendar)
    tokens = {token['slot']: token for token in relation['relationships']['tokens']}
    for identity, feature_id, label in (('BZ-E03', 'BZ-S02', '六冲'), ('BZ-E04', 'BZ-S01', '六合')):
        feature = next(f for f in structural['features'] if f['id'] == feature_id)
        if not feature['value']:
            continue
        edges = []
        for edge in feature['evidence']:
            natal_slot, flow_slot = edge['slots']
            edges.append({**edge, 'natalSlot': natal_slot, 'flowSlot': flow_slot,
                          'natalBranch': tokens[natal_slot]['branch'], 'flowBranch': tokens[flow_slot]['branch']})
        matches.append({'ruleId': identity, 'featureId': feature_id,
                        'reason': f'流日支{day[1]}与原局形成{len(edges)}处{label}，当天仅记一个触发事件。',
                        'dayPillar': day, 'edges': edges})
    return {'matches': matches, 'pillars': calendar['pillars'], 'atLocal': calendar['atResearchTimezone']}


def window_outcome(rows, index, horizon, period_end=None):
    if horizon not in HORIZONS or isinstance(horizon, bool) or not 0 <= index < len(rows):
        raise ValueError('窗口或起点无效')
    start = date.fromisoformat(rows[index]['date'])
    end = start + timedelta(days=horizon - 1)
    outcome = {'status': None, 'startDate': start.isoformat(), 'endDate': end.isoformat(),
               'horizonDays': horizon, 'returnPct': None, 'maxDrawdownPct': None,
               'reason': None, 'missingDates': []}
    if index + horizon > len(rows):
        return {**outcome, 'status': 'pending', 'reason': 'dataset_tail'}
    if period_end is not None and end > date.fromisoformat(period_end):
        return {**outcome, 'status': 'pending', 'reason': 'period_boundary'}
    window = rows[index:index + horizon]
    for offset, row in enumerate(window):
        if row['date'] != (start + timedelta(days=offset)).isoformat():
            raise RuleEventIntegrityError('窗口日期不连续；禁止压缩缺口')
    missing = [r['date'] for r in window if not r['complete']]
    if missing:
        return {**outcome, 'status': 'missing', 'reason': 'incomplete_calendar_day', 'missingDates': missing}
    opening = window[0]['open']
    path = [opening] + [row['close'] for row in window]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in path):
        raise RuleEventIntegrityError('完整窗口含无效价格')
    peak, worst = opening, 0.0
    for close in path[1:]:
        peak = max(peak, close)
        worst = min(worst, (close / peak - 1) * 100)
    return {**outcome, 'status': 'scored', 'returnPct': (path[-1] / opening - 1) * 100,
            'maxDrawdownPct': worst}


def describe_windows(outcomes):
    scored = [value for value in outcomes if value['status'] == 'scored']
    returns = [value['returnPct'] for value in scored]
    drawdowns = [value['maxDrawdownPct'] for value in scored]
    return {'eligible': len(outcomes), 'scored': len(scored),
            'missing': sum(value['status'] == 'missing' for value in outcomes),
            'pending': sum(value['status'] == 'pending' for value in outcomes),
            'pendingReasons': {reason: sum(value['status'] == 'pending' and value['reason'] == reason for value in outcomes)
                               for reason in ('period_boundary', 'dataset_tail')},
            'meanReturnPct': statistics.mean(returns) if returns else None,
            'medianReturnPct': statistics.median(returns) if returns else None,
            'meanMaxDrawdownPct': statistics.mean(drawdowns) if drawdowns else None}


def summarize(daily, rows, periods=None):
    results = []
    for period in periods or PROTOCOL['periods']:
        indices = [i for i, row in enumerate(rows) if period['start'] <= row['date'] <= period['end']]
        by_horizon = {h: {i: window_outcome(rows, i, h, period_end=period['end']) for i in indices} for h in HORIZONS}
        for identity in RULE_IDS:
            triggered_indices = {i for i in indices if any(m['ruleId'] == identity for m in daily[i]['matches'])}
            for horizon in HORIZONS:
                triggered = describe_windows([by_horizon[horizon][i] for i in indices if i in triggered_indices])
                baseline = describe_windows([by_horizon[horizon][i] for i in indices if i not in triggered_indices])
                def delta(key):
                    a, b = triggered[key], baseline[key]
                    return a - b if a is not None and b is not None else None
                results.append({'ruleId': identity, 'periodId': period['id'], 'start': period['start'], 'end': period['end'],
                                'horizonDays': horizon, 'triggered': triggered, 'baseline': baseline,
                                'comparison': {'meanReturnDeltaPctPoints': delta('meanReturnPct'),
                                               'meanMaxDrawdownDeltaPctPoints': delta('meanMaxDrawdownPct')},
                                'independentHoldout': False, 'marketValidated': False})
    return results


def compute_result(history, catalog, registration):
    rows = history['rows']
    daily = []
    for index, row in enumerate(rows):
        match = matches_at(row['atLocal'])
        if match['pillars'] != row['pillars']:
            raise RuleEventIntegrityError(f'{row["date"]}事件历法与历史图表不一致')
        daily.append({'date': row['date'], 'atLocal': row['atLocal'], 'matches': match['matches'],
                      'pillars': match['pillars'],
                      'horizons': {str(h): window_outcome(rows, index, h) for h in HORIZONS}})
    result = {'schemaVersion': 1, 'version': VERSION, 'symbol': SYMBOL, 'profile': PROFILE,
              'protocol': PROTOCOL, 'catalog': catalog, 'daily': daily, 'summary': summarize(daily, rows),
              'range': history['range'], 'coverage': history['coverage'],
              'integrity': {'registrationSha256': registration['registrationSha256'],
                            'dailyInputSha256': registration['inputHashes']['daily_input.json'],
                            'chartHistorySha256': registration['inputHashes']['chart_history.json'],
                            'inputHashes': registration['inputHashes']},
              'marketDirection': None, 'marketValidated': False,
              'limitations': ['两段历史均已看过，不是独立留出验证。',
                             '窗口可能重叠，样本相互依赖；仅作固定口径描述统计。',
                             '收益没有费用、滑点或下单过程，不是交易策略绩效。',
                             '最大回撤仅沿起始开盘价与每日收盘价路径，未刻画日内极端波动。',
                             '未来验证只预留日期，尚未采集或执行。']}
    result['contentSha256'] = digest(result)
    return result


@lru_cache(maxsize=2)
def _decode_result(text):
    result = json.loads(text)
    if digest({k: v for k, v in result.items() if k != 'contentSha256'}) != result.get('contentSha256'):
        raise RuleEventIntegrityError('规则事件结果内容哈希不匹配')
    return result


def load_rule_events(base=HERE):
    base = Path(base)
    registration = verify_registration(base)
    started = read_json(base / STARTED_FILE)
    if started.get('registrationSha256') != registration['registrationSha256']:
        raise RuleEventIntegrityError('规则执行回执与注册不匹配')
    try:
        result = _decode_result((base / RESULT_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError) as exc:
        raise RuleEventIntegrityError('规则事件结果尚未生成或格式错误') from exc
    if result.get('protocol') != PROTOCOL or result.get('integrity', {}).get('registrationSha256') != registration['registrationSha256']:
        raise RuleEventIntegrityError('规则事件结果与当前注册不一致')
    if result.get('integrity', {}).get('inputHashes') != registration['inputHashes']:
        raise RuleEventIntegrityError('规则事件结果输入哈希与注册不一致')
    return result
