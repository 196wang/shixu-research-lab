"""Exploratory BTC/USD element and branch audit on the sealed Coinbase snapshot.

Reads only existing data. A complete Beijing day containing a Jie transition is
excluded from day/month conditional attribution. No rule is reweighted here.
"""
from collections import defaultdict
from datetime import date
from pathlib import Path
import json
import statistics

from build_jie_event_study import read_snapshot
from coinbase_study import inspect
from exploration_generators import days_between
from month_study import daily_rows, episode_plan, evaluate_episodes


FIRST, LAST = date(2022, 1, 1), date(2026, 10, 16)
OUTPUT = Path(__file__).resolve().parent / 'research' / 'element-pattern-study-2026-10-24.json'

DAY_RULES = {
    'stem_metal_water_up': ('日干庚辛壬癸：当日上涨', lambda row: row['pillars']['day'][0] in '庚辛壬癸', 'up'),
    'stem_fire_down': ('日干丙丁：当日下跌', lambda row: row['pillars']['day'][0] in '丙丁', 'down'),
    'branch_yin_down': ('寅日：当日下跌', lambda row: row['pillars']['day'][1] == '寅', 'down'),
    'gengxu_down': ('庚戌日：当日下跌', lambda row: row['pillars']['day'] == '庚戌', 'down'),
}


def mean(values):
    return statistics.mean(values) if values else None


def day_observation(row):
    opened, high, low = (float(row[key]) for key in ('open', 'high', 'low'))
    return dict(row, rangePct=100 * (high - low) / opened,
                upExcursionPct=100 * (high - opened) / opened,
                downExcursionPct=100 * (opened - low) / opened)


def daily_stats(rows, direction):
    if not rows:
        return {'n': 0}
    return {'n': len(rows), 'correct': sum(row['actual'] == direction for row in rows),
            'up': sum(row['actual'] == 'up' for row in rows),
            'down': sum(row['actual'] == 'down' for row in rows),
            'flat': sum(row['actual'] == 'flat' for row in rows),
            'meanReturnPct': mean([row['returnPct'] for row in rows]),
            'medianReturnPct': statistics.median(row['returnPct'] for row in rows),
            'meanAbsReturnPct': mean([abs(row['returnPct']) for row in rows]),
            'meanRangePct': mean([row['rangePct'] for row in rows]),
            'meanUpExcursionPct': mean([row['upExcursionPct'] for row in rows]),
            'meanDownExcursionPct': mean([row['downExcursionPct'] for row in rows])}


def matched_daily(rows, predicate, direction):
    strata = defaultdict(list)
    for row in rows:
        strata[(row['year'], row['episodeId'])].append(row)
    observed, control_weighted = [], defaultdict(float)
    comparable_n = 0
    empty_complement = 0
    for members in strata.values():
        trigger = [row for row in members if predicate(row)]
        complement = [row for row in members if not predicate(row)]
        if not trigger:
            continue
        if not complement:
            empty_complement += len(trigger)
            continue
        observed.extend(trigger)
        comparable_n += len(complement)
        baseline = daily_stats(complement, direction)
        count = len(trigger)
        for key in ('correct', 'up', 'down', 'flat'):
            control_weighted[key + 'RatePct'] += 100 * baseline[key] / len(complement) * count
        for key in ('meanReturnPct', 'meanAbsReturnPct', 'meanRangePct',
                    'meanUpExcursionPct', 'meanDownExcursionPct'):
            control_weighted[key] += baseline[key] * count
    n = len(observed)
    return {'trigger': daily_stats(observed, direction),
            'matchedControl': {key: value / n for key, value in control_weighted.items()} if n else {},
            'matchedControlDistinctDays': comparable_n,
            'unmatchedTriggerDays': empty_complement,
            'interpretation': 'matched on same calendar year and exact solar-month episode; weighted by trigger count'}


def monthly_stats(episodes):
    if not episodes:
        return {'n': 0}
    return {'n': len(episodes), 'up': sum(row['returnPct'] > 0 for row in episodes),
            'down': sum(row['returnPct'] < 0 for row in episodes),
            'meanReturnPct': mean([row['returnPct'] for row in episodes]),
            'medianReturnPct': statistics.median(row['returnPct'] for row in episodes),
            'meanDailyUpFractionPct': 100 * mean([row['dailyUpCount'] / row['dailyN'] for row in episodes]),
            'meanDailyDownFractionPct': 100 * mean([row['dailyDownCount'] / row['dailyN'] for row in episodes]),
            'meanDailyRangePct': mean([row['meanDailyRangePct'] for row in episodes])}


