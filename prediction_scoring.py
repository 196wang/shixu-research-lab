"""Pure, per-version prediction outcomes from one immutable market snapshot.

No I/O, current-clock reads, registration, optimization or database writes.
The caller controls and authenticates as_of; late versions remain descriptive.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import re

from gann_timing import validate_row, snapshot_hash, json_safe

VERSION = '0.15.0'
LOCAL = timezone(timedelta(hours=8))
UTC = timezone.utc
DAY = timedelta(days=1)
KINDS = ('up', 'down', 'high', 'low', 'time')
SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'BTCUSD', 'ETHUSD')
DEFINITION = {
    'algorithmId': 'PREDICTION-SCORE-V15-001', 'timeZone': 'UTC+8',
    'directions': {'windowsDays': [1, 3, 7, 30], 'start': 'target_day_open',
                   'end': 'target_plus_horizon_minus_one_close',
                   'comparison': 'Decimal_last_close_vs_first_open', 'flat': 'tie_not_correct',
                   'toleranceApplied': False},
    'timing': {'toleranceCalendarDays': 3, 'leftBars': 2, 'rightBars': 2,
               'strictComparison': True, 'ties': 'no_pivot', 'dualHighLow': 'ambiguous_excluded',
               'requiredDates': 'target_minus_5_through_target_plus_5',
               'matureAt': 'target_plus_6_00:00',
               'selection': 'absolute_day_error_then_earlier_date',
               'directionAccuracyIncluded': False},
    'availability': {'completeWindowRequired': True, 'noFutureOrLateObservedRows': True,
                     'missingOrConflictedOrMalformed': 'missing', 'matureComparison': 'as_of_gte_end',
                     'maxPriceTokenLength': 200, 'maxAbsolutePriceAdjustedExponent': 100},
    'eligibility': {'directionDeadline': 'target_00:00', 'timingDeadline': 'target_minus_3_00:00',
                    'creationComparison': 'strictly_before_deadline',
                    'lateRevisionOrLedgerIneligible': 'descriptive_only'},
    'invalidation': {'firstOpen': 'first_local_midnight_gte_created',
                     'until': 'min_as_of_score_window_end', 'strictThreshold': True,
                     'scoreRetained': True, 'firstTriggerAfterGaps': 'earliest_observed_not_certain_first'},
}


def _instant(value, name):
    if isinstance(value, str):
        if value.endswith('-00:00') or not re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError(name + ' 必须是明确时区的 ISO 时间')
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValueError(name + ' 无效') from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(name + ' 必须包含明确时区')
    return value.astimezone(UTC)


def _open(day):
    return datetime.combine(day, time(), LOCAL)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('targetDate 必须是 YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('targetDate 无效') from exc
    if not 2020 <= parsed.year <= 2099:
        raise ValueError('targetDate 范围为 2020–2099 年')
    return parsed


def _normalize(record, snapshot, as_of):
    if not isinstance(record, dict):
        raise ValueError('record 必须是对象')
    if record.get('symbol') not in SYMBOLS or record.get('kind') not in KINDS:
        raise ValueError('symbol 或 kind 不受支持')
    horizon = record.get('horizonDays')
    if type(horizon) is not int or horizon not in (1, 3, 7, 30):
        raise ValueError('horizonDays 必须为 1、3、7、30')
    target = _date(record.get('targetDate'))
    created, observed = _instant(record.get('createdAtUtc'), 'createdAtUtc'), _instant(as_of, 'as_of')
    if observed < created:
        raise ValueError('as_of 不能早于该版本登记时刻')
    for field in ('prospectiveEligible', 'lateRevision'):
        if field in record and type(record[field]) is not bool:
            raise ValueError(field + ' 必须是布尔值')
    condition = record.get('invalidation')
    if condition is not None:
        if not isinstance(condition, dict) or set(condition) != {'type', 'price'} or condition['type'] not in ('close_below', 'close_above'):
            raise ValueError('invalidation 条件无效')
        price = condition['price']
        if not isinstance(price, str) or not 1 <= len(price) <= 200 or not re.fullmatch(r'\d+(?:\.\d+)?', price):
            raise ValueError('失效价格必须为正十进制字符串')
        try:
            number = Decimal(price)
            if not number.is_finite() or number <= 0:
                raise ValueError('失效价格必须大于零')
        except InvalidOperation as exc:
            raise ValueError('失效价格无效') from exc
    if not isinstance(snapshot, (tuple, list)) or len(snapshot) != 3 or not all(isinstance(part, dict) for part in snapshot):
        raise ValueError('snapshot 必须为 (state,candles,gaps)')
    if snapshot[0].get('symbol') is not None and snapshot[0]['symbol'] != record['symbol']:
        raise ValueError('行情快照币种与记录不一致')
    return target, created, observed, condition


def _bounded_price(value):
    """Bound work before the shared validator or return-ratio Decimal arithmetic."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return False
    try:
        token = str(value)
        if not 1 <= len(token) <= 200:
            return False
        price = Decimal(token)
        return price.is_finite() and price > 0 and abs(price.adjusted()) <= 100
    except (ValueError, InvalidOperation, OverflowError):
        return False


