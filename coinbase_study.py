"""Fixed retrospective BTC/USD rule comparison on complete Coinbase Beijing days.

ETH/USD repeats the calendar calculations only as a cross-asset control. This
module never registers historical predictions or changes the live ledger.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json

from exploration_generators import (DAY, astronomical_events, days_between,
                                    gann_availability, midnight, pivot_kind, sequential_gann)
from gann_timing import validate_row, snapshot_hash
from month_study import (CONDITIONS, RULES, condition_summary, daily_rows,
                         episode_plan, stats)

HERE = Path(__file__).resolve().parent
RULE_BY_ID = {r['id']: r for r in RULES}
ASTRO_IDS = ('ASTRO-MOON-PHASE', 'ASTRO-NAKSHATRA-INGRESS')
TIMING_IDS = ('GANN-EQUAL-INTERVAL', *ASTRO_IDS)


def load_protocol(base=HERE):
    value = json.loads((Path(base) / 'coinbase_study_protocol.json').read_text(encoding='utf-8'))
    if value['studyId'] != 'CB-USD-HX-001' or value['symbols'] != ['BTCUSD', 'ETHUSD']:
        raise ValueError('Coinbase 回测协议不符合固定版本')
    return value


def inspect(snapshot, first, last, generated):
    state, candles, gaps = snapshot
    symbol = state['symbol']
    result = {}
    for day in days_between(first, last):
        key = int(midnight(day).timestamp() * 1000)
        reasons, prices = validate_row(candles.get(key), midnight(day), symbol, generated)
        reasons += gaps.get(key, [])
        result[day] = {'valid': not reasons, 'reasons': sorted(set(reasons)),
                       'prices': prices if not reasons else None}
    return result


def timing_label(day, index, block_first, block_last):
    if day - 5 * DAY < block_first or day + 5 * DAY > block_last:
        return None, 'block_boundary'
    if any(not index.get(day + offset * DAY, {}).get('valid') for offset in range(-5, 6)):
        return None, 'missing_pivot_neighborhood'
    candidates = []
    for offset in range(-3, 4):
        candidate_day = day + offset * DAY
        kind = pivot_kind(index, candidate_day)
        if kind in ('high', 'low'):
            candidates.append((abs(offset), candidate_day, kind))
    if not candidates:
        return {'hit': False, 'errorDays': None, 'pivotDate': None, 'pivotKind': None}, None
    distance, pivot_day, kind = min(candidates)
    return {'hit': True, 'errorDays': distance, 'pivotDate': pivot_day.isoformat(), 'pivotKind': kind}, None


def direction_for(row):
    directions = {RULE_BY_ID[r]['direction'] for r in row['ruleIds']}
    return next(iter(directions)) if len(directions) == 1 else 'conflict' if directions else None


def summarize_direction(rows):
    eligible = [r for r in rows if r['status'] == 'eligible']
    conditions = condition_summary(eligible)
    selected = {c['id']: c for c in conditions}
    ids = load_protocol()['monthConditionIds']
    rule_results = []
    for condition_id in ids:
        condition = selected[condition_id]
        rule_results.append({'id': condition_id, 'label': condition['label'],
                             'eligibleDays': len([r for r in eligible if condition_id in r['conditionIds']]),
                             'rules': [{k: metric[k] for k in ('id', 'label', 'direction', 'n', 'correct', 'incorrect', 'tie',
                                                                'hitRatePct', 'baselineRatePct', 'meanReturnPct', 'medianReturnPct', 'matched')}
                                       for metric in condition['rules']]})
    baseline = {'up': stats(eligible, 'up'), 'down': stats(eligible, 'down')}
    return {'eligibleDays': len(eligible), 'baseline': baseline, 'conditions': rule_results}


def group_name(row):
    direction = row['direction']
    time_ids = row['timeRuleIds']
    if not row['timeKnown']:
        return 'unknown_time_inputs'
    if direction == 'conflict':
        return 'direction_conflict'
    if direction and time_ids:
        return 'bazi_and_timing'
    if direction:
        return 'bazi_only'
    if time_ids:
        return 'timing_only'
    return 'none'


def confluence_summary(rows):
    groups = []
    for identity in ('bazi_and_timing', 'bazi_only', 'timing_only', 'none', 'direction_conflict', 'unknown_time_inputs'):
        members = [r for r in rows if r['group'] == identity and r['status'] == 'eligible']
        directional = [r for r in members if r['direction'] in ('up', 'down')]
        correct = sum(r['direction'] == r['actual'] for r in directional)
        ties = sum(r['actual'] == 'flat' for r in directional)
        groups.append({'id': identity, 'n': len(members), 'directionEvaluated': len(directional),
                       'directionCorrect': correct, 'directionTie': ties,
                       'directionHitRatePct': correct / len(directional) * 100 if directional else None,
                       'timingEvaluated': sum(r['timing'] is not None for r in members),
                       'timingWithin3': sum(bool(r['timing'] and r['timing']['hit']) for r in members)})
    return groups


def timing_summary(rows):
    eligible = [r for r in rows if r['timeKnown'] and r['timing'] is not None]
    eligible_dates = {date.fromisoformat(r['date']) for r in eligible}
    result = []
    groups = [('all_dates', lambda r: True),
              ('gann', lambda r: 'GANN-EQUAL-INTERVAL' in r['timeRuleIds']),
              ('moon_phase', lambda r: 'ASTRO-MOON-PHASE' in r['timeRuleIds']),
              ('nakshatra_ingress', lambda r: 'ASTRO-NAKSHATRA-INGRESS' in r['timeRuleIds']),
              ('gann_and_astro', lambda r: 'GANN-EQUAL-INTERVAL' in r['timeRuleIds'] and any(x in r['timeRuleIds'] for x in ASTRO_IDS)),
              ('bazi_and_timing', lambda r: r['group'] == 'bazi_and_timing')]
    for identity, test in groups:
        subset = [r for r in eligible if test(r)]
        covered = {date.fromisoformat(r['date']) + i * DAY for r in subset for i in range(-3, 4)
                   if date.fromisoformat(r['date']) + i * DAY in eligible_dates}
        exact = sum(r['timing']['errorDays'] == 0 for r in subset)
        hits = sum(r['timing']['hit'] for r in subset)
        pivots = {(r['timing']['pivotDate'], r['timing']['pivotKind']) for r in subset if r['timing']['hit']}
        result.append({'id': identity, 'n': len(subset), 'exact': exact, 'within3': hits,
                       'exactRatePct': exact / len(subset) * 100 if subset else None,
                       'within3RatePct': hits / len(subset) * 100 if subset else None,
                       'uniqueMatchedPivots': len(pivots), 'coveredEligibleDays': len(covered),
                       'coveragePct': len(covered) / len(eligible) * 100 if eligible else None})
    return {'commonEligibleDays': len(eligible), 'metrics': result}


def build_symbol(snapshot, protocol, generated, astronomy, astro_events, episodes, boundaries):
    state = snapshot[0]
    symbol = state['symbol']
    first, last = date.fromisoformat(protocol['startDate']), date.fromisoformat(protocol['endDate'])
    source_first = date.fromisoformat(protocol['sourceStartDate'])
    index = inspect(snapshot, source_first, last, generated)
    targets = days_between(first, last)
    calendar = daily_rows(snapshot, targets, index, episodes, boundaries)
    anchors, gann_events = sequential_gann(index, first, last, generated,
                                           protocol['gann']['lookbackDays'], tuple(protocol['gann']['multipliers']), symbol)
    by_date = defaultdict(set)
    for event in gann_events:
        if event['eligible']:
            by_date[event['targetDate']].add('GANN-EQUAL-INTERVAL')
    for event in astro_events:
        by_date[event['targetDate']].add(event['ruleId'])
    blocks = [(date.fromisoformat(b['start']), date.fromisoformat(b['end'])) for b in protocol['reportBlocks']]
    rows = []
    for row in calendar:
        day = date.fromisoformat(row['date'])
        gann_known, _ = gann_availability(index, day, tuple(protocol['gann']['availabilityOffsets']))
        astro_known = astronomy[row['date']]['known']
        block = next((b for b in blocks if b[0] <= day <= b[1]), None)
        label, reason = timing_label(day, index, *block) if block else (None, 'outside_block')
        known = gann_known and astro_known and reason is None
        row.update({'direction': direction_for(row), 'timeRuleIds': sorted(by_date[row['date']]),
                    'timeKnown': known, 'timeUnknownReason': reason if reason else
                    'gann_source_gap' if not gann_known else 'astronomy_unavailable' if not astro_known else None,
                    'timing': label if known else None})
        row['group'] = group_name(row)
        rows.append(row)
    summaries = []
    for b in protocol['reportBlocks']:
        selected = [r for r in rows if b['start'] <= r['date'] <= b['end']]
        summaries.append({'id': b['id'], 'range': b, 'quality': {'days': len(selected),
                          'statusCounts': dict(Counter(r['status'] for r in selected)),
                          'timeKnownDays': sum(r['timeKnown'] for r in selected)},
                          'direction': summarize_direction(selected),
                          'timing': timing_summary(selected), 'confluence': confluence_summary(selected)})
    source = {'provider': 'Coinbase Exchange', 'marketType': 'spot', 'symbol': symbol,
              'snapshotSha256': snapshot_hash(snapshot), 'lastSuccessAtUtc': state['last_success'],
              'completeSourceDays': sum(r['valid'] for r in index.values()),
              'missingSourceDays': sum(not r['valid'] for r in index.values()),
              'sourceExclusions': [{'date': d.isoformat(), 'reasons': r['reasons']} for d, r in index.items() if not r['valid']]}
    return ({'symbol': symbol, 'role': 'primary_hypothesis' if symbol == protocol['primarySymbol'] else 'cross_asset_control',
             'source': source, 'blocks': summaries,
             'eventCounts': {'gannAnchors': len(anchors), 'gannProjections': len(gann_events),
                             'gannEligible': sum(e['eligible'] for e in gann_events), 'astro': len(astro_events)}},
            {'symbol': symbol, 'daily': rows, 'gannAnchors': anchors, 'gannProjections': gann_events})


def build(snapshots, protocol, generated):
    if generated.tzinfo is None:
        raise ValueError('generated timestamp needs timezone')
    first, last = date.fromisoformat(protocol['startDate']), date.fromisoformat(protocol['endDate'])
    episodes, boundaries = episode_plan(first, last)
    astronomy, astro_events = astronomical_events(first, last, generated)
    symbols, details = [], []
    for symbol in protocol['symbols']:
        if snapshots[symbol][0]['symbol'] != symbol:
            raise ValueError('snapshot symbol mismatch')
        summary, detail = build_symbol(snapshots[symbol], protocol, generated, astronomy, astro_events, episodes, boundaries)
        symbols.append(summary)
        details.append(detail)
    report = {'schemaVersion': 1, 'studyId': protocol['studyId'], 'generatedAtUtc': generated.isoformat(),
              'protocolSha256': snapshot_hash(protocol), 'status': 'retrospective', 'registeredPrediction': False,
              'independentBlindSample': False, 'marketValidated': False, 'symbols': symbols,
              'rules': {'direction': RULES, 'timing': list(TIMING_IDS)},
              'limitations': [
                  '规则来自此前已看过的市场资料；2026 也是事后复盘，不能当成独立盲样本。',
                  'ETH 仅作跨资产对照，沿用 BTC 历法方向条件不代表建立了 ETH 预测规则。',
                  '江恩与星象只提供时间条件，不单独推断上涨、下跌、高点或低点。',
                  '±3 日转点窗口可能自然覆盖大量转点，须同时看全日基准及覆盖率。',
                  '共振为同日出现，时间信号本身无方向；高命中或小样本差异不能证明因果或盈利。',
                  '日线缺小时、节气日内换月及不可用的时间输入均排除并计数；不把缺失记为失败。',
                  '未计算交易成本、资金曲线或独立前瞻样本，也未据此选择最优参数。']}
    return report, {'symbols': details, 'astronomyEvents': astro_events,
                    'astronomyUnknownDates': [d for d, r in astronomy.items() if not r['known']]}
