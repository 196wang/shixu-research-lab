"""Fixed exploratory screen for Mercury stations/retrograde and lunar phases.

Price: the existing verified Bitstamp hourly-derived Beijing days, 2013–2026.
Sky: the repository's pinned Astronomy Engine 2.1.19, geocentric tropical.
No market direction is inferred from astronomy and no signal is published.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import hashlib
import json
import statistics
import sys

from build_bitstamp_long_history import FIRST_DAY, LAST_DAY, LOCAL
from build_bitstamp_long_study import load_local

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
import astronomy  # noqa: E402

OUTPUT = ROOT / 'research' / 'astro-pattern-screen-2013-2026.json'
SPLITS = {'2013-2020': (2013, 2020), '2021-2023': (2021, 2023),
          '2024-2026': (2024, 2026)}
UTC = timezone.utc


def engine_time(instant: datetime):
    value = instant.astimezone(UTC)
    return astronomy.Time.Make(value.year, value.month, value.day, value.hour,
                               value.minute, value.second + value.microsecond / 1_000_000)


def mercury_longitude(instant: datetime):
    vector = astronomy.GeoVector(astronomy.Body.Mercury, engine_time(instant), True)
    return astronomy.Ecliptic(vector).elon % 360


def mercury_speed(instant: datetime):
    """Centered one-day apparent angular velocity, handling the 0/360 wrap."""
    a = mercury_longitude(instant - timedelta(hours=12))
    b = mercury_longitude(instant + timedelta(hours=12))
    return (b - a + 180) % 360 - 180


def mercury_stations(first: datetime, last: datetime):
    """Bracket daily speed sign changes, then bisect to < 1 minute."""
    stations = []
    left = first
    left_speed = mercury_speed(left)
    while left < last:
        right = min(left + timedelta(days=1), last)
        right_speed = mercury_speed(right)
        if left_speed * right_speed < 0:
            lo, hi = left, right
            for _ in range(18):
                mid = lo + (hi - lo) / 2
                if mercury_speed(mid) * left_speed > 0:
                    lo = mid
                else:
                    hi = mid
            instant = lo + (hi - lo) / 2
            stations.append({'atUtc': instant.isoformat(),
                             'date': instant.astimezone(LOCAL).date().isoformat(),
                             'kind': 'retrograde_start' if left_speed > 0 else 'direct_start'})
        left, left_speed = right, right_speed
    return stations


def moon_events(first: datetime, last: datetime):
    events = []
    quarter = astronomy.SearchMoonQuarter(engine_time(first))
    while True:
        at = datetime.fromisoformat(str(quarter.time).replace('Z', '+00:00'))
        if at >= last:
            break
        if quarter.quarter in (0, 2):
            events.append({'atUtc': at.isoformat(),
                           'date': at.astimezone(LOCAL).date().isoformat(),
                           'kind': 'new_moon' if quarter.quarter == 0 else 'full_moon'})
        quarter = astronomy.NextMoonQuarter(quarter)
    return events


def mean(values):
    return statistics.mean(values) if values else None


def clean_days(raw):
    rows = {}
    for label, source in raw.items():
        if source['quality'] != 'strict':
            continue
        opened, closed = float(source['open']), float(source['close'])
        rows[date.fromisoformat(label)] = {
            'returnPct': 100 * (closed / opened - 1),
            'rangePct': 100 * (float(source['high']) - float(source['low'])) / opened,
            'close': closed,
        }
    ordered = sorted(rows)
    for day in ordered:
        prior = [rows.get(day - timedelta(days=offset)) for offset in range(1, 31)]
        if all(item is not None for item in prior):
            baseline = statistics.median(item['rangePct'] for item in prior)
            rows[day]['bigRange'] = rows[day]['rangePct'] >= 1.5 * baseline
        else:
            rows[day]['bigRange'] = None
    return rows


def metrics(rows, dates):
    values = [rows[d] for d in dates if d in rows]
    ranges = [r for r in values if r['bigRange'] is not None]
    return {'days': len(values),
            'upRatePct': 100 * mean([r['returnPct'] > 0 for r in values]) if values else None,
            'meanReturnPct': mean([r['returnPct'] for r in values]),
            'medianReturnPct': statistics.median(r['returnPct'] for r in values) if values else None,
            'meanRangePct': mean([r['rangePct'] for r in values]),
            'bigRangeDays': len(ranges),
            'bigRangeRatePct': 100 * mean([r['bigRange'] for r in ranges]) if ranges else None}


def matched(rows, exposure, controls):
    """Each event day is paired to its own year-calendar-month control average."""
    groups = defaultdict(list)
    for day in controls:
        if day in rows:
            groups[(day.year, day.month)].append(day)
    used = [day for day in exposure if day in rows and groups[(day.year, day.month)]]
    trigger = metrics(rows, used)
    matched_means = [metrics(rows, groups[(day.year, day.month)]) for day in used]
    base = {field: mean([item[field] for item in matched_means if item[field] is not None])
            for field in ('upRatePct', 'meanReturnPct', 'meanRangePct', 'bigRangeRatePct')}
    return {'trigger': trigger, 'sameYearMonthControl': base,
            'unmatchedDays': len([d for d in exposure if d in rows]) - len(used)}


def split_screen(rows, exposure, controls):
    result = {}
    for label, (start, end) in SPLITS.items():
        result[label] = matched(rows, [d for d in exposure if start <= d.year <= end],
                                [d for d in controls if start <= d.year <= end])
    return result


def station_reversal(rows, anchors):
    """Two nonoverlapping 7-day close-to-close legs, with event day excluded."""
    outcome = []
    for day in anchors:
        required = [day - timedelta(days=n) for n in (8, 1)] + [day, day + timedelta(days=7)]
        if not all(d in rows for d in required):
            continue
        before = 100 * (rows[day - timedelta(days=1)]['close'] /
                        rows[day - timedelta(days=8)]['close'] - 1)
        after = 100 * (rows[day + timedelta(days=7)]['close'] / rows[day]['close'] - 1)
        outcome.append({'date': day.isoformat(), 'beforePct': before, 'afterPct': after,
                        'materialReversal': before * after < 0 and
                        min(abs(before), abs(after)) >= 2})
    return {'n': len(outcome), 'materialReversals': sum(r['materialReversal'] for r in outcome),
            'ratePct': 100 * mean([r['materialReversal'] for r in outcome]) if outcome else None}


def build():
    manifest, raw = load_local()
    rows = clean_days(raw)
    start = datetime(2012, 12, 20, tzinfo=UTC)
    end = datetime(2026, 11, 20, tzinfo=UTC)
    stations = mercury_stations(start, end)
    moons = moon_events(start, end)
    # Station dates contain both direct and retrograde hours; keep them out of
    # full-day state comparisons. D0 is used only for event-day summaries.
    station_dates = {date.fromisoformat(item['date']) for item in stations}
    state = {}
    for day in rows:
        if day in station_dates:
            continue
        instant = datetime.combine(day, time(12), LOCAL)
        state[day] = 'retrograde' if mercury_speed(instant) < 0 else 'direct'
    retro = [d for d, value in state.items() if value == 'retrograde']
    direct = [d for d, value in state.items() if value == 'direct']
    moon_dates = {kind: {date.fromisoformat(item['date']) for item in moons if item['kind'] == kind}
                  for kind in ('new_moon', 'full_moon')}
    all_moon = moon_dates['new_moon'] | moon_dates['full_moon']
    station_by_kind = {kind: {date.fromisoformat(item['date']) for item in stations if item['kind'] == kind}
                       for kind in ('retrograde_start', 'direct_start')}
    other_station = [d for d in rows if d not in station_dates]
    non_moon = [d for d in rows if d not in all_moon]
    other_all = [d for d in rows if d not in station_dates]
    outcomes = {
        'mercuryRetrogradeFullDays': split_screen(rows, retro, direct),
        'retrogradeStartDay': split_screen(rows, station_by_kind['retrograde_start'], other_station),
        'directStartDay': split_screen(rows, station_by_kind['direct_start'], other_station),
        'newMoonDay': split_screen(rows, moon_dates['new_moon'], non_moon),
        'fullMoonDay': split_screen(rows, moon_dates['full_moon'], non_moon),
    }
    # Event-day class and future-day class are deliberately distinct; phase
    # times occur inside D0, so D0 performance is descriptive, not tradable.
    for kind in ('new_moon', 'full_moon'):
        next_days = {d + timedelta(days=1) for d in moon_dates[kind]}
        controls = [d for d in rows if d not in next_days]
        outcomes[kind + 'NextDay'] = split_screen(rows, next_days, controls)
    reversal = {}
    for label, (first, last) in SPLITS.items():
        anchors = [d for d in station_dates if first <= d.year <= last]
        controls = [d for d in other_all if first <= d.year <= last]
        event = station_reversal(rows, anchors)
        control_dates = set()
        by_month = defaultdict(list)
        for d in controls:
            by_month[(d.year, d.month)].append(d)
        for d in anchors:
            control_dates.update(by_month[(d.year, d.month)])
        reversal[label] = {'station': event, 'sameStationMonthsControl':
                           station_reversal(rows, sorted(control_dates))}
    report = {
        'studyType': 'exploratory_retrospective_not_prediction',
        'data': {'provider': 'Bitstamp BTC/USD official hourly OHLC',
                 'firstDay': FIRST_DAY.isoformat(), 'lastDay': LAST_DAY.isoformat(),
                 'capturedAtUtc': manifest['capturedAtUtc'],
                 'rawManifestSha256': hashlib.sha256((ROOT / 'local_data' /
                    'bitstamp_btcusd_hourly_2013_2026_v1' / 'manifest.json').read_bytes()).hexdigest(),
                 'strictDays': len(rows), 'thinDaysExcluded': len(raw) - len(rows),
                 'dayBoundary': 'Asia/Shanghai UTC+08:00 midnight'},
        'astronomy': {'engine': 'Astronomy Engine 2.1.19', 'coordinates': 'geocentric apparent tropical',
                      'stationDefinition': 'centered 24h ecliptic longitude velocity zero, bisected <1min',
                      'stationTimeUncertainty': 'numerical velocity approximates instantaneous speed',
                      'moonDefinition': 'exact geocentric 0/180-degree lunar elongation',
                      'dates': 'event UTC instant assigned to UTC+08:00 calendar date',
                      'calculationIsMarketBlind': True},
        'method': {'splits': SPLITS, 'direction': 'Beijing day open-to-close',
                   'range': '(daily high-low)/open',
                   'bigRange': 'range >= 1.5 x prior 30 consecutive strict-day median',
                   'matchedControl': 'equal weight per exposed day, controls in same Gregorian year/month',
                   'stationReversal': 'opposite signs and each >=2%, seven-day close legs excluding D0',
                   'multipleTests': 'six calendar screens and one reversal screen; no tuning or p-value claim',
                   'selection': 'informed by publicly discussed claims; no blind holdout'},
        'stationCount': len(stations), 'moonEventCount': len(moons),
        'sampleStations2026': [item for item in stations if item['date'].startswith('2026-')],
        'outcomes': outcomes, 'stationReversal': reversal,
        'sources': [
            {'role': 'claim example', 'url': 'https://medium.com/gains-associates/predicting-bitcoin-price-using-astrology-coincidence-or-fact-e76f4b18799a'},
            {'role': 'contrary replication', 'url': 'https://astrolog-app.com/en/studies/mercury-retrograde'},
            {'role': 'moon study', 'url': 'https://github.com/rangiho/Bitcoin-Returns-and-Lunar-Phases'},
            {'role': 'NASA apparent motion', 'url': 'https://starchild.gsfc.nasa.gov/docs/StarChild/questions/question46.html'},
            {'role': 'ephemeris library', 'url': 'https://github.com/cosinekitty/astronomy/blob/master/source/python/README.md'},
            {'role': 'Bitstamp public API', 'url': 'https://www.bitstamp.net/api/'}],
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return report


if __name__ == '__main__':
    value = build()
    print(json.dumps({'output': str(OUTPUT), 'stationCount': value['stationCount'],
                      'moonEventCount': value['moonEventCount'],
                      'splits': value['outcomes']['mercuryRetrogradeFullDays']}, ensure_ascii=False))
