"""Historical event generators with separate price-validity and chronology gates."""
from datetime import date, datetime, timedelta, timezone

from gann_timing import validate_row
from prediction_scoring import _bounded_price

LOCAL = timezone(timedelta(hours=8))
DAY = timedelta(days=1)
RULES = [
    {'id': 'BZ-DAY-METALWATER-UP', 'label': '日干金水上涨假设', 'family': 'BAZI', 'kind': 'up', 'version': '0.16.0'},
    {'id': 'BZ-DAY-FIRE-DOWN', 'label': '日干火下跌假设', 'family': 'BAZI', 'kind': 'down', 'version': '0.16.0'},
    {'id': 'GANN-EQUAL-INTERVAL', 'label': '江恩等距时间', 'family': 'GANN', 'kind': 'time', 'version': '0.16.0'},
    {'id': 'ASTRO-MOON-PHASE', 'label': '四种月相时间', 'family': 'ASTROLOGY', 'kind': 'time', 'version': '0.16.0'},
    {'id': 'ASTRO-NAKSHATRA-INGRESS', 'label': '月亮换宿时间', 'family': 'ASTROLOGY', 'kind': 'time', 'version': '0.16.0'},
]


def midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL)


def days_between(first, last):
    return [first + i * DAY for i in range((last - first).days + 1)]


def inspect_days(snapshot, first, last, asof):
    state, candles, gaps = snapshot
    if state.get('symbol') not in (None, 'BTCUSDT'):
        raise ValueError('历史探索固定使用BTCUSDT快照')
    result = {}
    for day in days_between(first, last):
        key = int(midnight(day).timestamp() * 1000)
        row = candles.get(key)
        bounded = dict(row) if isinstance(row, dict) else row
        if isinstance(bounded, dict):
            for field in ('open', 'high', 'low', 'close'):
                if not _bounded_price(bounded.get(field)):
                    bounded[field] = None
        reasons, prices = validate_row(bounded, midnight(day), 'BTCUSDT', asof)
        extra = gaps.get(key, [])
        if not isinstance(extra, list) or any(not isinstance(x, str) for x in extra):
            reasons.append('invalid_gap_metadata')
        else:
            reasons.extend(extra)
        result[day] = {'valid': not reasons, 'reasons': sorted(set(reasons)), 'prices': prices}
    return result


def pivot_kind(index, day):
    window = [index.get(day + offset * DAY) for offset in (-2, -1, 0, 1, 2)]
    if any(not row or not row['valid'] for row in window):
        return None
    center = window[2]['prices']
    other = [row['prices'] for i, row in enumerate(window) if i != 2]
    high = all(center['high'] > row['high'] for row in other)
    low = all(center['low'] < row['low'] for row in other)
    return 'ambiguous' if high and low else 'high' if high else 'low' if low else None


def pivot_id(day, kind, symbol='BTCUSDT'):
    return f'{symbol}-PIVOT2-{day}-{kind}'


def sequential_gann(index, target_start, target_end, asof, lookback=90, multipliers=(1, 2), symbol='BTCUSDT'):
    """Emit once per confirmed B, never re-anchor from a future extreme."""
    anchors, projections, previous = [], [], None
    for day in sorted(index):
        if not index[day]['valid']:
            previous = None
            continue
        kind = pivot_kind(index, day)
        if kind is None:
            continue
        known = midnight(day + 3 * DAY)
        if known > asof:
            continue
        anchor = {'id': pivot_id(day.isoformat(), kind, symbol), 'date': day.isoformat(), 'kind': kind,
                  'knownAtUtc': known.astimezone(timezone.utc).isoformat(),
                  'price': None if kind == 'ambiguous' else str(index[day]['prices'][kind])}
        anchors.append(anchor)
        if kind == 'ambiguous':
            previous = None
            continue
        if previous and previous['kind'] != kind:
            a = date.fromisoformat(previous['date'])
            intervals = (day - a).days
            if a >= known.astimezone(LOCAL).date() - lookback * DAY:
                for multiple in multipliers:
                    target = day + intervals * multiple * DAY
                    if not target_start <= target <= target_end:
                        continue
                    eligible = known < midnight(target - 3 * DAY)
                    projections.append({'id': f'GN-{previous["id"]}-{anchor["id"]}-x{multiple}',
                                        'ruleId': 'GANN-EQUAL-INTERVAL', 'family': 'GANN', 'kind': 'time',
                                        'targetDate': target.isoformat(), 'knownAtUtc': anchor['knownAtUtc'],
                                        'anchorAId': previous['id'], 'anchorBId': anchor['id'],
                                        'anchorADate': previous['date'], 'anchorBDate': anchor['date'],
                                        'intervalDays': intervals, 'multiplier': multiple,
                                        'eligible': eligible, 'exclusionReason': None if eligible else 'confirmation_not_before_tolerance_window',
                                        'historicalReconstruction': True, 'registeredPrediction': False})
        previous = anchor
    return anchors, projections