def _direction(prices, days, kind):
    first, last = prices[days[0]]['open'], prices[days[-1]]['close']
    actual = 'up' if last > first else 'down' if last < first else 'flat'
    status = 'tie' if actual == 'flat' else 'correct' if actual == kind else 'incorrect'
    # Classification above does not depend on this rounded display ratio.
    with localcontext() as ctx:
        ctx.prec = max(50, len(first.as_tuple().digits), len(last.as_tuple().digits)) + 10
        fraction = (last - first) / first
        percent = fraction * 100
    return status, {'expected': kind, 'actual': actual,
                    'correct': None if actual == 'flat' else actual == kind,
                    'firstOpen': str(first), 'lastClose': str(last),
                    'returnFraction': str(fraction), 'returnPercent': str(percent)}


def _timing(prices, target, kind):
    candidates, ambiguous = [], []
    for offset in range(-3, 4):
        day = target + offset * DAY
        neighbors = [prices[day + gap * DAY] for gap in (-2, -1, 1, 2)]
        high = all(prices[day]['high'] > row['high'] for row in neighbors)
        low = all(prices[day]['low'] < row['low'] for row in neighbors)
        if high and low:
            ambiguous.append(day.isoformat())
            continue
        pivot_kind = 'high' if high else 'low' if low else None
        if pivot_kind is not None and (kind == 'time' or kind == pivot_kind):
            candidates.append({'date': day.isoformat(), 'kind': pivot_kind,
                               'knownAtUtc': (_open(day) + 3 * DAY).astimezone(UTC).isoformat()})
    candidates.sort(key=lambda item: (abs((date.fromisoformat(item['date']) - target).days), item['date']))
    match = candidates[0] if candidates else None
    error = (date.fromisoformat(match['date']) - target).days if match else None
    status = 'miss' if match is None else 'exact' if error == 0 else 'within_tolerance'
    return status, {
        'toleranceDays': 3, 'candidateStartDate': (target - 3 * DAY).isoformat(),
        'candidateEndDate': (target + 3 * DAY).isoformat(),
        'matchedDate': match['date'] if match else None, 'matchedKind': match['kind'] if match else None,
        'signedErrorDays': error, 'absoluteErrorDays': abs(error) if error is not None else None,
        'candidates': candidates, 'ambiguousDates': ambiguous,
    }


