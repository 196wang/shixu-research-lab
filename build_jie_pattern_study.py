"""Reproducible exploratory BTC/USD Jie pattern audit on a sealed daily snapshot.

The definitions below are fixed before reading the resulting statistics. This
script never changes forecasts, scores, market caches, or the sealed snapshot.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import json
import statistics

from build_jie_event_study import read_snapshot, measure, pct
from coinbase_study import inspect
from exploration_generators import pivot_kind
from month_study import episode_plan


FIRST, LAST = date(2022, 1, 1), date(2026, 10, 16)
DAY = timedelta(days=1)
OUTPUT = Path(__file__).resolve().parent / 'research' / 'jie-pattern-study-2026-10-23.json'
OFFSETS = (-2, -1, 0, 1, 2)


def daily(index, center, offset):
    row = index[center + offset * DAY]['prices']
    return pct(row['open'], row['close'])


def describe(rows):
    if not rows:
        return {'n': 0}
    return {
        'n': len(rows),
        'reversalCount': sum(row['reversal'] for row in rows),
        'reversalPct': 100 * statistics.mean(row['reversal'] for row in rows),
        'dayUpCount': sum(row['dayPct'] > 0 for row in rows),
        'dayUpPct': 100 * statistics.mean(row['dayPct'] > 0 for row in rows),
        'dayMeanPct': statistics.mean(row['dayPct'] for row in rows),
        'dayMeanAbsPct': statistics.mean(abs(row['dayPct']) for row in rows),
        'dayMeanRangePct': statistics.mean(row['dayRangePct'] for row in rows),
        'pivotCount': sum(bool(row['pivot']) for row in rows),
        'pivotPct': 100 * statistics.mean(bool(row['pivot']) for row in rows),
        'highPivotCount': sum(row['pivot'] == 'high' for row in rows),
        'lowPivotCount': sum(row['pivot'] == 'low' for row in rows),
        'ambiguousPivotCount': sum(row['pivot'] == 'ambiguous' for row in rows),
        'before2UpCount': sum(row['before2Pct'] > 0 for row in rows),
        'after2UpCount': sum(row['after2Pct'] > 0 for row in rows),
        'after2MeanPct': statistics.mean(row['after2Pct'] for row in rows),
        'offsets': {str(offset): {
            'upCount': sum(row['daily'][str(offset)] > 0 for row in rows),
            'upPct': 100 * statistics.mean(row['daily'][str(offset)] > 0 for row in rows),
            'meanPct': statistics.mean(row['daily'][str(offset)] for row in rows),
            'meanAbsPct': statistics.mean(abs(row['daily'][str(offset)]) for row in rows),
        } for offset in OFFSETS},
    }


def majority_transfer(events):
    """Apply each named term's 2022-25 simple majority to its 2026 occurrence.

    This is only a retrospective transfer check: the rule was selected after
    looking at the data, so it is not an independently registered backtest.
    """
    result = {}
    fields = ('reversal', 'dayPct', 'after2Pct', 'pivot')
    for field in fields:
        checks, ties = [], []
        for current in events:
            if current['year'] != 2026:
                continue
            previous = [row for row in events if row['term'] == current['term'] and row['year'] < 2026]
            previous_true = sum(bool(row[field]) if field in ('reversal', 'pivot') else row[field] > 0
                                for row in previous)
            if previous_true * 2 == len(previous):
                ties.append(current['term'])
                continue
            expected = previous_true * 2 > len(previous)
            observed = bool(current[field]) if field in ('reversal', 'pivot') else current[field] > 0
            checks.append({'term': current['term'], 'date': current['date'],
                           'priorTrue': previous_true, 'priorN': len(previous),
                           'expected': expected, 'observed': observed, 'matched': expected == observed})
        result[field] = {'evaluated': len(checks), 'matches': sum(row['matched'] for row in checks),
                         'tiesAbstained': ties, 'checks': checks}
    return result


def build():
    snapshot, captured, source_hash = read_snapshot()
    index = inspect(snapshot, FIRST, LAST, captured)
    _, boundaries = episode_plan(FIRST, LAST)
    event_days = {at.date() for _, at in boundaries if FIRST <= at.date() <= LAST}
    events, controls, excluded = [], [], []
    for term, instant in boundaries:
        center = instant.date()
        if not FIRST + 2 * DAY <= center <= LAST - 2 * DAY:
            continue
        measured = measure(center, index)
        if measured is None:
            excluded.append({'term': term, 'date': center.isoformat(), 'reason': 'incomplete five-day window'})
            continue
        event = dict(measured, term=term, date=center.isoformat(), year=center.year,
                     atLocal=instant.isoformat(), pivot=pivot_kind(index, center),
                     daily={str(offset): daily(index, center, offset) for offset in OFFSETS})
        events.append(event)
        month_start = center.replace(day=1)
        next_month = (month_start.replace(day=28) + 4 * DAY).replace(day=1)
        candidate = month_start
        while candidate < next_month and candidate <= LAST - 2 * DAY:
            if candidate >= FIRST + 2 * DAY and all(abs((candidate - day).days) > 2 for day in event_days):
                control = measure(candidate, index)
                if control:
                    controls.append(dict(control, matchedEvent=center.isoformat(), date=candidate.isoformat(),
                                         year=center.year, pivot=pivot_kind(index, candidate),
                                         daily={str(offset): daily(index, candidate, offset) for offset in OFFSETS}))
            candidate += DAY

    years = {}
    for year in range(FIRST.year, LAST.year + 1):
        selected = [row for row in events if row['year'] == year]
        matched = [row for row in controls if row['year'] == year]
        years[str(year)] = {'jie': describe(selected), 'sameMonthNonJie': describe(matched)}
    prior = [row for row in events if row['year'] <= 2025]
    prior_controls = [row for row in controls if row['year'] <= 2025]
    by_term = {}
    for term in sorted({row['term'] for row in events}):
        rows = [row for row in events if row['term'] == term]
        by_term[term] = {'jie': describe(rows), 'dates': [row['date'] for row in rows],
                         'dayPctByYear': {str(row['year']): row['dayPct'] for row in rows},
                         'after2PctByYear': {str(row['year']): row['after2Pct'] for row in rows}}
    return {
        'studyType': 'exploratory_retrospective_not_prediction',
        'snapshotSha256': source_hash,
        'data': 'Coinbase Exchange BTC/USD; complete Beijing 00:00-24:00 daily candles',
        'dateCoverage': [FIRST.isoformat(), LAST.isoformat()],
        'event': '12 exact Jie transitions per year, using their Beijing calendar date',
        'control': 'same Gregorian month, valid five-day window, center farther than two days from any Jie',
        'metrics': 'fixed five-day offsets; complete two-day before/after sign reversal; strict two-neighbor high/low pivot',
        'limits': ['All hypotheses were inspected after observing at least some prices.',
                   'Calendar-day event candle mixes old and new months.',
                   'Controls overlap in time and are descriptive, not independent or causal.',
                   'All 12 named terms are multiple exploratory comparisons with 4-5 observations each.',
                   'Only 10 valid events in incomplete 2026; no out-of-sample accuracy is inferred.'],
        'all': {'jie': describe(events), 'sameMonthNonJie': describe(controls)},
        'pre2026': {'jie': describe(prior), 'sameMonthNonJie': describe(prior_controls)},
        'years': years, 'terms': by_term, 'majorityTransfer2026': majority_transfer(events),
        'excluded': excluded,
        'events': events,
    }


if __name__ == '__main__':
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(OUTPUT)
