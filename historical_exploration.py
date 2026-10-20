"""Fixed-protocol descriptive exploration of a real, current cache snapshot."""
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics

from gann_timing import snapshot_hash
from prediction_scoring import score_prediction
from exploration_generators import (RULES, LOCAL, DAY, midnight, days_between, inspect_days, sequential_gann,
                                    gann_availability, calendar_events, astronomical_events, deduplicate_events, pivot_id)

VERSION = '0.16.0'
HERE = Path(__file__).resolve().parent
FIXED_PROTOCOL = {
    'schemaVersion': 1, 'studyId': 'HX-BTC-001', 'version': '0.1.0', 'symbol': 'BTCUSDT',
    'provider': 'Binance spot public cache', 'timezone': 'UTC+8', 'interval': '1d',
    'sourceStartDate': '2020-01-01', 'startDate': '2021-01-01', 'endDate': '2026-09-26',
    'isolationDays': 32, 'horizons': [1, 3, 7, 30], 'primaryHorizon': 1,
    'timingToleranceDays': 3, 'pivotLeft': 2, 'pivotRight': 2, 'gannAnchorLookbackDays': 90,
    'gannAvailabilityOffsets': [-272, -5], 'gannMultipliers': [1, 2],
    'gannDeadline': 'known_at_strictly_before_target_minus_3_midnight',
    'timingGroupsUseSharedKnownMask': True, 'confluence': 'same_target_date',
    'boundaryPolicy': 'full_label_and_confirmation_inside_annual_target_block',
    'controls': {'seed': 160927, 'iterations': 100, 'method': 'circular_shift_eligible_date_indices',
                 'offsetSampling': 'uniform_with_replacement_including_zero', 'exactCoverageMatched': False},
    'invalidation': 'disabled_no_defined_historical_rule', 'revisions': 'none',
    'status': 'current_snapshot_historical_exploration', 'registeredPrediction': False,
    'marketValidated': False, 'parameterSearch': False,
}
TIMING_IDS = [r['id'] for r in RULES if r['kind'] == 'time']
FAMILIES = {r['id']: r['family'] for r in RULES}
LABELS = {'conflict': '方向冲突', 'cross_family': '跨家族一致', 'same_family': '同家族多规则',
          'single': '单规则', 'none': '无触发（结果分布）'}


def validate_protocol(protocol):
    if protocol != FIXED_PROTOCOL:
        raise ValueError('只接受已固定的HX-BTC-001协议；不支持运行时选择参数')
    return deepcopy(protocol)


def load_protocol(base=HERE):
    return validate_protocol(json.loads((Path(base) / 'historical_exploration_protocol.json').read_text(encoding='utf-8-sig')))


def _target_days(protocol):
    return days_between(date.fromisoformat(protocol['startDate']), date.fromisoformat(protocol['endDate']))


def group_rules(rule_ids, direction=False):
    if direction and {'BZ-DAY-METALWATER-UP', 'BZ-DAY-FIRE-DOWN'} <= set(rule_ids):
        return 'conflict'
    if len({FAMILIES[r] for r in rule_ids}) >= 2:
        return 'cross_family'
    if len(set(rule_ids)) >= 2:
        return 'same_family'
    return 'single' if rule_ids else 'none'


def block_reason(day, horizon, kind, end_date, isolation_days=32):
    if (day - date(day.year, 1, 1)).days < isolation_days:
        return 'isolated'
    block_end = min(date(day.year, 12, 31), end_date)
    first = day - 5 * DAY if kind == 'time' else day
    last = day + (5 if kind == 'time' else horizon - 1) * DAY
    if first < date(day.year, 1, 1) or last > block_end:
        return 'boundary_excluded'
    return None


def compact_score(score):
    result = {k: deepcopy(score[k]) for k in ('status', 'reason', 'window', 'directionResult', 'timeResult', 'dataQuality', 'definitionSha256', 'prospectiveEligible')}
    result['evidence'] = {k: score['evidence'][k] for k in ('marketGeneration', 'marketAsOfUtc', 'snapshotSha256')}
    result['evidence']['selectedDates'] = [r['date'] for r in score['evidence']['selectedDays']]
    if result['timeResult']:
        for candidate in result['timeResult']['candidates']:
            candidate['pivotId'] = pivot_id(candidate['date'], candidate['kind'])
        match = result['timeResult']['matchedDate']
        result['timeResult']['matchedPivotId'] = pivot_id(match, result['timeResult']['matchedKind']) if match else None
    return result


