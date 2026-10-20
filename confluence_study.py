"""Frozen descriptive cross-family overlaps from verified v0.16 detail rows."""
from collections import defaultdict
from datetime import date
import hashlib
import json
from pathlib import Path

from historical_exploration_store import read_artifact
from historical_exploration import covered_dates

HERE = Path(__file__).resolve().parent
SETS = [
    ('bazi_only', '仅八字方向', lambda ids: not ids),
    ('bazi_astro_only', '八字 + 占星，无江恩', lambda ids: any(x.startswith('ASTRO-') for x in ids) and 'GANN-EQUAL-INTERVAL' not in ids),
    ('bazi_gann_only', '八字 + 江恩，无占星', lambda ids: 'GANN-EQUAL-INTERVAL' in ids and not any(x.startswith('ASTRO-') for x in ids)),
    ('three_families', '八字 + 江恩 + 占星', lambda ids: 'GANN-EQUAL-INTERVAL' in ids and any(x.startswith('ASTRO-') for x in ids)),
    ('gann', '八字 + 江恩', lambda ids: 'GANN-EQUAL-INTERVAL' in ids),
    ('moon_phase', '八字 + 月相', lambda ids: 'ASTRO-MOON-PHASE' in ids),
    ('nakshatra', '八字 + 月亮换宿', lambda ids: 'ASTRO-NAKSHATRA-INGRESS' in ids),
    ('gann_moon_phase', '八字 + 江恩 + 月相', lambda ids: 'GANN-EQUAL-INTERVAL' in ids and 'ASTRO-MOON-PHASE' in ids),
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def accepted(row):
    return bool(row['directionRuleIds']) and row['directionKnown'] and row['timingKnown'] and row['directionScores']['1']['status'] in ('correct', 'incorrect', 'tie') and row['timingScore']['status'] in ('exact', 'within_tolerance', 'miss')


def direction_contrast(selected, pool):
    strata = defaultdict(list)
    for row in pool:
        strata[(row['year'], row['directionKind'])].append(row)
    chosen = {row['date'] for row in selected}
    matched_n = correct = complement_n = 0
    weighted = 0.0
    per_year = defaultdict(lambda: {'n': 0, 'correct': 0})
    for (year, _), members in strata.items():
        positive = [x for x in members if x['date'] in chosen]
        negative = [x for x in members if x['date'] not in chosen]
        if not positive or not negative:
            continue
        n = len(positive)
        matched_n += n
        complement_n += len(negative)
        count = sum(x['directionScores']['1']['status'] == 'correct' for x in positive)
        correct += count
        weighted += n * sum(x['directionScores']['1']['status'] == 'correct' for x in negative) / len(negative)
        per_year[year]['n'] += n
        per_year[year]['correct'] += count
    baseline = weighted / matched_n * 100 if matched_n else None
    actual = correct / matched_n * 100 if matched_n else None
    return {'matchedN': matched_n, 'complementN': complement_n, 'matchedRatePct': actual,
            'baselineRatePct': baseline, 'deltaPp': actual - baseline if actual is not None else None,
            'comparisonReady': matched_n >= 30 and complement_n >= max(30, matched_n * 0.25),
            'annual': [{'year': y, 'n': m['n'], 'correct': m['correct'],
                        'ratePct': m['correct'] / m['n'] * 100 if m['n'] else None}
                       for y, m in sorted(per_year.items())]}


def build():
    details = read_artifact('details', HERE)
    report = read_artifact('report', HERE)
    pool = [r for r in details['daily'] if accepted(r)]
    timing_pool = [r for r in details['daily'] if r['timingKnown'] and r['timingScore']['status'] in ('exact', 'within_tolerance', 'miss')]
    timing_dates = {date.fromisoformat(r['date']) for r in timing_pool}
    pivots = {date.fromisoformat(p['date']) for p in details['pivots'] if p['kind'] in ('high', 'low')}
    items = []
    for identity, label, predicate in SETS:
        selected = [r for r in pool if predicate(r['timingRuleIds'])]
        selected_dates = {date.fromisoformat(r['date']) for r in selected}
        covered = covered_dates(selected_dates, timing_dates)
        nearby = {p for p in pivots if p in covered}
        count = len(selected)
        correct = sum(r['directionScores']['1']['status'] == 'correct' for r in selected)
        exact = sum(r['timingScore']['status'] == 'exact' for r in selected)
        near = sum(r['timingScore']['status'] in ('exact', 'within_tolerance') for r in selected)
        contrast = direction_contrast(selected, pool)
        items.append({'id': identity, 'label': label, 'triggerDays': count,
                      'directionCorrect': correct, 'directionRatePct': correct / count * 100 if count else None,
                      'directionContrast': contrast,
                      'timingExact': exact, 'timingWithin3': near,
                      'timingWithin3Pct': near / count * 100 if count else None,
                      'coveredDates': len(covered), 'coveragePct': len(covered) / len(timing_dates) * 100 if timing_dates else None,
                      'densityWarning': len(covered) / len(timing_dates) >= .95 if timing_dates else False,
                      'uniquePivotsCovered': len(nearby), 'availablePivots': len(pivots),
                      'pivotRecallPct': len(nearby) / len(pivots) * 100 if pivots else None})
    return {'schemaVersion': 1, 'studyId': 'CONFLUENCE-BTC-001', 'status': 'historical_exploration',
            'marketValidated': False, 'sourceStudyId': report['studyId'],
            'sourceDetailsSha256': digest(HERE / 'exploration_v16/run-001/details.json'),
            'sourceReportSha256': digest(HERE / 'exploration_v16/run-001/report.json'),
            'sourceRange': report['range'], 'protocol': {
                'direction': 'same_date_bazi_daily_open_to_close', 'timing': 'same_target_date_gann_or_astronomy',
                'directionEligibility': 'both_generators_known_and_one_day_direction_and_timing_window_scored',
                'directionControl': 'same_civil_year_same_direction_other_bazi_trigger_dates',
                'minimumContrastSample': 30, 'minimumComplementToSelectedRatio': .25,
                'timingToleranceDays': 3,
                'timingCoverage': 'union_of_plus_minus_3_day_windows_on_common_timing_eligible_grid',
                'densityPolicy': 'report_coverage_and_pivot_recall_no_claim_of_exact_coverage_matching',
                'multipleComparisons': len(SETS), 'registeredPrediction': False,
            }, 'eligibleBaziDays': len(pool), 'timingEligibleDays': len(timing_dates),
            'pivotCount': len(pivots), 'sets': items,
            'limitations': ['已查看过同一历史区间，按年拆开仍不是独立盲测。',
                            '八字、江恩、月相与换宿的日期有重叠；各组并非独立样本。',
                            '时间规则不判断涨跌或高低；±3天匹配受信号覆盖率影响。',
                            '对照按年份和八字方向匹配，尚未做到时间覆盖率完全匹配或多重检验校正。']}


def save():
    result = build()
    folder = HERE / 'confluence_v20'
    folder.mkdir(exist_ok=True)
    target = folder / 'report.json'
    if target.exists():
        raise RuntimeError('固定共振报告已存在，不覆盖')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (folder / 'manifest.json').write_text(json.dumps({'reportSha256': digest(target),
       'sourceDetailsSha256': result['sourceDetailsSha256'],
       'sourceReportSha256': result['sourceReportSha256']}, indent=2) + '\n', encoding='utf-8')
    return result


def read():
    folder = HERE / 'confluence_v20'
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    target = folder / 'report.json'
    read_artifact('details', HERE)
    read_artifact('report', HERE)
    if digest(target) != manifest['reportSha256'] or digest(HERE / 'exploration_v16/run-001/details.json') != manifest['sourceDetailsSha256'] or digest(HERE / 'exploration_v16/run-001/report.json') != manifest['sourceReportSha256']:
        raise RuntimeError('共振报告与固定来源校验不一致')
    return json.loads(target.read_text(encoding='utf-8'))


if __name__ == '__main__':
    print(json.dumps(save(), ensure_ascii=False, indent=2))
