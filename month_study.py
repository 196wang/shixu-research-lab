"""Fixed, descriptive solar-month conditioning on an immutable current snapshot.

No database/network writes, no natal inference and no parameter search.  Daily
bars crossing a Jie boundary are excluded; monthly observations are interior
whole daily bars rather than exact intraday Jie-to-Jie returns.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import statistics

from bazi_engine import BRANCHES, BRANCH_ELEMENT, stem_info
from calendar_adapter import calculate_calendar, POLICY
from calendar_browser import JIE, terms_in_year
from exploration_generators import LOCAL, DAY, midnight, days_between, inspect_days
from gann_timing import snapshot_hash

VERSION = '0.17.0'
HERE = Path(__file__).resolve().parent
RULES = [
    {'id': 'DAY-STEM-METALWATER-UP', 'label': '日干金水上涨假设', 'direction': 'up',
     'definition': '北京时间00:00日干为庚、辛、壬、癸；当日收盘高于开盘为正确。'},
    {'id': 'DAY-STEM-FIRE-DOWN', 'label': '日干火下跌假设', 'direction': 'down',
     'definition': '北京时间00:00日干为丙、丁；当日收盘低于开盘为正确。'},
    {'id': 'DAY-BRANCH-YIN-DOWN', 'label': '寅日下跌假设', 'direction': 'down',
     'definition': '北京时间00:00日支为寅；当日收盘低于开盘为正确。'},
]
CONDITIONS = [
    {'id': 'ALL', 'label': '全部月份', 'definition': '所有可用节气月'},
    {'id': 'MONTH-STEM-METALWATER', 'label': '月干金水', 'definition': '月干五行为金或水'},
    {'id': 'MONTH-BRANCH-METALWATER', 'label': '月支金水', 'definition': '月支本气五行为金或水'},
    {'id': 'MONTH-BOTH-METALWATER', 'label': '月干与月支均金水', 'definition': '月干、月支各自五行都属于金或水'},
    {'id': 'MONTH-STEM-FIRE', 'label': '月干火', 'definition': '月干五行为火'},
    {'id': 'MONTH-BRANCH-FIRE', 'label': '月支火', 'definition': '月支本气五行为火'},
] + [{'id': 'MONTH-BRANCH-' + branch, 'label': branch + '月', 'definition': '节气月月支为' + branch} for branch in BRANCHES]
FIXED_PROTOCOL = {
    'schemaVersion': 1, 'studyId': 'MONTH-BTC-001', 'version': '0.1.0', 'symbol': 'BTCUSDT',
    'startDate': '2021-01-01', 'endDate': '2026-09-26', 'timezone': 'UTC+8', 'interval': '1d',
    'source': 'exploration_v16/run-001/snapshot.json',
    'pillarSample': 'local_00:00', 'monthBoundary': 'exact_jie_not_zhongqi',
    'dailyBoundaryPolicy': 'exclude_jie_strictly_inside_daily_interval_allow_exact_midnight',
    'dailyHorizonDays': 1, 'dailyReturn': 'close_over_open_minus_one', 'tieInDenominator': True,
    'annualIsolationDays': 0,
    'conditionIds': [item['id'] for item in CONDITIONS], 'ruleIds': [item['id'] for item in RULES],
    'matchedStrata': ['local_gregorian_year', 'solar_month_episode_id'],
    'matchedComplement': 'other_eligible_days_in_same_stratum_excluding_current_rule_triggers',
    'matchedWeight': 'trigger_count_in_strata_with_nonempty_complement',
    'unmatchedPolicy': 'exclude_from_both_matched_trigger_and_matched_baseline_rates_report_counts',
    'monthlyReturn': 'last_close_over_first_open_of_whole_daily_bars_inside_exact_jie_interval_minus_one',
    'monthlyBoundaryPolicy': 'ceil_start_to_midnight_floor_end_to_midnight_end_exclusive',
    'monthlyEligibility': 'exact_episode_inside_study_span_and_all_interior_bars_valid_closed',
    'branchOrder': list(BRANCHES), 'yearPolicy': 'daily_local_date_year_monthly_episode_start_year',
    'parameterSearch': False, 'registeredPrediction': False, 'marketValidated': False,
    'status': 'current_snapshot_historical_exploration',
}


def validate_protocol(protocol):
    if protocol != FIXED_PROTOCOL:
        raise ValueError('只接受固定MONTH-BTC-001协议；不支持动态筛选参数或另选窗口')
    return deepcopy(protocol)


def load_protocol(base=HERE):
    return validate_protocol(json.loads((Path(base) / 'month_study_protocol.json').read_text(encoding='utf-8-sig')))


def condition_ids(pillar):
    stem_element, branch_element = stem_info(pillar[0])['element'], BRANCH_ELEMENT[pillar[1]]
    ids = ['ALL']
    if stem_element in '金水': ids.append('MONTH-STEM-METALWATER')
    if branch_element in '金水': ids.append('MONTH-BRANCH-METALWATER')
    if stem_element in '金水' and branch_element in '金水': ids.append('MONTH-BOTH-METALWATER')
    if stem_element == '火': ids.append('MONTH-STEM-FIRE')
    if branch_element == '火': ids.append('MONTH-BRANCH-FIRE')
    return ids + ['MONTH-BRANCH-' + pillar[1]]


def rule_ids(pillar):
    result = []
    if pillar[0] in '庚辛壬癸': result.append(RULES[0]['id'])
    if pillar[0] in '丙丁': result.append(RULES[1]['id'])
    if pillar[1] == '寅': result.append(RULES[2]['id'])
    return result


def ceil_midnight(instant):
    floor = midnight(instant.astimezone(LOCAL).date())
    return floor if instant == floor else floor + DAY


def daily_transitions(day, boundaries):
    start = midnight(day)
    return [{'term': term, 'at': at.isoformat()} for term, at in boundaries if start < at < start + DAY]


def episode_plan(first, last):
    """Return every exact Jie interval overlapping the inclusive study dates."""
    lo, hi = midnight(first), midnight(last + DAY)
    boundaries = sorted(((name, instant) for year in range(first.year - 1, last.year + 2)
                         for name, instant in terms_in_year(year) if name in JIE), key=lambda item: item[1])
    episodes = []
    for (term, begin), (_, end) in zip(boundaries, boundaries[1:]):
        if begin >= hi or end <= lo: continue
        pillar = calculate_calendar(begin.isoformat())['pillars']['month']
        interior_start, interior_end = ceil_midnight(begin), midnight(end.date())
        observed_start, observed_end = max(interior_start, lo), min(interior_end, hi)
        episodes.append({
            'id': 'JIE-' + begin.strftime('%Y%m%dT%H%M%S'), 'year': begin.year, 'term': term,
            'startAt': begin.isoformat(), 'endAt': end.isoformat(), 'pillar': pillar,
            'stemElement': stem_info(pillar[0])['element'], 'branchElement': BRANCH_ELEMENT[pillar[1]],
            'conditionIds': condition_ids(pillar),
            'interiorStartDate': interior_start.date().isoformat(), 'interiorEndExclusiveDate': interior_end.date().isoformat(),
            'interiorDays': max(0, (interior_end - interior_start).days),
            'observedStartDate': observed_start.date().isoformat(), 'observedEndExclusiveDate': observed_end.date().isoformat(),
            'observedDays': max(0, (observed_end - observed_start).days),
            'partialStart': begin < lo, 'partialEnd': end > hi,
        })
    return episodes, boundaries


def _target_days(protocol):
    return days_between(date.fromisoformat(protocol['startDate']), date.fromisoformat(protocol['endDate']))


def return_pct(opened, closed):
    return float((closed / opened - Decimal(1)) * 100)


def actual_direction(opened, closed):
    return 'up' if closed > opened else 'down' if closed < opened else 'flat'


def pending_bar(day, inspected, generated):
    return (midnight(day + DAY) > generated or
            any(reason in ('not_confirmed_closed', 'not_yet_closed', 'future_bar') for reason in inspected.get('reasons', [])))


def daily_rows(snapshot, targets, index, episodes, boundaries, progress=None):
    result = []
    for i, day in enumerate(targets):
        start = midnight(day)
        episode = next((e for e in episodes if datetime.fromisoformat(e['startAt']) <= start < datetime.fromisoformat(e['endAt'])), None)
        transitions = daily_transitions(day, boundaries)
        valid = index[day]
        reasons = list(valid['reasons'])
        pillars, conditions, rules = None, [], []
        try:
            pillars = calculate_calendar(start.isoformat())['pillars']
            conditions, rules = condition_ids(pillars['month']), rule_ids(pillars['day'])
            if episode is None or pillars['month'] != episode['pillar']:
                reasons.append('calendar_episode_mismatch')
        except Exception as exc:
            reasons.append('calendar_unavailable:' + type(exc).__name__)
        if transitions: reasons.append('month_boundary_inside_bar')
        if transitions: status = 'boundary_excluded'
        elif pillars is None or episode is None or 'calendar_episode_mismatch' in reasons: status = 'calendar_unknown'
        elif valid['valid']: status = 'eligible'
        elif any(r in ('not_confirmed_closed', 'not_yet_closed', 'future_bar') for r in reasons): status = 'pending'
        else: status = 'missing'
        prices = valid['prices'] if status == 'eligible' else None
        original = snapshot[1].get(int(start.timestamp() * 1000))
        result.append({
            'date': day.isoformat(), 'year': day.year, 'atLocal': start.isoformat(),
            'pillars': pillars, 'episodeId': episode['id'] if episode else None,
            'conditionIds': conditions, 'ruleIds': rules, 'status': status, 'reasons': sorted(set(reasons)),
            'monthTransitions': transitions,
            'open': str(prices['open']) if prices else None, 'high': str(prices['high']) if prices else None,
            'low': str(prices['low']) if prices else None, 'close': str(prices['close']) if prices else None,
            'returnPct': return_pct(prices['open'], prices['close']) if prices else None,
            'actual': actual_direction(prices['open'], prices['close']) if prices else None,
            'sourceRef': {k: original.get(k) for k in ('request_id', 'body_sha256', 'updated')} if isinstance(original, dict) else None,
        })
        if progress and (i % 100 == 0 or i + 1 == len(targets)):
            progress({'stage': 'calendar', 'completed': i + 1, 'total': len(targets), 'message': day.isoformat()})
    return result


def stats(rows, direction):
    n = len(rows)
    correct = sum(r['actual'] == direction for r in rows)
    ties = sum(r['actual'] == 'flat' for r in rows)
    returns = [r['returnPct'] for r in rows]
    return {'n': n, 'correct': correct, 'incorrect': n - correct - ties, 'tie': ties,
            'hitRatePct': correct / n * 100 if n else None,
            'meanReturnPct': statistics.mean(returns) if returns else None,
            'medianReturnPct': statistics.median(returns) if returns else None,
            'monthEpisodeCount': len({r['episodeId'] for r in rows}), 'yearCount': len({r['year'] for r in rows})}


def matched_complement(rows, rule):
    """Condition-preserving complements, stratified by civil year AND episode."""
    strata = defaultdict(list)
    for row in rows: strata[(row['year'], row['episodeId'])].append(row)
    matched_rows, audit, weighted = [], [], 0.0
    complement_n, unmatched_n, unmatched_episodes = 0, 0, 0
    for (year, episode_id), members in sorted(strata.items()):
        trigger = [r for r in members if rule['id'] in r['ruleIds']]
        if not trigger: continue
        complement = [r for r in members if rule['id'] not in r['ruleIds']]
        trigger_stats, complement_stats = stats(trigger, rule['direction']), stats(complement, rule['direction'])
        matched = bool(complement)
        audit.append({'year': year, 'episodeId': episode_id, 'triggerN': len(trigger), 'complementN': len(complement),
                      'triggerCorrect': trigger_stats['correct'], 'triggerIncorrect': trigger_stats['incorrect'], 'triggerTie': trigger_stats['tie'],
                      'complementCorrect': complement_stats['correct'], 'complementIncorrect': complement_stats['incorrect'], 'complementTie': complement_stats['tie'],
                      'triggerRatePct': trigger_stats['hitRatePct'], 'complementRatePct': complement_stats['hitRatePct'], 'matched': matched})
        if matched:
            matched_rows.extend(trigger)
            weighted += len(trigger) * complement_stats['hitRatePct']
            complement_n += len(complement)
        else:
            unmatched_n += len(trigger)
            unmatched_episodes += 1
    result = stats(matched_rows, rule['direction'])
    result.update({'evaluated': result['n'], 'triggerHitRatePct': result['hitRatePct'],
                   'baselineRatePct': weighted / result['n'] if result['n'] else None,
                   'complementN': complement_n, 'unmatchedN': unmatched_n, 'unmatched': unmatched_n,
                   'unmatchedEpisodeCount': unmatched_episodes, 'episodeCount': sum(item['matched'] for item in audit),
                   'stratumPolicy': 'local_gregorian_year_and_solar_month_episode_id', 'episodes': audit})
    result['excessPp'] = result['hitRatePct'] - result['baselineRatePct'] if result['n'] else None
    result['deltaPp'] = result['excessPp']
    return result


def condition_summary(eligible):
    full = {r['id']: stats([day for day in eligible if r['id'] in day['ruleIds']], r['direction']) for r in RULES}
    output = []
    for condition in CONDITIONS:
        members = [r for r in eligible if condition['id'] in r['conditionIds']]
        metrics = []
        for rule in RULES:
            metric = stats([r for r in members if rule['id'] in r['ruleIds']], rule['direction'])
            baseline = stats(members, rule['direction'])
            metric.update({'id': rule['id'], 'label': rule['label'], 'direction': rule['direction'],
                           'baseline': baseline, 'baselineRatePct': baseline['hitRatePct'],
                           'matched': matched_complement(members, rule), 'allMonthBaseline': deepcopy(full[rule['id']])})
            metrics.append(metric)
        output.append({'id': condition['id'], 'label': condition['label'], 'rules': metrics})
    return output


def distribution(rows):
    result = stats(rows, 'up')
    n = result['n']
    return {key: result[key] for key in ('n', 'meanReturnPct', 'medianReturnPct', 'monthEpisodeCount', 'yearCount')} | {
        'up': result['correct'], 'down': result['incorrect'], 'tie': result['tie'],
        'upRatePct': result['correct'] / n * 100 if n else None,
        'downRatePct': result['incorrect'] / n * 100 if n else None}


def calendar_context(eligible):
    baseline = stats(eligible, 'down')
    days, months = [], []
    for branch in BRANCHES:
        metric = stats([r for r in eligible if r['pillars']['day'][1] == branch], 'down')
        days.append({'branch': branch, 'metric': metric, 'baselineRatePct': baseline['hitRatePct'],
                     'deltaPp': metric['hitRatePct'] - baseline['hitRatePct'] if metric['n'] and baseline['n'] else None})
        months.append({'branch': branch, **distribution([r for r in eligible if r['pillars']['month'][1] == branch])})
    return {'dayBranches': days, 'monthBranches': months, 'eligibleDownBaseline': baseline,
            'scope': 'all_eligible_study_dates', 'independentCorroboration': False}


def evaluate_episodes(episodes, index, generated):
    result = []
    for plan in episodes:
        item = deepcopy(plan)
        first, end = date.fromisoformat(item['observedStartDate']), date.fromisoformat(item['observedEndExclusiveDate'])
        observed = days_between(first, end - DAY) if first < end else []
        invalid = [{'date': day.isoformat(), 'reasons': index.get(day, {}).get('reasons', ['outside_snapshot_span'])}
                   for day in observed if not index.get(day, {}).get('valid', False)]
        pending = any(pending_bar(day, index.get(day, {}), generated) for day in observed)
        partial = item['partialStart'] or item['partialEnd']
        reasons = (['partial_start'] if item['partialStart'] else []) + (['partial_end'] if item['partialEnd'] else [])
        if not observed: reasons.append('no_whole_daily_bars')
        if invalid: reasons.append('invalid_interior_bars')
        if pending: reasons.append('interior_bars_not_mature')
        status = 'partial' if partial else 'pending' if pending else 'missing' if invalid or not observed else 'complete'
        observed_ok = bool(observed) and not invalid and not pending
        opened = index[observed[0]]['prices']['open'] if observed_ok else None
        closed = index[observed[-1]]['prices']['close'] if observed_ok else None
        observed_return = return_pct(opened, closed) if observed_ok else None
        item.update({'status': status, 'reasons': reasons, 'missingDates': invalid,
                     'validObservedDays': len(observed) - len(invalid),
                     'open': str(opened) if status == 'complete' else None, 'close': str(closed) if status == 'complete' else None,
                     'returnPct': observed_return if status == 'complete' else None,
                     'observedOpen': str(opened) if observed_ok else None, 'observedClose': str(closed) if observed_ok else None,
                     'observedReturnPct': observed_return,
                     'actual': actual_direction(opened, closed) if status == 'complete' else None,
                     'returnDefinition': 'whole_daily_bars_inside_exact_jie_interval_not_exact_intraday_jie_return'})
        result.append(item)
    return result


def monthly_summary(episodes):
    result = []
    for condition in CONDITIONS:
        members = [e for e in episodes if condition['id'] in e['conditionIds']]
        complete = [e for e in members if e['status'] == 'complete']
        values = [e['returnPct'] for e in complete]
        n = len(complete)
        up, down, tie = (sum(e['actual'] == k for e in complete) for k in ('up', 'down', 'flat'))
        result.append({'id': condition['id'], 'label': condition['label'], 'n': n, 'up': up, 'down': down, 'tie': tie,
                       'upRatePct': up / n * 100 if n else None, 'downRatePct': down / n * 100 if n else None,
                       'meanReturnPct': statistics.mean(values) if values else None, 'medianReturnPct': statistics.median(values) if values else None,
                       'yearCount': len({e['year'] for e in complete}), 'excludedEpisodes': len(members) - n,
                       'partialEpisodes': sum(e['status'] == 'partial' for e in members),
                       'missingEpisodes': sum(e['status'] == 'missing' for e in members),
                       'pendingEpisodes': sum(e['status'] == 'pending' for e in members)})
    return result


def build_report(snapshot, protocol, generated_at, progress=None):
    protocol = validate_protocol(protocol)
    if not isinstance(generated_at, datetime) or generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError('generated_at必须是带时区的真实快照捕获时间')
    if not isinstance(snapshot, (list, tuple)) or len(snapshot) != 3 or not all(isinstance(part, dict) for part in snapshot):
        raise ValueError('snapshot必须是(state,candles,gaps)')
    generated = generated_at.astimezone(timezone.utc)
    first, last = date.fromisoformat(protocol['startDate']), date.fromisoformat(protocol['endDate'])
    index = inspect_days(snapshot, first, last, generated)
    episodes, boundaries = episode_plan(first, last)
    rows = daily_rows(snapshot, _target_days(protocol), index, episodes, boundaries, progress)
    # Pending is a chronology property, even when the source's precise reason name differs.
    for row in rows:
        if row['status'] == 'missing' and midnight(date.fromisoformat(row['date']) + DAY) > generated:
            row['status'] = 'pending'
    eligible = [row for row in rows if row['status'] == 'eligible']
    conditions = condition_summary(eligible)
    years = []
    for year in sorted({row['year'] for row in rows}):
        years.append({'year': year, 'conditions': condition_summary([row for row in eligible if row['year'] == year])})
        if progress: progress({'stage': 'summary', 'completed': len(years), 'total': len({r['year'] for r in rows}), 'message': str(year)})
    episode_results = evaluate_episodes(episodes, index, generated)
    examples = [e for e in episode_results if e['year'] == 2026 and e['term'] in ('立秋', '白露')]
    counts = Counter(row['status'] for row in rows)
    source_complete = sum(value['valid'] for value in index.values())
    source_pending = sum(not value['valid'] and pending_bar(day, value, generated) for day, value in index.items())
    return {
        'schemaVersion': 1, 'version': VERSION, 'studyId': protocol['studyId'], 'generatedAtUtc': generated.isoformat(),
        'status': 'exploratory', 'symbol': protocol['symbol'], 'marketValidated': False, 'registeredPrediction': False,
        'range': {'startDate': protocol['startDate'], 'endDate': protocol['endDate']},
        'source': {'provider': 'Binance spot public cache', 'timezone': 'UTC+8', 'interval': '1d',
                   'lastSuccessAtUtc': snapshot[0].get('last_success'), 'snapshotSha256': snapshot_hash(snapshot),
                   'snapshotHashMeaning': 'canonical_complete_state_candles_gaps_not_export_file_bytes',
                   'sourceReference': protocol['source'], 'completeDays': source_complete,
                   'missingDays': len(index) - source_complete - source_pending, 'pendingDays': source_pending,
                   'countScope': 'study_target_dates_before_month_boundary_exclusion'},
        'protocolSha256': snapshot_hash(protocol), 'calendarPolicy': deepcopy(POLICY),
        'rules': deepcopy(RULES), 'conditions': deepcopy(CONDITIONS),
        'daily': {'conditions': conditions, 'years': years}, 'context': calendar_context(eligible),
        'monthly': {'conditions': monthly_summary(episode_results), 'episodes': episode_results}, 'examples': examples,
        'quality': {'targetDays': len(rows), 'eligibleDays': len(eligible), 'statusCounts': dict(counts),
                    'monthlyEpisodeCount': len(episode_results), 'monthlyStatusCounts': dict(Counter(e['status'] for e in episode_results)),
                    'conditionGroupsOverlap': True, 'branchGroupsPartitionEligibleDays': True,
                    'unmatchedPolicy': protocol['unmatchedPolicy']},
        'limitations': [
            '复用已经看过的固定行情快照做历法条件探索；不是独立盲样本、事前预测或交易收益检验。',
            '全部固定条件同时展示，元素条件互有重叠；不能相加，也不按结果挑选最佳月份或调整权重。',
            '日线内跨节气换月的日期剔除，恰好00:00交节归新月；本研究掩码与v0.16不同，绝对命中率不能直接比较。',
            '匹配对照只用同公历年、同节气月内该规则未触发的合格日；无补集样本同时退出匹配两侧分母并单列。',
            '日级可纳入边缘不完整节气月中的完整日线；月级收益仅纳入整个节气月在研究区间内且所有内部日线有效的月份。',
            '节气月内完整日线区间收益不等于精确交节时刻到交节时刻的收益；部分月份仅单独显示观察收益。',
            '寅日与寅月的相关描述不构成独立佐证；寅申冲未在此建立或证实涨跌机制。',
            '保留平盘、缺失与未收盘状态；未计算显著性、p值、交易成本、资金曲线或盈利能力。',
        ],
        'files': {'details': '/api/month-study-details', 'protocol': '/api/month-study-protocol', 'registration': '/api/month-study-registration'},
        'details': {'daily': rows, 'monthEpisodes': episode_results,
                    'boundaryEvents': [{'term': term, 'at': at.isoformat()} for term, at in boundaries if midnight(first) <= at < midnight(last + DAY)]},
    }