def effective_score(raw, reason, known):
    if reason:
        return {'status': reason, 'reason': reason, 'rawStatus': raw.get('status') if raw else None}
    if not known:
        return {'status': 'trigger_unknown', 'reason': 'one_or_more_generator_inputs_unknown', 'rawStatus': raw.get('status') if raw else None}
    return raw


def base_metric(identity, label, total):
    return {'id': identity, 'label': label, 'total': total, 'evaluated': 0, 'missing': 0,
            'pending': 0, 'boundaryExcluded': 0, 'isolated': 0, 'triggerUnknown': 0}


def count_exclusion(metric, score):
    key = {'missing': 'missing', 'pending': 'pending', 'boundary_excluded': 'boundaryExcluded',
           'isolated': 'isolated', 'trigger_unknown': 'triggerUnknown'}.get(score['status'])
    if key:
        metric[key] += 1
        return True
    return False


def direction_metric(identity, label, rows, horizon, expected=None, is_none=False):
    result = base_metric(identity, label, len(rows))
    result.update({'correct': 0, 'incorrect': 0, 'tie': 0, 'hitRatePct': None, 'baselineRatePct': None,
                   'deltaPp': None, 'actualUp': 0, 'actualDown': 0, 'actualFlat': 0,
                   'predictionCounts': {'up': 0, 'down': 0}, 'outcomeDistributionOnly': is_none})
    for row in rows:
        score = row['directionScores'][str(horizon)]
        if count_exclusion(result, score):
            continue
        actual = score['directionResult']['actual']
        result['evaluated'] += 1
        result[{'up': 'actualUp', 'down': 'actualDown', 'flat': 'actualFlat'}[actual]] += 1
        direction = expected or row['directionKind']
        if is_none or direction is None:
            continue
        result['predictionCounts'][direction] += 1
        result['tie' if actual == 'flat' else 'correct' if actual == direction else 'incorrect'] += 1
    if result['evaluated'] and not is_none:
        result['hitRatePct'] = result['correct'] / result['evaluated'] * 100
    return result


def set_direction_baseline(metric, up, down):
    n = sum(metric['predictionCounts'].values())
    if n and up['hitRatePct'] is not None and down['hitRatePct'] is not None:
        metric['baselineRatePct'] = (up['hitRatePct'] * metric['predictionCounts']['up'] + down['hitRatePct'] * metric['predictionCounts']['down']) / n
        metric['deltaPp'] = metric['hitRatePct'] - metric['baselineRatePct']
    metric['baselinePolicy'] = 'all_eligible_dates_weighted_by_evaluated_prediction_direction_mix'
    return metric


def direction_summary(rows, horizon):
    up = direction_metric('baseline_up', '全部可评分日：上涨方向对照', rows, horizon, 'up')
    down = direction_metric('baseline_down', '全部可评分日：下跌方向对照', rows, horizon, 'down')
    groups = [set_direction_baseline(direction_metric(identity, LABELS[identity], [r for r in rows if r['directionGroup'] == identity], horizon,
                                                       is_none=identity in ('none', 'conflict')), up, down)
              for identity in ('conflict', 'cross_family', 'same_family', 'single', 'none')]
    rules = []
    for rule in RULES[:2]:
        selected = [r for r in rows if rule['id'] in r['directionRuleIds'] or not r['directionKnown']]
        rules.append(set_direction_baseline(direction_metric(rule['id'], rule['label'], selected, horizon, rule['kind']), up, down))
    return {'horizonDays': horizon, 'primary': horizon == 1, 'groups': groups, 'rules': rules, 'baselineUp': up, 'baselineDown': down}


