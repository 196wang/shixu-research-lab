"""Append-only, explicitly registered candidate validation using cache snapshots."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import threading
import uuid

from historical_chart import HERE, file_sha
from rule_events import input_hashes, matches_at

VERSION = '0.13.0'
LOCAL = timezone(timedelta(hours=8))
SYMBOL, PROFILE = 'BTCUSDT', 'BTC-BZ-V01'
HORIZONS = (1, 3, 7, 30)
DIRECTIONS = {'BZ-E01': 'up', 'BZ-E02': 'down', 'BZ-E03': None, 'BZ-E04': None}
START, END = date(2026, 9, 28), date(2026, 12, 31)
DAY_MS = 86_400_000


class ProspectiveError(RuntimeError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError('offset missing')
        return parsed.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProspectiveError('时间必须带明确时区', 400) from exc


def day_start(day):
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL)


def load_protocol(base):
    try:
        protocol = json.loads((Path(base) / 'prospective_protocol.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ProspectiveError('前瞻协议文件缺失或格式无效') from exc
    fixed = {'id': 'PROSPECTIVE-BZ-2026Q4-001', 'version': '0.1.0', 'symbol': SYMBOL, 'profile': PROFILE,
             'startDate': START.isoformat(), 'endDate': END.isoformat(),
             'registrationDeadlineAt': '2026-09-28T00:00:00+08:00',
             'observationEndExclusiveAt': '2027-01-30T00:00:00+08:00',
             'horizons': list(HORIZONS), 'primaryHorizonDays': 1, 'timezone': 'UTC+8',
             'matchingRulesVersion': '0.1.0', 'candidateDirectionsVersion': '0.1.0',
             'featureInstant': '00:00:00+08:00', 'calendarPolicyId': 'BZ-CALENDAR-CIVIL-00-V01',
             'windowConvention': 'inclusive_event_day', 'closePathDefinition': 'initial_open_then_daily_close',
             'missingPolicy': 'any_bad_day_masks_entire_window', 'baseline': 'same_rule_nontrigger_same_horizon',
             'directionComparison': 'exact_decimal_close_vs_open', 'tiePolicy': 'ties_in_denominator'}
    if any(protocol.get(key) != value for key, value in fixed.items()):
        raise ProspectiveError('前瞻协议与本版本固定规格不一致')
    rules = protocol.get('rules', [])
    if [r.get('id') for r in rules] != list(DIRECTIONS) or any(r.get('candidateDirection') != DIRECTIONS[r['id']] or r.get('version') != '0.1.0' for r in rules):
        raise ProspectiveError('前瞻候选规则或方向不符合固定版本')
    return protocol


def dependencies(base):
    try:
        hashes = input_hashes(base)
        for name in ('prospective.py', 'prospective_protocol.json', 'test_prospective.py', 'market_feed.py', 'test_market_feed.py'):
            hashes[name] = file_sha(Path(base) / name)
        return hashes
    except Exception as exc:
        raise ProspectiveError(f'无法核验前瞻依赖：{exc}') from exc


def make_schedule(protocol):
    result = []
    current = START
    while current <= END:
        at = day_start(current).isoformat()
        observed = matches_at(at)
        by_id = {m['ruleId']: m for m in observed['matches']}
        result.append({'date': current.isoformat(), 'atLocal': at, 'pillars': observed['pillars'],
                       'matches': observed['matches'],
                       'rules': {rule['id']: {'triggered': rule['id'] in by_id,
                                            'candidateDirection': rule['candidateDirection'],
                                            'reason': by_id.get(rule['id'], {}).get('reason', '未满足固定匹配条件')}
                                 for rule in protocol['rules']}})
        current += timedelta(days=1)
    return result


def finite_price(value, name, positive=True):
    if value is None or isinstance(value, (bool, dict, list)):
        raise ValueError('invalid_' + name)
    try:
        number = Decimal(str(value))
        if not number.is_finite() or not math.isfinite(float(number)) or (number <= 0 if positive else number < 0):
            raise ValueError('invalid_' + name)
        return number
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('invalid_' + name) from exc


def validate_candle(row, opened, asof):
    reasons = []
    if not row:
        return ['missing_bar']
    if row.get('open_ms') != opened or row.get('close_ms') != opened + DAY_MS - 1:
        reasons.append('wrong_day_boundary')
    if row.get('symbol') != SYMBOL:
        reasons.append('wrong_symbol')
    if row.get('conflicted'):
        reasons.append('source_conflict')
    if row.get('is_closed') != 1:
        reasons.append('not_confirmed_closed')
    if not row.get('request_id') or not re.fullmatch(r'[a-f0-9]{64}', row.get('body_sha256') or ''):
        reasons.append('missing_source_reference')
    try:
        if instant(row.get('updated')) > asof:
            reasons.append('source_observed_after_evaluation')
    except ProspectiveError:
        reasons.append('invalid_source_observation_time')
    prices = {}
    for key in ('open', 'high', 'low', 'close', 'volume'):
        try:
            prices[key] = finite_price(row.get(key), key, key != 'volume')
        except ValueError as exc:
            reasons.append(str(exc))
    if len(prices) == 5 and not prices['low'] <= min(prices['open'], prices['close']) <= max(prices['open'], prices['close']) <= prices['high']:
        reasons.append('invalid_ohlc_order')
    return reasons


def evaluate_window(day, horizon, candles, gaps, asof):
    if horizon not in HORIZONS or isinstance(horizon, bool):
        raise ProspectiveError('未知观察窗口', 400)
    opened = day_start(date.fromisoformat(day))
    end_exclusive = opened + timedelta(days=horizon)
    result = {'status': 'pending', 'reason': 'not_due', 'startDate': day,
              'endDate': (end_exclusive.date() - timedelta(days=1)).isoformat(),
              'endExclusiveAt': end_exclusive.isoformat(), 'horizonDays': horizon,
              'returnPct': None, 'maxDrawdownPct': None, 'missingDates': [], 'missingReasons': {},
              'directions': {'up': None, 'down': None}}
    if asof < end_exclusive:
        return result
    window = []
    for offset in range(horizon):
        moment = opened + timedelta(days=offset)
        key = int(moment.timestamp() * 1000)
        row = candles.get(key)
        reasons = validate_candle(row, key, asof)
        reasons.extend(gaps.get(key, []))
        if reasons:
            day_key = moment.date().isoformat()
            result['missingDates'].append(day_key)
            result['missingReasons'][day_key] = sorted(set(reasons))
        else:
            window.append(row)
    if result['missingDates']:
        return {**result, 'status': 'missing', 'reason': 'incomplete_market_window'}
    first_open = finite_price(window[0]['open'], 'open')
    closes = [finite_price(row['close'], 'close') for row in window]
    peak, worst = first_open, Decimal(0)
    for close in closes:
        peak = max(peak, close)
        worst = min(worst, (close / peak - 1) * 100)
    last_close = closes[-1]
    sign = 'tie' if last_close == first_open else ('up' if last_close > first_open else 'down')
    return {**result, 'status': 'scored', 'reason': None,
            'returnPct': float((last_close / first_open - 1) * 100), 'maxDrawdownPct': float(worst),
            'directions': {direction: 'tie' if sign == 'tie' else ('hit' if sign == direction else 'miss') for direction in ('up', 'down')}}


def group_summary(outcomes, direction):
    scored = [value for value in outcomes if value['status'] == 'scored']
    returns = [value['returnPct'] for value in scored]
    drawdowns = [value['maxDrawdownPct'] for value in scored]
    counts = {name: sum(value['directions'][direction] == name for value in scored) if direction else None for name in ('hit', 'miss', 'tie')}
    return {'eligible': len(outcomes), 'scored': len(scored),
            'missing': sum(value['status'] == 'missing' for value in outcomes),
            'pending': sum(value['status'] == 'pending' for value in outcomes),
            'hits': counts['hit'], 'misses': counts['miss'], 'ties': counts['tie'],
            'hitRatePct': counts['hit'] / len(scored) * 100 if direction and scored else None,
            'meanReturnPct': statistics.mean(returns) if returns else None,
            'medianReturnPct': statistics.median(returns) if returns else None,
            'meanMaxDrawdownPct': statistics.mean(drawdowns) if drawdowns else None}


def summarize(daily, protocol):
    result = []
    for rule in protocol['rules']:
        for horizon in HORIZONS:
            triggered, baseline = [], []
            for day in daily:
                target = triggered if day['rules'][rule['id']]['triggered'] else baseline
                target.append(day['horizons'][str(horizon)])
            trig, base = group_summary(triggered, rule['candidateDirection']), group_summary(baseline, rule['candidateDirection'])
            def delta(key):
                return trig[key] - base[key] if trig[key] is not None and base[key] is not None else None
            result.append({'ruleId': rule['id'], 'title': rule['title'], 'candidateDirection': rule['candidateDirection'],
                           'horizonDays': horizon, 'isPrimary': horizon == protocol['primaryHorizonDays'],
                           'triggered': trig, 'baseline': base,
                           'comparison': {'meanReturnDeltaPctPoints': delta('meanReturnPct'),
                                          'meanMaxDrawdownDeltaPctPoints': delta('meanMaxDrawdownPct'),
                                          'hitRateDeltaPctPoints': delta('hitRatePct')},
                           'baselineDirectionIsForecast': False, 'marketValidated': False})
    return result


class ProspectiveStudy:
    def __init__(self, data_dir, market, clock=None, base=None):
        self.base = Path(base or HERE)
        self.path = Path(data_dir) / 'prospective.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.market = market
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS prospective_registrations(
                id TEXT PRIMARY KEY,slot INTEGER NOT NULL UNIQUE CHECK(slot=1),registered TEXT NOT NULL,
                record TEXT NOT NULL,record_sha TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS prospective_evaluations(
                id TEXT PRIMARY KEY,registration_id TEXT NOT NULL REFERENCES prospective_registrations(id),
                created TEXT NOT NULL,asof TEXT NOT NULL,report TEXT NOT NULL,report_sha TEXT NOT NULL,
                previous_report_sha TEXT);
              CREATE TRIGGER IF NOT EXISTS prospective_reg_no_update BEFORE UPDATE ON prospective_registrations
                BEGIN SELECT RAISE(ABORT,'prospective registration is append-only'); END;
              CREATE TRIGGER IF NOT EXISTS prospective_reg_no_delete BEFORE DELETE ON prospective_registrations
                BEGIN SELECT RAISE(ABORT,'prospective registration is append-only'); END;
              CREATE TRIGGER IF NOT EXISTS prospective_eval_no_update BEFORE UPDATE ON prospective_evaluations
                BEGIN SELECT RAISE(ABORT,'prospective evaluation is append-only'); END;
              CREATE TRIGGER IF NOT EXISTS prospective_eval_no_delete BEFORE DELETE ON prospective_evaluations
                BEGIN SELECT RAISE(ABORT,'prospective evaluation is append-only'); END;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ProspectiveError('系统时钟必须有明确时区', 400)
        return value.astimezone(timezone.utc)

    def _read(self):
        with self.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM prospective_registrations WHERE slot=1').fetchone()
            evaluation_rows = list(db.execute('SELECT * FROM prospective_evaluations ORDER BY rowid'))
        try:
            record = json.loads(row['record']) if row else None
            if record and (digest(record) != row['record_sha'] or digest(record['schedule']) != record['scheduleSha256'] or digest(record['protocol']) != record['protocolSha256']):
                raise ProspectiveError('前瞻登记或计划内容哈希不匹配')
            reports, previous = [], None
            for evaluation in evaluation_rows:
                report = json.loads(evaluation['report'])
                if digest({k: v for k, v in report.items() if k != 'contentSha256'}) != report.get('contentSha256') or report['contentSha256'] != evaluation['report_sha']:
                    raise ProspectiveError('前瞻评价内容哈希不匹配')
                if evaluation['previous_report_sha'] != previous or report.get('previousReportSha256') != previous:
                    raise ProspectiveError('前瞻评价追加链不一致')
                if not record or report.get('registrationId') != record['id'] or report.get('protocolSha256') != record['protocolSha256']:
                    raise ProspectiveError('评价与前瞻登记不一致')
                if digest(report['marketSnapshot']) != report.get('marketSnapshotSha256'):
                    raise ProspectiveError('选用行情快照哈希不匹配')
                reports.append(report)
                previous = report['contentSha256']
        except (KeyError, ValueError, TypeError) as exc:
            raise ProspectiveError('前瞻留档格式不正确') from exc
        return record, reports

    def _verify(self, record):
        current = load_protocol(self.base)
        if current != record['protocol'] or dependencies(self.base) != record['inputHashes']:
            raise ProspectiveError('已登记后协议、规则或依赖发生变化；拒绝沿用该前瞻计划')
        if instant(record['registeredAtUtc']) >= instant(record['protocol']['registrationDeadlineAt']):
            raise ProspectiveError('登记时间不早于首个窗口，不能作为有效前瞻')

    def status(self):
        record, reports = self._read()
        errors = []
        now = self._now()
        try:
            if record:
                self._verify(record)
            protocol = record['protocol'] if record else load_protocol(self.base)
        except ProspectiveError as exc:
            errors.append(str(exc))
            protocol = record['protocol'] if record else {}
        before = bool(protocol) and now < instant(protocol['registrationDeadlineAt'])
        status = 'integrity_error' if errors else ('registered' if record else 'unregistered' if before else 'registration_closed')
        return {'schemaVersion': 1, 'version': VERSION, 'status': status, 'nowUtc': now.isoformat(),
                'canRegister': not record and before and not errors, 'canEvaluate': bool(record) and not errors,
                'integrityErrors': errors, 'protocol': protocol,
                'registration': {k: record[k] for k in ('id', 'registeredAtUtc', 'protocolSha256', 'inputHashes', 'scheduleSha256')} if record else None,
                'schedule': record['schedule'] if record else [], 'evaluationCount': len(reports),
                'evaluationHistory': [{k: report[k] for k in ('id', 'createdAtUtc', 'asOfUtc', 'marketGeneration', 'marketSnapshotSha256')} for report in reports],
                'latestReport': reports[-1] if reports else None, 'marketValidated': False}

    def register(self):
        with self.lock:
            existing, _ = self._read()
            if existing:
                self._verify(existing)
                return {'created': False, 'status': self.status()}
            protocol = load_protocol(self.base)
            deadline = instant(protocol['registrationDeadlineAt'])
            if self._now() >= deadline:
                raise ProspectiveError('首个信号窗口已经开始，不能补登记为前瞻')
            hashes = dependencies(self.base)
            schedule = make_schedule(protocol)
            if len(schedule) != 95 or hashes != dependencies(self.base):
                raise ProspectiveError('登记计划或依赖在构建期间发生变化')
            registered = self._now()
            if registered >= deadline:
                raise ProspectiveError('构建期间已到登记截止时间，未保存前瞻计划')
            record = {'id': str(uuid.uuid4()), 'registeredAtUtc': registered.isoformat(),
                      'protocol': protocol, 'protocolSha256': digest(protocol), 'inputHashes': hashes,
                      'schedule': schedule, 'scheduleSha256': digest(schedule),
                      'allDatesIncluded': True, 'marketReadDuringRegistration': False}
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT 1 FROM prospective_registrations').fetchone():
                    raise ProspectiveError('另一请求刚完成登记，请刷新查看原登记')
                registered = self._now()
                if registered >= deadline:
                    raise ProspectiveError('写入时已到登记截止时间，未保存前瞻计划')
                record['registeredAtUtc'] = registered.isoformat()
                db.execute('INSERT INTO prospective_registrations VALUES (?,1,?,?,?)',
                           (record['id'], registered.isoformat(), encode(record), digest(record)))
            return {'created': True, 'status': self.status()}

    def evaluate(self):
        with self.lock:
            record, reports = self._read()
            if not record:
                raise ProspectiveError('请先在截止时间之前显式登记完整计划')
            self._verify(record)
            try:
                state, candles, gaps = self.market._snapshot(SYMBOL)
            except Exception as exc:
                raise ProspectiveError(f'无法读取独立行情缓存：{type(exc).__name__}', 503) from exc
            asof = self._now()
            if asof < instant(record['registeredAtUtc']):
                raise ProspectiveError('系统时间早于登记时间，拒绝倒退复核')
            if reports and asof < instant(reports[-1]['asOfUtc']):
                raise ProspectiveError('系统时间早于上一份评价，拒绝倒退复核')
            low = int(day_start(START).timestamp() * 1000)
            high = int(instant(record['protocol']['observationEndExclusiveAt']).timestamp() * 1000)
            now_ms = int(asof.timestamp() * 1000)
            selected = {key: deepcopy(value) for key, value in candles.items() if low <= key < high and key < now_ms}
            selected_gaps = {key: deepcopy(value) for key, value in gaps.items() if low <= key < high and key < now_ms}
            snapshot = {'asOfUtc': asof.isoformat(), 'state': deepcopy(state),
                        'requestedRange': {'start': START.isoformat(), 'end': '2027-01-29'},
                        'candles': [selected[key] for key in sorted(selected)],
                        'gaps': [{'open_ms': key, 'reasons': selected_gaps[key]} for key in sorted(selected_gaps)],
                        'excludedFutureRows': sum(low <= key < high and key >= now_ms for key in candles)}
            daily = []
            for day in record['schedule']:
                daily.append({**deepcopy(day), 'horizons': {str(h): evaluate_window(day['date'], h, selected, selected_gaps, asof) for h in HORIZONS}})
            created = self._now()
            if created < asof:
                raise ProspectiveError('系统时间在复核期间倒退，未保存评价')
            report = {'id': str(uuid.uuid4()), 'createdAtUtc': created.isoformat(), 'asOfUtc': asof.isoformat(),
                      'registrationId': record['id'], 'protocolSha256': record['protocolSha256'],
                      'marketGeneration': state.get('generation', 0), 'marketAsOfUtc': state.get('last_success'),
                      'marketSnapshot': snapshot, 'marketSnapshotSha256': digest(snapshot),
                      'daily': daily, 'summary': summarize(daily, record['protocol']),
                      'outcomeCounts': {str(h): {kind: sum(d['horizons'][str(h)]['status'] == kind for d in daily) for kind in ('pending', 'missing', 'scored')} for h in HORIZONS},
                      'previousReportSha256': reports[-1]['contentSha256'] if reports else None,
                      'marketValidated': False}
            report['contentSha256'] = digest(report)
            self._verify(record)
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                latest = db.execute('SELECT report_sha FROM prospective_evaluations ORDER BY rowid DESC LIMIT 1').fetchone()
                if (latest['report_sha'] if latest else None) != report['previousReportSha256']:
                    raise ProspectiveError('另一评价刚被追加，请重试以保持完整留痕链')
                if self._now() < created:
                    raise ProspectiveError('系统时间在评价写入前倒退，未保存评价')
                db.execute('INSERT INTO prospective_evaluations VALUES (?,?,?,?,?,?,?)',
                           (report['id'], record['id'], created.isoformat(), asof.isoformat(), encode(report), report['contentSha256'], report['previousReportSha256']))
            return {'created': True, 'report': report, 'status': self.status()}

    def export(self):
        record, reports = self._read()
        status = self.status()
        return {'schemaVersion': 1, 'version': VERSION, 'exportedAtUtc': self._now().isoformat(),
                'protocol': status['protocol'], 'registration': record, 'schedule': record['schedule'] if record else [],
                'evaluations': reports, 'integrityErrors': status['integrityErrors'], 'marketValidated': False}
