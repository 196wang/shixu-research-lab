"""Read-only chart aggregation and an isolated, manually refreshed Binance 4h cache.

The daily MarketFeed and all study/ledger inputs remain unchanged. The caller
serializes calendar calls with CALC_LOCK. One process owns each data directory.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from urllib.parse import parse_qs
import uuid

from calendar_adapter import LOCAL, POLICY, calculate_calendar
from historical_chart import element_labels
from market_feed import (BASE_URL, DAY_MS, OFFSET_MS, START_DATE, SYMBOLS,
                         MarketFeedError, default_transport, day_ms, encode,
                         ms_day, ms_iso, transitions_for, validate_symbol)

VERSION = '0.19.0'
INTERVALS = ('4h', '1d', '1w', '1M')
FOUR_HOURS = 14_400_000
MAX_PAGE = 1000
MAX_PAGES = 20
MAX_BODY = 4_000_000
PRICE_KEYS = ('open', 'high', 'low', 'close', 'volume')
DOCS = 'https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market'


def validate_interval(interval):
    if interval not in INTERVALS:
        raise MarketFeedError('周期仅支持4h、1d、1w、1M', 400)
    return interval


def parse_bars_query(query):
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise MarketFeedError('多周期行情参数格式无效', 400) from exc
    if set(values) != {'symbol', 'interval'} or any(len(v) != 1 for v in values.values()):
        raise MarketFeedError('多周期行情必须且只能包含一个symbol和一个interval', 400)
    result = {k: v[0] for k, v in values.items()}
    validate_symbol(result['symbol'])
    validate_interval(result['interval'])
    return result


def numeric(value, positive=True):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or len(str(value)) > 200:
        raise ValueError('invalid_numeric_value')
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number.adjusted()) > 100 or not math.isfinite(float(number)):
            raise ValueError('invalid_numeric_value')
        if (number <= 0 if positive else number < 0):
            raise ValueError('invalid_numeric_value')
        return number
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('invalid_numeric_value') from exc


def normalize_four_hour(raw, server_ms, lower_ms, upper_ms):
    if not isinstance(raw, list) or len(raw) != 12:
        raise ValueError('invalid_kline_shape')
    opened, closed = raw[0], raw[6]
    if type(opened) is not int or type(closed) is not int:
        raise ValueError('invalid_millisecond_timestamps')
    if (opened + OFFSET_MS) % FOUR_HOURS or closed != opened + FOUR_HOURS - 1:
        raise ValueError('wrong_UTC8_four_hour_boundary')
    if not lower_ms <= opened <= upper_ms or opened > server_ms:
        raise ValueError('outside_requested_range')
    values = {key: numeric(raw[i], key != 'volume') for key, i in zip(PRICE_KEYS, (1, 2, 3, 4, 5))}
    if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
        raise ValueError('invalid_ohlc_order')
    if type(raw[8]) is not int or raw[8] < 0:
        raise ValueError('invalid_trade_count')
    return {'open_ms': opened, 'close_ms': closed, **{k: str(v) for k, v in values.items()},
            'is_closed': int(opened + FOUR_HOURS <= server_ms), 'conflicted': 0}


def identity_values(bar):
    return tuple(Decimal(bar[k]) for k in PRICE_KEYS)


def local_at(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone(LOCAL)


@lru_cache(maxsize=10000)
def date_pillars(day):
    return {k: v for k, v in calculate_calendar(day + 'T00:00:00+08:00')['pillars'].items()
            if k in ('year', 'month', 'day')}


def labels_at(opened, by_day):
    """One midnight calculation per date; apply exact Jie changes within a 4h day."""
    at = local_at(opened)
    pillars = dict(date_pillars(at.date().isoformat()))
    for change in by_day.get(at.date().isoformat(), []):
        if datetime.fromisoformat(change['atLocal']) <= at:
            pillars.update({k: change['afterPillars'][k] for k in ('year', 'month')})
    return pillars


def validated_row(row, symbol, opened, duration, server_ms, gaps):
    errors = list(gaps) if isinstance(gaps, list) and all(isinstance(x, str) for x in gaps) else ['invalid_gap_metadata']
    if row is None:
        errors.append('missing_bar')
    else:
        if row.get('symbol') != symbol:
            errors.append('wrong_symbol')
        if row.get('open_ms') != opened or row.get('close_ms') != opened + duration - 1:
            errors.append('wrong_period_boundary')
        if row.get('conflicted'):
            errors.append('source_conflict')
        try:
            values = {k: numeric(row.get(k), k != 'volume') for k in PRICE_KEYS}
            if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
                raise ValueError('invalid_ohlc_order')
        except ValueError as exc:
            errors.append(str(exc))
        if not row.get('is_closed'):
            errors.append('not_closed')
    if opened + duration > server_ms:
        errors.append('not_closed')
    return sorted(set(errors))


def next_month(day):
    return date(day.year + (day.month == 12), 1 if day.month == 12 else day.month + 1, 1)


def period_for(day, interval):
    if interval == '1w':
        first = day - timedelta(days=day.weekday())
        return first, first + timedelta(days=7)
    if interval == '1M':
        first = day.replace(day=1)
        return first, next_month(first)
    return day, day + timedelta(days=1)


def chart_row(symbol, interval, opened, end, members, reasons, server_ms, by_day, expected):
    reasons = sorted(set(reasons))
    complete = not reasons
    pillars = labels_at(opened, by_day)
    numbers = {k: None for k in PRICE_KEYS}
    if complete:
        numbers = {'open': float(numeric(members[0]['open'])), 'close': float(numeric(members[-1]['close'])),
                   'high': float(max(numeric(r['high']) for r in members)),
                   'low': float(min(numeric(r['low']) for r in members)),
                   'volume': float(sum((numeric(r['volume'], False) for r in members), Decimal(0)))}
    at = local_at(opened)
    return {'id': f'{symbol}:{interval}:{opened}', 'date': at.date().isoformat(), 'atLocal': at.isoformat(),
            'openTimeMs': opened, 'closeTimeMs': end - 1, 'openTimeUtc': ms_iso(opened),
            'closeTimeUtcExclusive': ms_iso(end), **numbers, 'complete': complete, 'isClosed': end <= server_ms,
            'errors': reasons, 'quality': {'status': 'complete' if complete else 'pending' if end > server_ms else 'missing',
                                         'expectedDays': expected if interval != '4h' else None,
                                         'validDays': len(members) if interval != '4h' else None,
                                         'expectedBars': expected, 'validBars': len(members)},
            'pillars': pillars, 'elements': element_labels(pillars),
            'sourceRefs': sorted({f'request:{r.get("request_id")};sha256:{r.get("body_sha256")}' for r in members})}


def build_bars(snapshot, symbol, interval):
    """Pure conversion: full regular time grid and strict complete-period aggregates."""
    validate_symbol(symbol)
    validate_interval(interval)
    state, candles, gaps = snapshot
    if state.get('symbol') != symbol:
        raise MarketFeedError('行情快照交易对不一致')
    available = bool(state.get('range_start'))
    rows, transitions = [], []
    if available:
        server_ms = state.get('server_ms')
        if type(server_ms) is not int:
            raise MarketFeedError('行情快照缺少服务器观察时间')
        first, last = date.fromisoformat(state['range_start']), date.fromisoformat(state['range_end'])
        if not START_DATE <= first <= last <= date(2099, 12, 31):
            raise MarketFeedError('行情快照日期范围无效')
        first_period = period_for(first, interval)[0] if interval != '4h' else first
        transitions = deepcopy(transitions_for(first_period.isoformat(), last.isoformat()))
        by_day = {}
        for item in transitions:
            by_day.setdefault(item['atLocal'][:10], []).append(item)
        if interval == '4h':
            end_open = state['range_end_ms']
            if not day_ms(first) <= end_open <= server_ms or (end_open + OFFSET_MS) % FOUR_HOURS:
                raise MarketFeedError('4小时缓存范围无效')
            for opened in range(day_ms(first), end_open + 1, FOUR_HOURS):
                row = candles.get(opened)
                errors = validated_row(row, symbol, opened, FOUR_HOURS, server_ms, gaps.get(opened, []))
                rows.append(chart_row(symbol, interval, opened, opened + FOUR_HOURS,
                                      [row] if not errors else [], errors, server_ms, by_day, 1))
        else:
            current = first_period
            while current <= last:
                _, finish = period_for(current, interval)
                members, reasons = [], []
                count = (finish - current).days
                for i in range(count):
                    day = current + timedelta(days=i)
                    opened = day_ms(day)
                    row = candles.get(opened)
                    errors = validated_row(row, symbol, opened, DAY_MS, server_ms, gaps.get(opened, []))
                    if day < first or day > last:
                        errors.append('outside_cache_range')
                    if errors:
                        reasons.extend(errors)
                    else:
                        members.append(row)
                rows.append(chart_row(symbol, interval, day_ms(current), day_ms(finish), members,
                                      reasons, server_ms, by_day, count))
                current = finish
    coverage = {'total': len(rows), 'complete': sum(r['complete'] for r in rows),
                'missing': sum(r['quality']['status'] == 'missing' for r in rows),
                'pending': sum(r['quality']['status'] == 'pending' for r in rows),
                'conflicts': sum('source_conflict' in r['errors'] for r in rows)}
    four = interval == '4h'
    aggregation = 'binance_native_4h' if four else 'binance_native_1d' if interval == '1d' else 'complete_daily_bars'
    return {'schemaVersion': 1, 'version': VERSION, 'available': available, 'symbol': symbol,
            'interval': interval, 'timezone': 'UTC+8', 'venue': 'Binance', 'marketType': 'spot',
            'range': {'start': rows[0]['date'] if rows else None, 'end': rows[-1]['date'] if rows else None,
                      'startAtLocal': rows[0]['atLocal'] if rows else None,
                      'endExclusiveAtLocal': local_at(rows[-1]['closeTimeMs'] + 1).isoformat() if rows else None},
            'coverage': coverage, 'rows': rows, 'transitions': transitions,
            'source': {'title': 'Binance公开4小时线 · 独立缓存' if four else 'Binance公开日线 · 完整周期聚合',
                       'mode': 'dynamic_cache', 'aggregation': aggregation, 'sourceInterval': '4h' if four else '1d',
                       'asOf': state.get('range_end'), 'generation': state.get('generation', 0),
                       'generationScope': 'four_hour_cache' if four else 'daily_cache',
                       'lastSuccessAtUtc': state.get('last_success'), 'lastAttemptAtUtc': state.get('last_attempt'),
                       'lastError': state.get('last_error'), 'serverTimeUtc': ms_iso(state['server_ms']) if state.get('server_ms') else None,
                       'volumeUnit': symbol.removesuffix('USDT'), 'priceUnit': 'USDT',
                       'files': ['market_multiframe.sqlite3'] if four else ['market.sqlite3'],
                       'endpoint': BASE_URL + '/api/v3/klines', 'timeZoneParameter': '8', 'docs': DOCS,
                       'closedOnlyPlotted': True, 'rawResponsesRetained': True,
                       'weeklyBoundary': 'Monday 00:00+08:00', 'monthlyBoundary': 'Gregorian month day 1 00:00+08:00'},
            'volumeUnit': symbol.removesuffix('USDT'), 'calendarPolicy': POLICY, 'labelAt': 'barOpen',
            'liveMarketData': True, 'marketValidated': False, 'marketDirection': None,
            'limits': ['手动更新的公开现货行情缓存，不是实时推送。', '未收盘、缺口和冲突周期不绘制价格，不补造未来K线。',
                       '周线按北京时间周一、月线按公历月聚合；不是节气月。',
                       '年月日干支属于每根开盘瞬间；4小时柱不等同于一个传统时辰。',
                       '周期切换不改变原日级候选、预测评分或冻结研究结果。']}


class MarketMultiframe:
    def __init__(self, data_dir, market, transport=None, clock=None):
        self.path = Path(data_dir) / 'market_multiframe.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.market = market
        self.transport = transport or default_transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.Lock()
        self.thread = None
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS mf_state(symbol TEXT PRIMARY KEY,data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS mf_jobs(id TEXT PRIMARY KEY,data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS mf_bars(symbol TEXT NOT NULL,open_ms INTEGER NOT NULL,data TEXT NOT NULL,PRIMARY KEY(symbol,open_ms));
              CREATE TABLE IF NOT EXISTS mf_gaps(symbol TEXT NOT NULL,open_ms INTEGER NOT NULL,reasons TEXT NOT NULL,PRIMARY KEY(symbol,open_ms));
              CREATE TABLE IF NOT EXISTS mf_requests(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,symbol TEXT NOT NULL,path TEXT NOT NULL,
                params TEXT NOT NULL,requested TEXT NOT NULL,fetched TEXT NOT NULL,status INTEGER,headers TEXT NOT NULL,
                body BLOB NOT NULL,sha256 TEXT NOT NULL,error TEXT);
              CREATE TABLE IF NOT EXISTS mf_issues(id TEXT PRIMARY KEY,job_id TEXT NOT NULL,symbol TEXT NOT NULL,kind TEXT NOT NULL,evidence TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS mf_config(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            ''')
            for symbol in SYMBOLS:
                db.execute('INSERT OR IGNORE INTO mf_state VALUES (?,?)', (symbol, encode(self.empty_state(symbol))))
            for item in db.execute('SELECT id,data FROM mf_jobs').fetchall():
                job = json.loads(item['data'])
                if job['status'] in ('queued', 'running'):
                    job.update(status='failed', phase='interrupted', finishedAtUtc=self._now(), lastError='上次关闭时4小时行情更新中断，可手动重试')
                    db.execute('UPDATE mf_jobs SET data=? WHERE id=?', (encode(job), item['id']))
                    state = self._state(db, job['symbol'])
                    state.update(status='failed', last_error=job['lastError'])
                    self._save_state(db, state)

    @staticmethod
    def empty_state(symbol):
        return {'symbol': symbol, 'status': 'empty', 'range_start': None, 'range_end': None,
                'range_end_ms': None, 'server_ms': None, 'generation': 0,
                'last_success': None, 'last_attempt': None, 'last_error': None}

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _now(self):
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketFeedError('行情时钟必须有明确时区')
        return value.astimezone(timezone.utc).isoformat()

    def _state(self, db, symbol):
        return json.loads(db.execute('SELECT data FROM mf_state WHERE symbol=?', (symbol,)).fetchone()['data'])

    def _save_state(self, db, state):
        db.execute('UPDATE mf_state SET data=? WHERE symbol=?', (encode(state), state['symbol']))

    def _snapshot(self, symbol):
        validate_symbol(symbol)
        with self.connect() as db:
            db.execute('BEGIN')
            state = self._state(db, symbol)
            bars = {r['open_ms']: json.loads(r['data']) for r in db.execute('SELECT open_ms,data FROM mf_bars WHERE symbol=?', (symbol,))}
            gaps = {r['open_ms']: json.loads(r['reasons']) for r in db.execute('SELECT open_ms,reasons FROM mf_gaps WHERE symbol=?', (symbol,))}
        return state, bars, gaps

    def _job(self, identity=None):
        with self.connect() as db:
            row = db.execute('SELECT data FROM mf_jobs WHERE id=?', (identity,)).fetchone() if identity else db.execute('SELECT data FROM mf_jobs ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(row['data']) if row else None

    def _update_job(self, identity, **changes):
        with self.connect() as db:
            value = json.loads(db.execute('SELECT data FROM mf_jobs WHERE id=?', (identity,)).fetchone()['data'])
            value.update(changes)
            db.execute('UPDATE mf_jobs SET data=? WHERE id=?', (encode(value), identity))

    def _cooldown(self, db):
        row = db.execute("SELECT value FROM mf_config WHERE key='cooldown'").fetchone()
        return row['value'] if row else None

    def _begin(self, symbol):
        validate_symbol(symbol)
        if not self.lock.acquire(blocking=False):
            raise MarketFeedError('已有4小时行情更新任务正在运行', 409)
        try:
            with self.connect() as db:
                cooldown = self._cooldown(db)
                if cooldown and datetime.fromisoformat(cooldown) > datetime.fromisoformat(self._now()):
                    raise MarketFeedError('币安限流退避中，请稍后手动更新', 429, cooldown)
                job = {'id': str(uuid.uuid4()), 'symbol': symbol, 'interval': '4h', 'status': 'queued', 'phase': 'waiting',
                       'startedAtUtc': self._now(), 'finishedAtUtc': None, 'lastError': None, 'requests': 0,
                       'cooldownUntilUtc': None, 'serverTimeUtc': None, 'summary': None}
                db.execute('INSERT INTO mf_jobs VALUES (?,?)', (job['id'], encode(job)))
                state = self._state(db, symbol)
                state.update(status='running', last_attempt=job['startedAtUtc'], last_error=None)
                self._save_state(db, state)
            return job['id']
        except Exception:
            self.lock.release()
            raise

    def start_refresh(self, symbol, interval='4h'):
        validate_symbol(symbol)
        validate_interval(interval)
        if interval != '4h':
            return self.market.start_refresh(symbol)
        identity = self._begin(symbol)
        self.thread = threading.Thread(target=self._run, args=(identity, symbol), daemon=True)
        self.thread.start()
        return {'accepted': True, 'job': self._job(identity)}

    def refresh(self, symbol, interval='4h'):
        validate_symbol(symbol)
        validate_interval(interval)
        if interval != '4h':
            return self.market.refresh(symbol)
        identity = self._begin(symbol)
        self._run(identity, symbol)
        return {'accepted': True, 'job': self._job(identity)}

    def _fetch(self, identity, symbol, path, params):
        started, request_id = self._now(), str(uuid.uuid4())
        status, headers, body, failure = None, {}, b'', None
        try:
            response = self.transport(path, dict(params))
            status, headers, body = response['status'], response.get('headers', {}), response['body']
            if not isinstance(body, bytes):
                raise MarketFeedError('行情transport必须返回原始bytes')
        except Exception as exc:
            failure, body = str(exc), b''
        sha = hashlib.sha256(body).hexdigest()
        with self.connect() as db:
            db.execute('INSERT INTO mf_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, identity, symbol, path, encode(params), started, self._now(), status, encode(headers), body, sha, failure))
        job = self._job(identity)
        self._update_job(identity, requests=job['requests'] + 1)
        if failure:
            raise MarketFeedError(failure)
        if status in (429, 418):
            values = {str(k).lower(): v for k, v in headers.items()}
            try:
                seconds = max(1, int(values.get('retry-after', 180 if status == 418 else 60)))
            except (ValueError, TypeError):
                seconds = 180 if status == 418 else 60
            try:
                cooldown = (datetime.fromisoformat(self._now()) + timedelta(seconds=seconds)).isoformat()
            except OverflowError:
                cooldown = datetime.max.replace(tzinfo=timezone.utc).isoformat()
            with self.connect() as db:
                db.execute("INSERT INTO mf_config VALUES ('cooldown',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (cooldown,))
            self._update_job(identity, cooldownUntilUtc=cooldown)
            raise MarketFeedError(f'币安HTTP {status}，4小时缓存保留，不自动重试', 429, cooldown)
        if status != 200 or len(body) > MAX_BODY:
            raise MarketFeedError(f'币安行情响应无效（HTTP {status} 或过大），旧缓存保留')
        try:
            return json.loads(body.decode('utf-8')), request_id, sha
        except (ValueError, UnicodeDecodeError) as exc:
            raise MarketFeedError('币安返回无效JSON，旧缓存保留') from exc

    def _ranges(self, state, bars, end_open, first):
        if not state['range_start']:
            return [(first, end_open)]
        recent = max(first, min(state['range_end_ms'], end_open) - 2 * DAY_MS)
        ranges = [(recent, end_open)]
        repairs = []
        for opened in range(first, recent, FOUR_HOURS):
            row = bars.get(opened)
            if row and (row.get('is_closed') or row.get('conflicted')):
                continue
            if repairs and opened == repairs[-1][1] + FOUR_HOURS and opened - repairs[-1][0] < MAX_PAGE * FOUR_HOURS:
                repairs[-1] = (repairs[-1][0], opened)
            elif len(repairs) < 4:
                repairs.append((opened, opened))
            else:
                break
        return ranges + repairs

    def _run(self, identity, symbol):
        try:
            self._update_job(identity, status='running', phase='server_time')
            payload, _, _ = self._fetch(identity, symbol, '/api/v3/time', {})
            server_ms = payload.get('serverTime') if isinstance(payload, dict) else None
            if type(server_ms) is not int or not day_ms(START_DATE) <= server_ms < day_ms(date(2100, 1, 1)):
                raise MarketFeedError('币安服务器时间无效')
            state, bars, _ = self._snapshot(symbol)
            if state['server_ms'] is not None and server_ms < state['server_ms']:
                raise MarketFeedError('币安服务器时间回退，旧缓存保留')
            today = ms_day(server_ms)
            first = day_ms(date.fromisoformat(state['range_start']) if state['range_start'] else max(START_DATE, today - timedelta(days=365)))
            end_open = server_ms - (server_ms + OFFSET_MS) % FOUR_HOURS
            ranges = self._ranges(state, bars, end_open, first)
            if sum((end - start) // (MAX_PAGE * FOUR_HOURS) + 1 for start, end in ranges) > MAX_PAGES:
                raise MarketFeedError('本次4小时行情分页超过有界限制，旧缓存保留')
            self._update_job(identity, phase='four_hour_klines', serverTimeUtc=ms_iso(server_ms))
            staged, issues, invalid = {}, [], {}
            for start, end in ranges:
                cursor = start
                while cursor <= end:
                    upper = min(cursor + MAX_PAGE * FOUR_HOURS - 1, end + FOUR_HOURS - 1, server_ms)
                    params = {'symbol': symbol, 'interval': '4h', 'timeZone': '8', 'startTime': cursor, 'endTime': upper, 'limit': MAX_PAGE}
                    payload, request_id, sha = self._fetch(identity, symbol, '/api/v3/klines', params)
                    if not isinstance(payload, list) or len(payload) > MAX_PAGE:
                        raise MarketFeedError('币安4小时K线格式或数量无效，旧缓存保留')
                    for raw in payload:
                        try:
                            row = normalize_four_hour(raw, server_ms, cursor, upper)
                        except (ValueError, TypeError) as exc:
                            opened = raw[0] if isinstance(raw, list) and raw and type(raw[0]) is int else None
                            issues.append(('invalid', {'raw': raw, 'reason': str(exc), 'requestId': request_id}))
                            if opened is not None and not (opened + OFFSET_MS) % FOUR_HOURS:
                                invalid.setdefault(opened, []).append(str(exc))
                            continue
                        row.update(symbol=symbol, request_id=request_id, body_sha256=sha)
                        opened = row['open_ms']
                        if opened in staged and identity_values(staged[opened]) != identity_values(row):
                            staged[opened]['conflicted'] = 1
                            issues.append(('duplicate_conflict', {'old': staged[opened], 'candidate': row}))
                        else:
                            staged.setdefault(opened, row)
                    cursor += MAX_PAGE * FOUR_HOURS
            self._commit(identity, symbol, server_ms, first, end_open, staged, issues, invalid)
        except Exception as exc:
            message = str(exc)
            status = 'rate_limited' if isinstance(exc, MarketFeedError) and exc.status_code == 429 else 'failed'
            self._update_job(identity, status=status, phase='finished', finishedAtUtc=self._now(), lastError=message)
            with self.connect() as db:
                state = self._state(db, symbol)
                state.update(status=status, last_error=message)
                self._save_state(db, state)
        finally:
            self.lock.release()

    def _commit(self, job_id, symbol, server_ms, first, end_open, staged, issues, invalid):
        finished = self._now()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for opened, row in staged.items():
                found = db.execute('SELECT data FROM mf_bars WHERE symbol=? AND open_ms=?', (symbol, opened)).fetchone()
                old = json.loads(found['data']) if found else None
                if old and old['is_closed'] and identity_values(old) != identity_values(row):
                    issues.append(('closed_revision_conflict', {'old': old, 'candidate': row}))
                    old['conflicted'] = 1
                    row = old
                elif old:
                    row['conflicted'] = max(old['conflicted'], row['conflicted'])
                    row['is_closed'] = max(old['is_closed'], row['is_closed'])
                row['updated'] = finished
                db.execute('INSERT INTO mf_bars VALUES (?,?,?) ON CONFLICT(symbol,open_ms) DO UPDATE SET data=excluded.data', (symbol, opened, encode(row)))
            for kind, evidence in issues:
                db.execute('INSERT INTO mf_issues VALUES (?,?,?,?,?)', (str(uuid.uuid4()), job_id, symbol, kind, encode(evidence)))
            bars = {r['open_ms']: json.loads(r['data']) for r in db.execute('SELECT open_ms,data FROM mf_bars WHERE symbol=?', (symbol,))}
            db.execute('DELETE FROM mf_gaps WHERE symbol=?', (symbol,))
            for opened in range(first, end_open + 1, FOUR_HOURS):
                row = bars.get(opened)
                errors = validated_row(row, symbol, opened, FOUR_HOURS, server_ms, [])
                if row is None:
                    errors = sorted(set(errors + invalid.get(opened, [])))
                if errors:
                    db.execute('INSERT INTO mf_gaps VALUES (?,?,?)', (symbol, opened, encode(errors)))
            state = self._state(db, symbol)
            state.update(status='ready', range_start=ms_day(first).isoformat(), range_end=ms_day(end_open).isoformat(),
                         range_end_ms=end_open, server_ms=server_ms, last_success=finished, last_error=None, generation=state['generation'] + 1)
            self._save_state(db, state)
            job = json.loads(db.execute('SELECT data FROM mf_jobs WHERE id=?', (job_id,)).fetchone()['data'])
            job.update(status='succeeded', phase='finished', finishedAtUtc=finished,
                       summary={'stagedBars': len(staged), 'issueCount': len(issues), 'rangeStart': state['range_start'], 'rangeEnd': state['range_end']})
            db.execute('UPDATE mf_jobs SET data=? WHERE id=?', (encode(job), job_id))

    def bars(self, symbol, interval):
        validate_symbol(symbol)
        validate_interval(interval)
        snapshot = self._snapshot(symbol) if interval == '4h' else self.market._snapshot(symbol)
        return build_bars(snapshot, symbol, interval)

    def status(self):
        symbols = []
        for symbol in SYMBOLS:
            state, bars, gaps = self._snapshot(symbol)
            total = (state['range_end_ms'] - day_ms(date.fromisoformat(state['range_start']))) // FOUR_HOURS + 1 if state['range_start'] else 0
            complete = sum(bool(r['is_closed']) and not r['conflicted'] for r in bars.values())
            pending = sum('not_closed' in values for values in gaps.values())
            conflicts = sum('source_conflict' in values for values in gaps.values())
            symbols.append({'symbol': symbol, 'interval': '4h', 'available': bool(state['range_start']), 'status': state['status'],
                            'range': {'start': state['range_start'], 'end': state['range_end']},
                            'coverage': {'total': total, 'complete': complete, 'pending': pending, 'missing': total - complete - pending,
                                         'conflicts': conflicts},
                            'lastSuccessAtUtc': state['last_success'], 'lastAttemptAtUtc': state['last_attempt'], 'lastError': state['last_error']})
        with self.connect() as db:
            cooldown = self._cooldown(db)
        return {'version': VERSION, 'manualOnly': True, 'fourHour': {'provider': 'Binance', 'interval': '4h', 'symbols': symbols,
                                                                  'job': self._job(), 'cooldownUntilUtc': cooldown},
                'daily': self.market.status(), 'frozenResearchChanged': False}