def gann_availability(index, target, offsets=(-272, -5)):
    missing = []
    for offset in range(offsets[0], offsets[1] + 1):
        day = target + offset * DAY
        row = index.get(day)
        if not row or not row['valid']:
            missing.append({'date': day.isoformat(), 'reasons': row['reasons'] if row else ['outside_source_span']})
    return not missing, missing


def calendar_events(target_days, generated, progress=None):
    from rule_events import matches_at
    result, events = {}, []
    for i, day in enumerate(target_days):
        key = day.isoformat()
        try:
            observed = matches_at(key + 'T00:00:00+08:00')
            matches = [m for m in observed['matches'] if m['ruleId'] in ('BZ-E01', 'BZ-E02')]
            ids = []
            for match in matches:
                identity = 'BZ-DAY-METALWATER-UP' if match['ruleId'] == 'BZ-E01' else 'BZ-DAY-FIRE-DOWN'
                ids.append(identity)
                events.append({'id': identity + '-' + key, 'ruleId': identity, 'family': 'BAZI',
                               'kind': 'up' if match['ruleId'] == 'BZ-E01' else 'down', 'targetDate': key,
                               'source': match, 'eligible': True, 'knownAtUtc': None,
                               'historicalReconstruction': True, 'registeredPrediction': False})
            result[key] = {'known': True, 'ruleIds': ids, 'pillars': observed['pillars'], 'errors': []}
        except Exception as exc:
            result[key] = {'known': False, 'ruleIds': [], 'errors': [type(exc).__name__ + ': ' + str(exc)]}
        if progress and (i % 100 == 0 or i + 1 == len(target_days)):
            progress({'stage': 'calendar', 'completed': i + 1, 'total': len(target_days), 'message': key})
    return result, events


def astronomical_events(first, last, generated, progress=None):
    from astro_events import calculate_astro_events
    result = {day.isoformat(): {'known': True, 'errors': []} for day in days_between(first, last)}
    events, cursor, batches = [], first, []
    while cursor <= last:
        end = min(last, cursor + 61 * DAY)
        batches.append((cursor, end))
        cursor = end + DAY
    for i, (begin, end) in enumerate(batches):
        try:
            batch = calculate_astro_events(begin.isoformat(), end.isoformat())
            for event in batch['events']:
                identity = 'ASTRO-MOON-PHASE' if event['kind'] == 'moon_phase' else 'ASTRO-NAKSHATRA-INGRESS'
                events.append({'id': event['id'], 'ruleId': identity, 'family': 'ASTROLOGY', 'kind': 'time',
                               'targetDate': event['atLocal'][:10], 'knownAtUtc': None, 'eligible': True,
                               'source': event, 'engineConfigurationSha256': batch['configurationSha256'],
                               'engineSourceHashes': batch['sourceHashes'],
                               'historicalReconstruction': True, 'registeredPrediction': False})
        except Exception as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            for day in days_between(begin, end):
                result[day.isoformat()] = {'known': False, 'errors': [error]}
        if progress:
            progress({'stage': 'astronomy', 'completed': i + 1, 'total': len(batches), 'message': begin.isoformat() + '..' + end.isoformat()})
    return result, events


def deduplicate_events(events):
    grouped = {}
    for event in events:
        if not event['eligible']:
            continue
        key = (event['ruleId'], event['targetDate'])
        item = grouped.setdefault(key, {'ruleId': key[0], 'targetDate': key[1], 'family': event['family'],
                                        'kind': event['kind'], 'sourceEventIds': []})
        if event['id'] not in item['sourceEventIds']:
            item['sourceEventIds'].append(event['id'])
    return sorted(grouped.values(), key=lambda e: (e['targetDate'], e['ruleId']))