def overlap_clusters(target_dates, metric_id):
    """Connected overlapping +/-3 windows, never connected across years."""
    result, active = [], []
    for day in sorted(set(target_dates)):
        if active and (day.year != active[-1].year or day - 3 * DAY > active[-1] + 3 * DAY):
            result.append(active)
            active = []
        active.append(day)
    if active:
        result.append(active)
    return [{'id': 'CL-' + snapshot_hash({'metric': metric_id, 'dates': [d.isoformat() for d in cluster]})[:20],
             'metricId': metric_id, 'year': cluster[0].year,
             'targetDates': [d.isoformat() for d in cluster],
             'startDate': (cluster[0] - 3 * DAY).isoformat(), 'endDate': (cluster[-1] + 3 * DAY).isoformat()}
            for cluster in result]


def covered_dates(targets, eligible):
    eligible = set(eligible)
    return {day + offset * DAY for day in targets for offset in range(-3, 4)
            if day + offset * DAY in eligible and (day + offset * DAY).year == day.year}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    point = (len(values) - 1) * fraction
    lower = int(point)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (point - lower)


def circular_controls(selected_dates, baseline_rows, controls):
    """Shift indices on each annual eligible grid; no fabricated coverage match."""
    if not selected_dates:
        return None, []
    grids = {}
    lookup = {}
    for row in baseline_rows:
        day = date.fromisoformat(row['date'])
        grids.setdefault(day.year, []).append(day)
        lookup[day] = row['timingScore']
    chosen = set(selected_dates)
    rng = random.Random(controls['seed'])
    shifts = {year: [rng.randrange(len(grid)) for _ in range(controls['iterations'])] for year, grid in sorted(grids.items()) if grid}
    indices = {year: [i for i, day in enumerate(grid) if day in chosen] for year, grid in grids.items()}
    denominator = sum(len(grid) for grid in grids.values())
    actual_coverage = len(covered_dates(chosen, lookup)) / denominator * 100 if denominator else None
    samples = []
    for iteration in range(controls['iterations']):
        shifted = set()
        annual_offsets = {}
        for year, grid in sorted(grids.items()):
            offset = shifts[year][iteration]
            annual_offsets[str(year)] = offset
            shifted.update(grid[(i + offset) % len(grid)] for i in indices[year])
        hits = sum(lookup[day]['status'] in ('exact', 'within_tolerance') for day in shifted)
        coverage = len(covered_dates(shifted, lookup)) / denominator * 100 if denominator else None
        samples.append({'iteration': iteration, 'offsetsByYear': annual_offsets, 'count': len(shifted),
                        'hitRatePct': hits / len(shifted) * 100 if shifted else None,
                        'coveragePct': coverage,
                        'actualMinusControlCoveragePp': actual_coverage - coverage if coverage is not None else None})
    rates = [r['hitRatePct'] for r in samples if r['hitRatePct'] is not None]
    coverage_values = [r['coveragePct'] for r in samples if r['coveragePct'] is not None]
    differences = [r['actualMinusControlCoveragePp'] for r in samples if r['actualMinusControlCoveragePp'] is not None]
    return {'iterations': controls['iterations'], 'seed': controls['seed'],
            'medianHitRatePct': statistics.median(rates) if rates else None,
            'p05HitRatePct': percentile(rates, .05), 'p95HitRatePct': percentile(rates, .95),
            'medianCoveragePct': statistics.median(coverage_values) if coverage_values else None,
            'actualCoveragePct': actual_coverage, 'exactCoverageMatched': False,
            'medianActualMinusControlCoveragePp': statistics.median(differences) if differences else None,
            'p05ActualMinusControlCoveragePp': percentile(differences, .05),
            'p95ActualMinusControlCoveragePp': percentile(differences, .95),
            'spacingPreserved': 'circular_eligible_date_indices_only_not_calendar_day_gaps'}, samples


