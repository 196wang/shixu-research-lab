"""Exploratory, read-only BTC/USD event study around exact Jie transitions.

This reads the sealed Coinbase daily snapshot. It does not edit the frozen
historical study, forward registration, market cache, or scoring records.
"""
from datetime import date, datetime, timedelta
from pathlib import Path
import hashlib
import json
import statistics

from coinbase_study import inspect
from month_study import episode_plan


ROOT = Path(__file__).resolve().parent
RUN = ROOT / 'coinbase_study_v21' / 'run-001'
FIRST, LAST = date(2023, 1, 1), date(2026, 10, 16)
DAY = timedelta(days=1)


def read_snapshot():
    manifest = json.loads((RUN / 'manifest.json').read_text(encoding='utf-8'))
    body = (RUN / 'snapshot.json').read_bytes()
    if hashlib.sha256(body).hexdigest() != manifest['fileSha256']['snapshot.json']:
        raise ValueError('固定 Coinbase 快照哈希不符')
    state, candles, gaps = json.loads(body)['BTCUSD']
    snapshot = state, {int(key): value for key, value in candles.items()}, {
        int(key): value for key, value in gaps.items()}
    return snapshot, datetime.fromisoformat(manifest['capturedAtUtc']), manifest['fileSha256']['snapshot.json']


def pct(opened, closed):
    return float((closed / opened - 1) * 100)


def measure(center, index):
    window = [index.get(center + offset * DAY) for offset in range(-2, 3)]
    if any(not item or not item['valid'] for item in window):
        return None
    prices = [item['prices'] for item in window]
    before = pct(prices[0]['open'], prices[1]['close'])
    after = pct(prices[3]['open'], prices[4]['close'])
    transition = pct(prices[2]['open'], prices[2]['close'])
    return {'before2Pct': before, 'dayPct': transition, 'after2Pct': after,
            'dayRangePct': pct(prices[2]['open'], prices[2]['high']) -
                           pct(prices[2]['open'], prices[2]['low']),
            'reversal': before * after < 0,
            'dayOpen': str(prices[2]['open']), 'dayHigh': str(prices[2]['high']),
            'dayLow': str(prices[2]['low']), 'dayClose': str(prices[2]['close'])}


def average(items, key):
    values = [item[key] for item in items]
    return statistics.mean(values) if values else None


def summarize(items):
    return {'n': len(items), 'dayUpPct': 100 * sum(x['dayPct'] > 0 for x in items) / len(items) if items else None,
            'dayDownPct': 100 * sum(x['dayPct'] < 0 for x in items) / len(items) if items else None,
            'meanDayPct': average(items, 'dayPct'),
            'meanAbsDayPct': statistics.mean(abs(x['dayPct']) for x in items) if items else None,
            'meanDayRangePct': average(items, 'dayRangePct'),
            'before2UpPct': 100 * sum(x['before2Pct'] > 0 for x in items) / len(items) if items else None,
            'after2UpPct': 100 * sum(x['after2Pct'] > 0 for x in items) / len(items) if items else None,
            'reversalPct': 100 * sum(x['reversal'] for x in items) / len(items) if items else None}


def build():
    snapshot, generated, source_hash = read_snapshot()
    index = inspect(snapshot, FIRST - 2 * DAY, LAST + 2 * DAY, generated)
    _, boundaries = episode_plan(FIRST, LAST)
    event_days = {at.date() for _, at in boundaries if FIRST <= at.date() <= LAST}
    event_rows, controls_by_event = [], {}
    for term, at in boundaries:
        center = at.date()
        if not FIRST <= center <= LAST - 2 * DAY:
            continue
        result = measure(center, index)
        event = {'term': term, 'atLocal': at.isoformat(), 'date': center.isoformat(),
                 'period': '2023-2025' if center.year < 2026 else '2026-to-10-16',
                 'valid': result is not None}
        if result:
            event.update(result)
        event_rows.append(event)
        controls = []
        month_start = center.replace(day=1)
        next_month = (month_start.replace(day=28) + 4 * DAY).replace(day=1)
        day = month_start
        while day < next_month and day <= LAST - 2 * DAY:
            if day >= FIRST + 2 * DAY and all(abs((day - event_day).days) > 2 for event_day in event_days):
                measured = measure(day, index)
                if measured:
                    controls.append(measured)
            day += DAY
        controls_by_event[center.isoformat()] = controls
    periods = {}
    for name in ('2023-2025', '2026-to-10-16'):
        events = [row for row in event_rows if row['period'] == name and row['valid']]
        control = [item for row in events for item in controls_by_event[row['date']]]
        periods[name] = {'jie': summarize(events), 'sameMonthNonJie': summarize(control),
                         'eventDates': [row['date'] for row in events],
                         'excludedEventDates': [row['date'] for row in event_rows
                                                if row['period'] == name and not row['valid']]}
    return {'studyType': 'exploratory_retrospective_not_prediction',
            'source': 'sealed Coinbase Exchange BTC/USD Beijing daily snapshot',
            'snapshotSha256': source_hash, 'sourceEnd': LAST.isoformat(),
            'eventDefinition': '12 Jie month-changing transitions; exact local time from calendar engine',
            'window': 'two complete Beijing days before, mixed transition day, two complete days after',
            'control': 'valid same Gregorian-month non-Jie center dates, more than two days from every Jie',
            'limits': ['The transition daily candle mixes old and new months.',
                       'A daily candle cannot locate an intraday move at the exact Jie instant.',
                       'All periods are retrospective and previously observed; controls are descriptive, not causal.',
                       'Overlapping two-day returns and multiple comparisons prevent a simple prediction claim.'],
            'periods': periods, 'events': event_rows}


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False, indent=2, allow_nan=False))