def score_prediction(record, snapshot, as_of) -> dict:
    target, created, observed, condition = _normalize(record, snapshot, as_of)
    state, candles, gaps = snapshot
    kind, symbol, horizon = record['kind'], record['symbol'], record['horizonDays']
    is_direction = kind in ('up', 'down')
    first = target if is_direction else target - 5 * DAY
    last = target + (horizon - 1) * DAY if is_direction else target + 5 * DAY
    window_start, window_end = _open(first), _open(last) + DAY
    score_days = [first + offset * DAY for offset in range((last - first).days + 1)]
    deadline = _open(target if is_direction else target - 3 * DAY)
    eligibility_reasons = []
    if created >= deadline:
        eligibility_reasons.append('created_not_before_window_deadline')
    if record.get('prospectiveEligible') is False:
        eligibility_reasons.append('ledger_marked_ineligible')
    if record.get('lateRevision') is True:
        eligibility_reasons.append('late_revision')
    eligible = not eligibility_reasons
    selected = {}
    price_by_day, problems_by_day = {}, {}

    def inspect(day, role):
        if day not in selected:
            opened = _open(day)
            key = int(opened.timestamp() * 1000)
            row, raw_gaps = candles.get(key), gaps.get(key, [])
            bounded_row = row
            if isinstance(row, dict):
                bounded_row = dict(row)
                for field in ('open', 'high', 'low', 'close'):
                    if not _bounded_price(row.get(field)):
                        bounded_row[field] = None
            problems, prices = validate_row(bounded_row, opened, symbol, observed)
            if not isinstance(raw_gaps, list) or any(not isinstance(reason, str) for reason in raw_gaps):
                problems.append('invalid_gap_metadata')
            else:
                problems.extend(raw_gaps)
            selected[day] = {'date': day.isoformat(), 'roles': [], 'row': deepcopy(row), 'gaps': deepcopy(raw_gaps)}
            price_by_day[day] = prices
            problems_by_day[day] = sorted(set(problems))
        if role not in selected[day]['roles']:
            selected[day]['roles'].append(role)
        return problems_by_day[day], price_by_day[day]

    missing = []
    for day in score_days:
        problems, _ = inspect(day, 'score')
        if problems:
            missing.append({'date': day.isoformat(), 'reasons': problems})
    direction_result = time_result = None
    if observed < window_end:
        status, reason = 'pending', 'window_not_mature'
    elif missing:
        status, reason = 'missing', 'required_window_has_unusable_days'
    elif is_direction:
        status, direction_result = _direction(price_by_day, score_days, kind)
        reason = 'fixed_direction_window_scored'
    else:
        status, time_result = _timing(price_by_day, target, kind)
        reason = 'fixed_timing_tolerance_scored'

    invalidation = {
        'configured': condition is not None, 'status': 'not_configured', 'invalidated': False,
        'type': condition['type'] if condition else None, 'price': condition['price'] if condition else None,
        'scanStartAt': None, 'scanEndExclusiveAt': None, 'observationCutoffAtUtc': None,
        'checkedDays': 0, 'coverageComplete': True,
        'firstObservedTriggerDate': None, 'firstObservedTriggerAtUtc': None, 'firstObservedClose': None,
        'firstTriggerCertain': False, 'missingDays': [],
    }
    if condition:
        local_created = created.astimezone(LOCAL)
        scan_start = _open(local_created.date())
        if scan_start < created:
            scan_start += DAY
        stop = min(observed, window_end)
        # Only completed days are scanned. Fractional current-day prices cannot
        # invalidate a condition defined on a daily close.
        scan_end = max(scan_start, _open(stop.astimezone(LOCAL).date()))
        invalidation.update({'scanStartAt': scan_start.isoformat(), 'scanEndExclusiveAt': scan_end.isoformat(),
                             'observationCutoffAtUtc': stop.astimezone(UTC).isoformat(),
                             'status': 'not_applicable' if window_end <= scan_start else 'pending',
                             'invalidated': None, 'coverageComplete': False})
        cursor, trigger, gaps_before_trigger = scan_start, None, False
        checked, bad_days = 0, []
        threshold = Decimal(condition['price'])
        while cursor < scan_end:
            day = cursor.date()
            problems, prices = inspect(day, 'invalidation')
            if problems:
                bad_days.append({'date': day.isoformat(), 'reasons': problems})
                if trigger is None:
                    gaps_before_trigger = True
            else:
                checked += 1
                close = prices['close']
                crossed = close < threshold if condition['type'] == 'close_below' else close > threshold
                if crossed and trigger is None:
                    trigger = (day, cursor + DAY, close)
            cursor += DAY
        invalidation.update({'checkedDays': checked, 'missingDays': bad_days,
                             'coverageComplete': not bad_days and scan_end > scan_start})
        if trigger:
            day, at, close = trigger
            invalidation.update({'status': 'triggered', 'invalidated': True,
                                 'firstObservedTriggerDate': day.isoformat(),
                                 'firstObservedTriggerAtUtc': at.astimezone(UTC).isoformat(),
                                 'firstObservedClose': str(close), 'firstTriggerCertain': not gaps_before_trigger})
        elif bad_days:
            invalidation.update({'status': 'missing', 'invalidated': None})
        elif scan_end > scan_start:
            invalidation.update({'status': 'clear', 'invalidated': False})

    # Valid cache rows retain their exact strings. Malformed non-JSON numeric
    # values are represented as strings so a missing report can still be saved.
    evidence_days = json_safe([selected[day] for day in sorted(selected)])
    evidence_state = json_safe(deepcopy(state))
    observation = observed.isoformat()
    evidence_hash = snapshot_hash({'state': evidence_state, 'asOfUtc': observation, 'selectedDays': evidence_days})
    return {
        'schemaVersion': 1, 'scoringVersion': VERSION, 'algorithmId': DEFINITION['algorithmId'],
        'recordId': record.get('id'), 'symbol': symbol, 'kind': kind, 'targetDate': target.isoformat(),
        'horizonDays': horizon, 'createdAtUtc': created.isoformat(), 'asOfUtc': observation,
        'status': status, 'reason': reason, 'prospectiveEligible': eligible,
        'eligibility': {'eligible': eligible, 'deadlineAtUtc': deadline.astimezone(UTC).isoformat(),
                        'reasons': eligibility_reasons},
        'windowStartAt': window_start.isoformat(), 'windowEndExclusiveAt': window_end.isoformat(),
        'window': {'startDate': first.isoformat(), 'endDate': last.isoformat(),
                   'startAtLocal': window_start.isoformat(), 'endExclusiveAtLocal': window_end.isoformat(),
                   'readyAtUtc': window_end.astimezone(UTC).isoformat(), 'requiredDays': len(score_days)},
        'directionResult': direction_result, 'timeResult': time_result,
        'directionAccuracyEligible': eligible and is_direction and status in ('correct', 'incorrect', 'tie'),
        'invalidation': invalidation,
        'dataQuality': {'requiredDays': len(score_days), 'usableDays': len(score_days) - len(missing),
                        'missingDays': missing},
        'evidence': {'marketGeneration': state.get('generation', 0), 'marketAsOfUtc': state.get('last_success'),
                     'state': evidence_state, 'snapshotSha256': evidence_hash, 'selectedDays': evidence_days,
                     'encoding': 'nonfinite_or_nonjson_values_stringified; valid_cache_strings_preserved'},
        'definition': deepcopy(DEFINITION), 'definitionSha256': snapshot_hash(DEFINITION),
    }