def month_groups(episodes):
    predicates = {
        'branch_metal_water': lambda row: row['branchElement'] in '金水',
        'branch_fire': lambda row: row['branchElement'] == '火',
        'stem_metal_water': lambda row: row['stemElement'] in '金水',
        'both_metal_water': lambda row: row['stemElement'] in '金水' and row['branchElement'] in '金水',
        'branch_yin': lambda row: row['pillar'][1] == '寅',
        'branch_xu': lambda row: row['pillar'][1] == '戌',
    }
    return {name: {'selected': monthly_stats([row for row in episodes if predicate(row)]),
                   'other': monthly_stats([row for row in episodes if not predicate(row)])}
            for name, predicate in predicates.items()}


def build():
    snapshot, generated, source_hash = read_snapshot()
    index = inspect(snapshot, FIRST, LAST, generated)
    plans, boundaries = episode_plan(FIRST, LAST)
    all_rows = daily_rows(snapshot, days_between(FIRST, LAST), index, plans, boundaries)
    eligible = [day_observation(row) for row in all_rows if row['status'] == 'eligible']
    daily = {}
    periods = {'2022-2025': [row for row in eligible if row['year'] <= 2025],
               '2026-to-10-16': [row for row in eligible if row['year'] == 2026]}
    for year in range(FIRST.year, LAST.year + 1):
        periods[str(year)] = [row for row in eligible if row['year'] == year]
    for name, members in periods.items():
        daily[name] = {identity: {'label': label, 'direction': direction,
                                  **matched_daily(members, predicate, direction)}
                       for identity, (label, predicate, direction) in DAY_RULES.items()}

    by_episode = defaultdict(list)
    for row in eligible:
        by_episode[row['episodeId']].append(row)
    complete = []
    for row in evaluate_episodes(plans, index, generated):
        if row['status'] != 'complete':
            continue
        members = by_episode[row['id']]
        if not members:
            continue
        complete.append({'id': row['id'], 'year': row['year'], 'term': row['term'],
                         'pillar': row['pillar'], 'stemElement': row['stemElement'],
                         'branchElement': row['branchElement'], 'startAt': row['startAt'],
                         'endAt': row['endAt'], 'returnPct': row['returnPct'],
                         'dailyN': len(members),
                         'dailyUpCount': sum(day['actual'] == 'up' for day in members),
                         'dailyDownCount': sum(day['actual'] == 'down' for day in members),
                         'meanDailyRangePct': mean([day['rangePct'] for day in members]),
                         'upDates': [day['date'] for day in members if day['actual'] == 'up'],
                         'downDates': [day['date'] for day in members if day['actual'] == 'down']})
    month_periods = {'2022-2025': [row for row in complete if row['year'] <= 2025],
                     '2026-completed': [row for row in complete if row['year'] == 2026]}
    for year in range(FIRST.year, LAST.year + 1):
        month_periods[str(year)] = [row for row in complete if row['year'] == year]
    return {'studyType': 'exploratory_retrospective_not_prediction',
            'snapshotSha256': source_hash, 'symbol': 'Coinbase Exchange BTC/USD spot',
            'dataThrough': LAST.isoformat(), 'timezone': 'Asia/Shanghai',
            'dailyBoundary': '00:00-24:00 local; day pillar sampled at 00:00',
            'monthBoundary': 'exact Jie; transition day excluded from day/month conditional attribution',
            'dailyEligibility': {'targetDays': len(all_rows), 'eligibleDays': len(eligible),
                                 'boundaryExcluded': sum(row['status'] == 'boundary_excluded' for row in all_rows),
                                 'missing': sum(row['status'] == 'missing' for row in all_rows)},
            'monthEligibility': {'completeEpisodes': len(complete),
                                 'definition': 'every complete interior Beijing day available; partial first/last months excluded'},
            'dailyRules': daily, 'monthlyGroups': {name: month_groups(members) for name, members in month_periods.items()},
            'monthlyEpisodes': complete,
            'limits': ['Rules have been discussed against known prices; this is not blind validation.',
                       'Daily OHLC shows excursion size but not whether the high or low came first.',
                       'Named days such as Gengxu have very few observations each year.',
                       'Monthly sample size is complete episodes, not the count of days inside them.',
                       'Overlapping element groups and multiple comparisons prevent a simple success claim.']}


if __name__ == '__main__':
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(OUTPUT)
