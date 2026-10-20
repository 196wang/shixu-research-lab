"""Read-only, retrospective pivot timing calculations. No registered forecasts."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re

VERSION = '0.14.0'
LOCAL = timezone(timedelta(hours=8))
DAY_MS = 86_400_000
SYMBOLS = ('BTCUSDT', 'ETHUSDT')
SPEC_SHA256 = 'af7e18248721bfbdaacf13bba6e443791753098438a8fa9a2916bda9084e5240'
SPEC_RELATIVE = '来源溯源/v0.14/江恩计算规范.md'
SPEC_PATH = Path(__file__).resolve().parent / SPEC_RELATIVE
SETTINGS = {
    'algorithmId': 'GN-PIVOT2-TIME-PROJECTION-001', 'algorithmVersion': '0.1.0',
    'timezone': 'UTC+8', 'interval': '1d', 'leftBars': 2, 'rightBars': 2,
    'strictComparison': True, 'tiesPolicy': 'no_pivot', 'ambiguousPolicy': 'break_pairing',
    'missingPolicy': 'break_segment', 'rangeBoundaryPolicy': 'no_external_padding',
    'pairing': 'consecutive_opposite_candidates_only', 'timeBasis': 'open_to_open_calendar_days',
    'multipliers': [1, 2], 'direction': None, 'retrospectiveCalculation': True,
    'registeredPrediction': False, 'scoringPerformed': False,
    'knownAtMeaning': 'right_2_bar_next_open', 'generatedAtMeaning': 'current_calculation_time',
}


class GannTimingError(RuntimeError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def parse_day(value):
    try:
        day = date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise GannTimingError('日期必须为YYYY-MM-DD') from exc
    if day.isoformat() != value or not date(2020, 1, 1) <= day <= date(2099, 12, 31):
        raise GannTimingError('日期范围为2020–2099年，格式为YYYY-MM-DD')
    return day


def day_open(day):
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL)


def decimal_price(value, field):
    if value is None or isinstance(value, (bool, dict, list)):
        raise ValueError('invalid_' + field)
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0 or not math.isfinite(float(number)):
            raise ValueError('invalid_' + field)
        return number
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('invalid_' + field) from exc


def json_safe(value):
    """Canonical hashing also represents malformed non-finite cache values."""
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def snapshot_hash(value):
    content = json.dumps(json_safe(value), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def verify_spec():
    try:
        actual = hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()
        code = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError as exc:
        raise GannTimingError('包内江恩规范或计算模块缺失，无法核验来源', 503) from exc
    if actual != SPEC_SHA256:
        raise GannTimingError('包内江恩计算规范哈希不匹配，拒绝计算', 503)
    return actual, code


def validate_row(row, opened, symbol, generated):
    if not isinstance(row, dict):
        return ['missing_bar' if row is None else 'invalid_bar_shape'], None
    problems, prices = [], {}
    opened_ms = int(opened.timestamp() * 1000)
    next_open = opened + timedelta(days=1)
    if row.get('open_ms') != opened_ms or row.get('close_ms') != opened_ms + DAY_MS - 1:
        problems.append('wrong_day_boundary')
    if row.get('symbol') != symbol:
        problems.append('wrong_symbol')
    if row.get('conflicted'):
        problems.append('source_conflict')
    if row.get('is_closed') != 1:
        problems.append('not_confirmed_closed')
    if opened > generated:
        problems.append('future_bar')
    if next_open > generated:
        problems.append('not_yet_closed')
    if not row.get('request_id') or not isinstance(row.get('body_sha256'), str) or not re.fullmatch('[a-f0-9]{64}', row['body_sha256']):
        problems.append('missing_source_reference')
    try:
        observed = datetime.fromisoformat(row.get('updated', '').replace('Z', '+00:00'))
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError('missing timezone')
        if observed > generated:
            problems.append('source_observed_after_generation')
    except (ValueError, TypeError, AttributeError):
        problems.append('invalid_source_observation_time')
    for field in ('open', 'high', 'low', 'close'):
        try:
            prices[field] = decimal_price(row.get(field), field)
        except ValueError as exc:
            problems.append(str(exc))
    if len(prices) == 4 and not prices['low'] <= min(prices['open'], prices['close']) <= max(prices['open'], prices['close']) <= prices['high']:
        problems.append('invalid_ohlc_order')
    return problems, prices


def calculate_gann(market, symbol, start: str, end: str, clock=None):
    if symbol not in SYMBOLS:
        raise GannTimingError('时间工具仅支持BTCUSDT和ETHUSDT')
    first, last = parse_day(start), parse_day(end)
    length = (last - first).days + 1
    if not 1 <= length <= 180:
        raise GannTimingError('开始日期须不晚于结束日期，含首尾最多180天')
    spec_sha, code_sha = verify_spec()
    try:
        state, candles, gaps = market._snapshot(symbol)
    except Exception as exc:
        raise GannTimingError('无法读取行情缓存：' + type(exc).__name__, 503) from exc
    if not all(isinstance(value, dict) for value in (state, candles, gaps)):
        raise GannTimingError('行情缓存快照格式无效', 503)
    generated = (clock or (lambda: datetime.now(timezone.utc)))()
    if not isinstance(generated, datetime) or generated.tzinfo is None or generated.utcoffset() is None:
        raise GannTimingError('系统时间缺少明确时区', 503)
    generated = generated.astimezone(timezone.utc)
    segments, active, excluded, raw_selection = [], [], [], []
    for offset in range(length):
        day = first + timedelta(days=offset)
        opened = day_open(day)
        key = int(opened.timestamp() * 1000)
        row, raw_gaps = candles.get(key), gaps.get(key, [])
        problems, prices = validate_row(row, opened, symbol, generated)
        if not isinstance(raw_gaps, list) or any(not isinstance(reason, str) for reason in raw_gaps):
            problems.append('invalid_gap_metadata')
        else:
            problems.extend(raw_gaps)
        raw_selection.append({'date': day.isoformat(), 'row': deepcopy(row), 'gaps': deepcopy(raw_gaps)})
        if problems:
            excluded.append({'date': day.isoformat(), 'reasons': sorted(set(problems))})
            if active:
                segments.append(active)
                active = []
        else:
            active.append({'date': day, 'at': opened, 'key': key, 'row': row, 'prices': prices})
    if active:
        segments.append(active)
    anchors, pairs, projections, segment_info = [], [], [], []
    for segment_number, segment in enumerate(segments, 1):
        segment_id = f'segment-{segment_number}'
        segment_info.append({'id': segment_id, 'startDate': segment[0]['date'].isoformat(),
                             'endDate': segment[-1]['date'].isoformat(), 'days': len(segment)})
        previous = None
        for i in range(2, len(segment) - 2):
            current, neighbors = segment[i], [segment[j] for j in (i - 2, i - 1, i + 1, i + 2)]
            high = all(current['prices']['high'] > other['prices']['high'] for other in neighbors)
            low = all(current['prices']['low'] < other['prices']['low'] for other in neighbors)
            if not high and not low:
                continue
            kind = 'ambiguous' if high and low else 'high' if high else 'low'
            known = segment[i + 2]['at'] + timedelta(days=1)
            window = segment[i - 2:i + 3]
            anchor = {
                'id': f'{symbol}-{current["date"].isoformat()}-{kind}', 'segmentId': segment_id,
                'date': current['date'].isoformat(), 'atLocal': current['at'].isoformat(), 'openTimeMs': current['key'],
                'type': kind, 'price': None if kind == 'ambiguous' else str(current['prices'][kind]),
                'high': str(current['prices']['high']), 'low': str(current['prices']['low']),
                'knownAt': known.isoformat(), 'knownAtUtc': known.astimezone(timezone.utc).isoformat(),
                'confirmationDate': segment[i + 2]['date'].isoformat(),
                'windowDates': [item['date'].isoformat() for item in window],
                'sourceRefs': [{'date': item['date'].isoformat(), 'requestId': item['row']['request_id'],
                                'bodySha256': item['row']['body_sha256']} for item in window],
                'eligibleForPairing': kind != 'ambiguous',
            }
            anchors.append(anchor)
            if kind == 'ambiguous':
                previous = None
                continue
            if previous and previous['type'] != kind:
                interval = (current['date'] - date.fromisoformat(previous['date'])).days
                pair = {'id': f'pair-{len(pairs) + 1}', 'segmentId': segment_id,
                        'anchorAId': previous['id'], 'anchorBId': anchor['id'],
                        'anchorADate': previous['date'], 'anchorBDate': anchor['date'],
                        'anchorAType': previous['type'], 'anchorBType': kind, 'intervalDays': interval,
                        'knownAt': anchor['knownAt'], 'knownAtUtc': anchor['knownAtUtc'], 'direction': None}
                pairs.append(pair)
                for multiple in (1, 2):
                    target = current['at'] + timedelta(days=interval * multiple)
                    retrospective, historical = target <= known, target <= generated
                    status = 'retrospective' if retrospective else 'historical' if historical else 'future_candidate'
                    projections.append({'id': f'{pair["id"]}-x{multiple}', 'pairId': pair['id'],
                                        'anchorAId': previous['id'], 'anchorBId': anchor['id'],
                                        'anchorADate': previous['date'], 'anchorBDate': anchor['date'],
                                        'intervalDays': interval, 'multiplier': multiple,
                                        'targetDate': target.date().isoformat(), 'targetAtLocal': target.isoformat(),
                                        'knownAt': anchor['knownAt'], 'knownAtUtc': anchor['knownAtUtc'],
                                        'status': status, 'retrospective': retrospective, 'historical': historical,
                                        'direction': None, 'registeredPrediction': False, 'marketValidated': False})
            # Consecutive same-type candidates are retained, without skipping
            # over them or retrospectively choosing a more extreme anchor.
            previous = anchor
    selected_snapshot = {'state': deepcopy(state), 'range': {'start': start, 'end': end}, 'selection': raw_selection}
    return {
        'schemaVersion': 1, 'version': VERSION, 'generatedAtUtc': generated.isoformat(), 'symbol': symbol,
        'range': {'start': start, 'end': end}, 'settings': deepcopy(SETTINGS),
        'settingsSha256': snapshot_hash(SETTINGS), 'codeSha256': code_sha,
        'dataQuality': {'requestedDays': length, 'validDays': length - len(excluded), 'excludedDays': len(excluded),
                        'segments': segment_info, 'excluded': excluded,
                        'insufficientSegmentCount': sum(len(segment) < 5 for segment in segments),
                        'ambiguousAnchorCount': sum(a['type'] == 'ambiguous' for a in anchors)},
        'anchors': anchors, 'pairs': pairs, 'projections': projections,
        'sources': [
            {'id': 'market-cache', 'kind': 'market_snapshot', 'symbol': symbol,
             'marketGeneration': state.get('generation', 0), 'marketAsOfUtc': state.get('last_success'),
             'snapshotSha256': snapshot_hash(selected_snapshot), 'snapshotReadAtUtc': generated.isoformat()},
            {'id': 'project-gann-spec', 'kind': 'project_spec', 'title': '江恩计算规范 GN-SPEC-0.3',
             'path': SPEC_RELATIVE, 'sha256': spec_sha},
        ],
        'marketValidated': False,
    }