def timing_metric(identity, label, rows, baseline_rows, available, controls=None):
    result = base_metric(identity, label, len(rows))
    result.update({'exact': 0, 'within1': 0, 'within2': 0, 'within3': 0, 'miss': 0,
                   'hitRatePct': None, 'exactRatePct': None, 'baselineRatePct': None, 'deltaPp': None,
                   'errorBins': dict.fromkeys(('exact', 'one', 'two', 'three', 'miss'), 0),
                   'uniqueMatchedPivots': 0, 'uniqueCoveredPivots': 0, 'availablePivots': len(available), 'pivotRecallPct': None,
                   'coveredDates': 0, 'coveragePct': None, 'clusterCount': 0, 'control': None})
    result['outcomeDistributionOnly'] = identity in ('none', 'timing_baseline')
    selected, matched, covered_pivots = [], set(), set()
    for row in rows:
        score = row['timingScore']
        if count_exclusion(result, score):
            continue
        result['evaluated'] += 1
        selected.append(date.fromisoformat(row['date']))
        covered_pivots.update(p['pivotId'] for p in score['timeResult']['candidates'])
        error = score['timeResult']['absoluteErrorDays']
        if error is None:
            result['miss'] += 1
            result['errorBins']['miss'] += 1
        else:
            result['errorBins'][('exact', 'one', 'two', 'three')[error]] += 1
            result['exact'] += int(error == 0)
            for tolerance in (1, 2, 3):
                result['within' + str(tolerance)] += int(error <= tolerance)
            matched.add(score['timeResult']['matchedPivotId'])
    if not matched <= covered_pivots <= available:
        raise ValueError('匹配转点不在共同基准转点集合内')
    if result['evaluated']:
        result['hitRatePct'] = result['within3'] / result['evaluated'] * 100
        result['exactRatePct'] = result['exact'] / result['evaluated'] * 100
    result['uniqueMatchedPivots'] = len(matched)
    result['uniqueCoveredPivots'] = len(covered_pivots)
    result['pivotRecallPct'] = len(covered_pivots) / len(available) * 100 if available else None
    result['pivotRecallMeaning'] = 'unique_pivots_in_any_selected_tolerance_window_not_only_nearest_matches'
    eligible = [date.fromisoformat(row['date']) for row in baseline_rows]
    result['coveredDates'] = len(covered_dates(selected, eligible))
    result['coveragePct'] = result['coveredDates'] / len(eligible) * 100 if eligible else None
    clusters = overlap_clusters(selected, identity)
    result['clusterCount'] = len(clusters)
    samples = []
    if controls is not None:
        result['control'], samples = circular_controls(selected, baseline_rows, controls)
    return result, clusters, samples


def timing_summary(rows, controls):
    eligible = [r for r in rows if r['timingScore']['status'] in ('exact', 'within_tolerance', 'miss')]
    available = {p['pivotId'] for r in eligible for p in r['timingScore']['timeResult']['candidates']}
    baseline, clusters, _ = timing_metric('timing_baseline', '全部共同可评分日期', rows, eligible, available)
    details = {'clusters': clusters, 'controls': {}, 'availablePivotIds': sorted(available)}
    groups, rules = [], []
    selections = [(g, LABELS[g], [r for r in rows if r['timingGroup'] == g], groups)
                  for g in ('cross_family', 'same_family', 'single', 'none')]
    for rule in RULES[2:]:
        selected = [r for r in rows if rule['id'] in r['timingRuleIds'] or not r['ruleKnown'][rule['id']]]
        selections.append((rule['id'], rule['label'], selected, rules))
    for identity, label, selected, destination in selections:
        metric, related, samples = timing_metric(identity, label, selected, eligible, available, controls)
        metric['baselineRatePct'] = baseline['hitRatePct']
        metric['deltaPp'] = metric['hitRatePct'] - baseline['hitRatePct'] if metric['hitRatePct'] is not None and baseline['hitRatePct'] is not None else None
        destination.append(metric)
        details['clusters'].extend(related)
        details['controls'][identity] = samples
    return {'groups': groups, 'rules': rules, 'baseline': baseline}, details


def build_report(snapshot, protocol, generated_at, progress=None):
    protocol = validate_protocol(protocol)
    if not isinstance(generated_at, datetime) or generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError('generated_at必须是真实快照捕获时刻且有时区')
    generated = generated_at.astimezone(timezone.utc)
    if not isinstance(snapshot, (tuple, list)) or len(snapshot) != 3 or not all(isinstance(part, dict) for part in snapshot):
        raise ValueError('snapshot必须是(state,candles,gaps)')
    targets = _target_days(protocol)
    first, last = date.fromisoformat(protocol['startDate']), date.fromisoformat(protocol['endDate'])
    source_first = date.fromisoformat(protocol['sourceStartDate'])
    index = inspect_days(snapshot, source_first, max(last, generated.astimezone(LOCAL).date()), generated)
    if progress:
        progress({'stage': 'source', 'completed': len(index), 'total': len(index), 'message': 'Validate actual captured rows without changing timestamps'})
    calendars, calendar_source_events = calendar_events(targets, generated, progress)
    astronomical, astro_source_events = astronomical_events(first, last, generated, progress)
    anchors, gann_source_events = sequential_gann(index, first, last, generated,
                                                 protocol['gannAnchorLookbackDays'], protocol['gannMultipliers'])
    events = calendar_source_events + astro_source_events + gann_source_events
    dedup = deduplicate_events(events)
    by_date = {}
    for event in dedup:
        by_date.setdefault(event['targetDate'], []).append(event)
    daily = []
    for i, day in enumerate(targets):
        key = day.isoformat()
        gann_known, bad_days = gann_availability(index, day, protocol['gannAvailabilityOffsets'])
        astro_known = astronomical[key]['known']
        calendar_known = calendars[key]['known']
        known = gann_known and astro_known
        date_events = by_date.get(key, [])
        direction_ids = calendars[key]['ruleIds']
        timing_ids = [e['ruleId'] for e in date_events if e['kind'] == 'time']
        row = {'date': key, 'year': day.year, 'directionKnown': calendar_known, 'timingKnown': known,
               'directionRuleIds': direction_ids, 'timingRuleIds': timing_ids,
               'directionGroup': group_rules(direction_ids, True) if calendar_known else None,
               'timingGroup': group_rules(timing_ids) if known else None,
               'directionKind': 'up' if direction_ids == ['BZ-DAY-METALWATER-UP'] else 'down' if direction_ids == ['BZ-DAY-FIRE-DOWN'] else None,
               'ruleKnown': {'GANN-EQUAL-INTERVAL': gann_known, 'ASTRO-MOON-PHASE': astro_known,
                             'ASTRO-NAKSHATRA-INGRESS': astro_known},
               'unknownReasons': {'gannDays': bad_days, 'astronomy': astronomical[key]['errors'], 'calendar': calendars[key]['errors']},
               'eventRefs': date_events, 'directionScores': {}, 'timingScore': None,
               'scoreReferenceKind': ('up' if direction_ids == ['BZ-DAY-METALWATER-UP'] else 'down' if direction_ids == ['BZ-DAY-FIRE-DOWN'] else 'up'),
               'scoreReferenceMeaning': 'triggered_hypothesis' if direction_ids else 'up_reference_for_actual_distribution_only',
               'reconstructionCreatedAtUtc': generated.isoformat(), 'registeredPrediction': False}
        for kind, horizon in [('up', h) for h in protocol['horizons']] + [('time', 1)]:
            reason = block_reason(day, horizon, kind, last, protocol['isolationDays'])
            raw = None
            if reason is None:
                # Real current capture time is deliberately used for both fields.
                # It does not claim the historical row was downloaded back then.
                record = {'symbol': 'BTCUSDT', 'kind': ('time' if kind == 'time' else row['scoreReferenceKind']), 'targetDate': key, 'horizonDays': horizon,
                          'createdAtUtc': generated.isoformat(), 'prospectiveEligible': False, 'invalidation': None}
                raw = compact_score(score_prediction(record, snapshot, generated))
            effective = effective_score(raw, reason, known if kind == 'time' else calendar_known)
            if kind == 'time':
                row['timingScore'] = effective
                if effective is not raw and raw:
                    row['excludedRawTimingScore'] = raw
            else:
                row['directionScores'][str(horizon)] = effective
        daily.append(row)
        if progress and (i % 100 == 0 or i + 1 == len(targets)):
            progress({'stage': 'scoring', 'completed': i + 1, 'total': len(targets), 'message': key})
    direction = [direction_summary(daily, h) for h in protocol['horizons']]
    timing, timing_details = timing_summary(daily, protocol['controls'])
    years = []
    for year in sorted({r['year'] for r in daily}):
        subset = [r for r in daily if r['year'] == year]
        annual_timing, extra = timing_summary(subset, protocol['controls'])
        annual_direction = direction_summary(subset, 1)
        years.append({'year': year, 'directionPrimary': annual_direction['groups'], 'timing': annual_timing['groups'],
                      'timingBaseline': annual_timing['baseline'], 'directionBaselineUp': annual_direction['baselineUp'],
                      'directionBaselineDown': annual_direction['baselineDown']})
        timing_details['clusters'].extend(extra['clusters'])
        timing_details['controls'][str(year)] = extra['controls']
        if progress:
            progress({'stage': 'controls', 'completed': len(years), 'total': len({r['year'] for r in daily}), 'message': str(year)})
    complete = sum(value['valid'] for value in index.values())
    pending = sum(not value['valid'] and midnight(day + DAY) > generated for day, value in index.items())
    all_pivots = {p['pivotId']: p for r in daily if r['timingScore']['status'] in ('exact', 'within_tolerance', 'miss')
                  for p in r['timingScore']['timeResult']['candidates']}
    unique_clusters = {c['id']: c for c in timing_details['clusters']}
    return {
        'schemaVersion': 1, 'version': VERSION, 'studyId': protocol['studyId'], 'generatedAtUtc': generated.isoformat(),
        'symbol': 'BTCUSDT', 'status': 'exploratory', 'marketValidated': False, 'registeredPrediction': False,
        'range': {'startDate': protocol['startDate'], 'endDate': protocol['endDate']},
        'source': {'provider': protocol['provider'], 'timezone': 'UTC+8', 'interval': '1d',
                   'lastSuccessAtUtc': snapshot[0].get('last_success'), 'snapshotSha256': snapshot_hash(snapshot),
                   'snapshotHashMeaning': 'canonical_complete_state_candles_gaps_not_export_file_bytes',
                   'completeDays': complete, 'missingDays': len(index) - complete - pending, 'pendingDays': pending},
        'protocolSha256': snapshot_hash(protocol), 'rules': deepcopy(RULES), 'direction': direction, 'timing': timing, 'years': years,
        'quality': {'targetDays': len(daily), 'sourceStartDate': source_first.isoformat(),
                    'directionTriggerUnknownDays': sum(not r['directionKnown'] for r in daily),
                    'timingTriggerUnknownDays': sum(not r['timingKnown'] for r in daily),
                    'gannAvailabilityOffsets': protocol['gannAvailabilityOffsets'],
                    'gannRawProjections': len(gann_source_events), 'gannEligibleProjections': sum(e['eligible'] for e in gann_source_events),
                    'deduplicatedEvents': len(dedup), 'sharedTimingAvailabilityMask': True,
                    'unknownGroupAssignment': 'unassigned_and_counted_in_quality_not_none'},
        'limitations': [
            '当前快照上的历史重建；已看过的历史是探索材料，不是过去事前登记或盲样本。',
            '只有理论确认时刻的先后约束，没有证明数据在历史当时已被本系统捕获；保留真实updated时间。',
            '各年最初32个目标日隔离；指标邻居可落隔离尾，标签和确认不跨当年目标块。无训练或逐年调参。',
            '±3日窗口很宽，接近每日的换宿可能机械覆盖转点；必须同时查看全日基准与覆盖率。',
            '圆移对照只保持合格日网格序号间距和数量，不保持自然日间距，也未精确匹配覆盖率。',
            '共享已知掩码需要Gann目标日前272至前5日完整有效，未知不当作无触发；这是保守可用性约束。',
            '候选窗口重叠、同一真实转点可被多次匹配；去重与簇仅描述，未作独立样本显著性推断。',
            '没有已定义的历史价格失效规则，不事后剔除失败；没有修订、交易、费用或盈利检验。',
        ],
        'files': {'protocol': '/api/exploration-protocol', 'details': '/api/exploration-details', 'snapshot': '/api/exploration-snapshot'},
        'details': {'daily': daily, 'sourceEvents': events, 'deduplicatedEvents': dedup, 'gannAnchors': anchors,
                    'pivots': [all_pivots[key] for key in sorted(all_pivots)],
                    'clusters': [unique_clusters[key] for key in sorted(unique_clusters)], 'controlSamples': timing_details['controls'],
                    'sourceExclusions': [{'date': day.isoformat(), 'reasons': item['reasons']} for day, item in index.items() if not item['valid']]},
    }
