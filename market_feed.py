"""Manual Binance public daily feed in a separate SQLite cache.

No account credentials, trading endpoints, scheduler, or frozen-study mutations.
The caller owns HTTP route security and calendar calculation locking.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import socket
import ssl
import sqlite3
import threading
from urllib import error, parse, request
import uuid

from calendar_adapter import LOCAL, POLICY, calculate_calendar, calculate_relations
from bazi_features import calculate_features
from historical_chart import build_transitions, element_labels

VERSION = '0.12.0'
BASE_URL = 'https://data-api.binance.vision'
SYMBOLS = ('BTCUSDT', 'ETHUSDT')
PROFILE_SYMBOLS = {'BTC-BZ-V01': 'BTCUSDT', 'ETH-BAZI-A': 'ETHUSDT', 'ETH-BAZI-B': 'ETHUSDT'}
START_DATE = date(2020, 1, 1)
DAY_MS = 86_400_000
OFFSET_MS = 28_800_000
MAX_PAGE_DAYS = 1000
MAX_PAGES = 40
MAX_REPAIR_PAGES = 8
MAX_BODY_BYTES = 4_000_000
DOCS = [
    'https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market',
    'https://developers.binance.com/en/docs/products/spot/faqs/market_data_only',
    'https://developers.binance.com/en/docs/products/spot/rest-api',
]


class MarketFeedError(RuntimeError):
    def __init__(self, message, status_code=503, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def utcnow():
    return datetime.now(timezone.utc)


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))


def validate_symbol(symbol):
    if symbol not in SYMBOLS:
        raise MarketFeedError('行情仅支持BTCUSDT和ETHUSDT', 400)
    return symbol


def validate_profile(symbol, profile):
    validate_symbol(symbol)
    if PROFILE_SYMBOLS.get(profile) != symbol:
        raise MarketFeedError('交易对与四柱版本不匹配', 400)


def parse_query(query, detail=False):
    try:
        args = parse.parse_qs(query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise MarketFeedError('行情查询格式无效', 400) from exc
    if any(len(value) != 1 for value in args.values()):
        raise MarketFeedError('行情查询参数不能重复', 400)
    if set(args) != ({'symbol', 'date', 'profile'} if detail else {'symbol'}):
        raise MarketFeedError('行情查询含缺失或多余参数', 400)
    result = {key: value[0] for key, value in args.items()}
    validate_symbol(result['symbol'])
    if detail:
        validate_profile(result['symbol'], result['profile'])
        parse_day(result['date'])
    return result


def parse_day(value):
    try:
        result = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MarketFeedError('日期必须为YYYY-MM-DD', 400) from exc
    if result.isoformat() != value or not START_DATE <= result <= date(2099, 12, 31):
        raise MarketFeedError('行情日期范围为2020–2099年', 400)
    return result


def day_ms(day):
    return int(datetime(day.year, day.month, day.day, tzinfo=LOCAL).timestamp() * 1000)


def ms_day(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).astimezone(LOCAL).date()


def ms_iso(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


def safe_network_error(exc):
    """Expose failure class/reason without logging proxy settings or credentials."""
    reason = exc.reason if isinstance(exc, error.URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        kind = 'timeout'
    elif isinstance(reason, ssl.SSLError):
        kind = 'TLS'
    elif isinstance(reason, socket.gaierror):
        kind = 'DNS'
    elif isinstance(reason, (ConnectionError, OSError)):
        kind = 'connection'
    else:
        kind = 'network'
    message = ' '.join(str(reason).split())
    # Exception messages can occasionally embed proxy URLs. Keep only their
    # hostname; never include user-info, path/query, or configuration contents.
    def redact_url(match):
        try:
            parsed = parse.urlsplit(match.group(0))
            return parsed.scheme + '://' + (parsed.hostname or '[host]') + '/[redacted]'
        except ValueError:
            return '[redacted-url]'
    message = re.sub(r'https?://[^\s]+', redact_url, message, flags=re.IGNORECASE)
    message = re.sub(r'(?i)(token|authorization|password|passwd|api[_-]?key|secret)\s*[:=]\s*[^,;\s]+',
                     r'\1=[redacted]', message)
    errno = getattr(reason, 'errno', None)
    detail = f'{type(exc).__name__}/{type(reason).__name__}; {kind}'
    if isinstance(errno, int):
        detail += f'; errno={errno}'
    return f'币安行情网络请求失败（{detail}; timeout=20s）：{message[:350] or "无底层原因"}'


def default_transport(path, params):
    """Fixed-origin GET only. Never follows a redirect to another host."""
    if path not in ('/api/v3/time', '/api/v3/klines'):
        raise MarketFeedError('未允许的行情接口', 400)
    url = BASE_URL + path + ('?' + parse.urlencode(params) if params else '')
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = request.build_opener(NoRedirect)
    req = request.Request(url, method='GET', headers={'User-Agent': 'LocalResearchDesk/0.12', 'Accept': 'application/json'})
    try:
        with opener.open(req, timeout=20) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            return {'status': response.status, 'headers': dict(response.headers), 'body': body}
    except error.HTTPError as exc:
        return {'status': exc.code, 'headers': dict(exc.headers), 'body': exc.read(MAX_BODY_BYTES + 1)}
    except (OSError, error.URLError) as exc:
        raise MarketFeedError(safe_network_error(exc)) from exc


def number(value, field, positive):
    if value is None or isinstance(value, (bool, list, dict)):
        raise ValueError('invalid_' + field)
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not math.isfinite(float(result)) or (result <= 0 if positive else result < 0):
            raise ValueError('invalid_' + field)
        return str(result.normalize())
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('invalid_' + field) from exc


def normalize_bar(raw, server_ms, lower_ms, upper_ms):
    if not isinstance(raw, list) or len(raw) != 12:
        raise ValueError('invalid_kline_shape')
    opened, closed = raw[0], raw[6]
    if type(opened) is not int or type(closed) is not int:
        raise ValueError('timestamps_must_be_milliseconds_integers')
    if (opened + OFFSET_MS) % DAY_MS or closed != opened + DAY_MS - 1:
        raise ValueError('wrong_UTC8_daily_boundary')
    if not lower_ms <= opened <= upper_ms or opened > server_ms:
        raise ValueError('kline_outside_requested_range')
    values = {key: number(raw[index], key, key != 'volume')
              for key, index in (('open', 1), ('high', 2), ('low', 3), ('close', 4), ('volume', 5))}
    op, hi, lo, cl = (Decimal(values[key]) for key in ('open', 'high', 'low', 'close'))
    if not lo <= min(op, cl) <= max(op, cl) <= hi:
        raise ValueError('invalid_ohlc_order')
    if type(raw[8]) is not int or raw[8] < 0:
        raise ValueError('invalid_trade_count')
    return {'open_ms': opened, 'close_ms': closed, **values,
            'is_closed': int(closed < server_ms), 'conflicted': 0}


def price_identity(bar):
    return tuple(bar[key] for key in ('open', 'high', 'low', 'close', 'volume'))


@lru_cache(maxsize=8192)
def day_calendar(day):
    return calculate_calendar(day + 'T00:00:00+08:00')


@lru_cache(maxsize=24)
def transitions_for(start, end):
    return build_transitions(date.fromisoformat(start), date.fromisoformat(end))


class MarketFeed:
    def __init__(self, data_dir, transport=None, clock=None):
        self.path = Path(data_dir) / 'market.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.transport = transport or default_transport
        self.clock = clock or utcnow
        self.lock = threading.Lock()
        self.thread = None
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS feed_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS symbol_state(
                symbol TEXT PRIMARY KEY,status TEXT NOT NULL,last_attempt TEXT,last_success TEXT,
                last_error TEXT,server_ms INTEGER,range_start TEXT,range_end TEXT,generation INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS feed_jobs(
                id TEXT PRIMARY KEY,symbol TEXT NOT NULL,status TEXT NOT NULL,phase TEXT NOT NULL,
                started TEXT NOT NULL,finished TEXT,requests INTEGER NOT NULL DEFAULT 0,last_error TEXT,
                cooldown_until TEXT,server_ms INTEGER,summary TEXT);
              CREATE TABLE IF NOT EXISTS feed_requests(
                id TEXT PRIMARY KEY,job_id TEXT NOT NULL,symbol TEXT NOT NULL,path TEXT NOT NULL,
                params TEXT NOT NULL,requested TEXT NOT NULL,fetched TEXT NOT NULL,http_status INTEGER,
                headers TEXT NOT NULL,body BLOB NOT NULL,body_sha256 TEXT NOT NULL,error TEXT);
              CREATE TABLE IF NOT EXISTS candles(
                symbol TEXT NOT NULL,open_ms INTEGER NOT NULL,close_ms INTEGER NOT NULL,
                open TEXT NOT NULL,high TEXT NOT NULL,low TEXT NOT NULL,close TEXT NOT NULL,volume TEXT NOT NULL,
                is_closed INTEGER NOT NULL,conflicted INTEGER NOT NULL DEFAULT 0,request_id TEXT NOT NULL,
                updated TEXT NOT NULL,PRIMARY KEY(symbol,open_ms));
              CREATE TABLE IF NOT EXISTS candle_conflicts(
                id TEXT PRIMARY KEY,symbol TEXT NOT NULL,open_ms INTEGER NOT NULL,existing TEXT NOT NULL,
                candidate TEXT NOT NULL,request_id TEXT NOT NULL,created TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS feed_gaps(
                symbol TEXT NOT NULL,open_ms INTEGER NOT NULL,reasons TEXT NOT NULL,checked TEXT NOT NULL,
                PRIMARY KEY(symbol,open_ms));
              CREATE TABLE IF NOT EXISTS feed_invalid_rows(
                id TEXT PRIMARY KEY,job_id TEXT NOT NULL,symbol TEXT NOT NULL,request_id TEXT NOT NULL,
                open_ms INTEGER,error TEXT NOT NULL,raw TEXT NOT NULL);
            ''')
            for symbol in SYMBOLS:
                db.execute("INSERT OR IGNORE INTO symbol_state(symbol,status) VALUES (?, 'empty')", (symbol,))
            # One server owns a data directory. On process restart, daemon
            # workers no longer exist; keep their evidence and release the UI.
            interrupted = '上次应用关闭时行情更新未完成；旧缓存保留，可手动重试'
            db.execute("""UPDATE symbol_state SET status='failed',last_error=? WHERE symbol IN
                          (SELECT symbol FROM feed_jobs WHERE status IN ('queued','running'))""", (interrupted,))
            db.execute("""UPDATE feed_jobs SET status='failed',phase='interrupted',finished=?,last_error=?
                          WHERE status IN ('queued','running')""", (self._now().isoformat(), interrupted))

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
        return self.clock().astimezone(timezone.utc)

    def _cooldown(self, db):
        row = db.execute("SELECT value FROM feed_state WHERE key='cooldown_until'").fetchone()
        return row['value'] if row else None

    def _begin(self, symbol):
        validate_symbol(symbol)
        if not self.lock.acquire(blocking=False):
            raise MarketFeedError('已有行情更新任务正在运行', 409)
        try:
            identity, started = str(uuid.uuid4()), self._now().isoformat()
            with self.connect() as db:
                cooldown = self._cooldown(db)
                if cooldown and datetime.fromisoformat(cooldown) > self._now():
                    raise MarketFeedError('币安限流退避中，请在提示时间后手动重试', 429, cooldown)
                db.execute("INSERT INTO feed_jobs(id,symbol,status,phase,started) VALUES (?,?,'queued','waiting',?)", (identity, symbol, started))
                db.execute("UPDATE symbol_state SET status='running',last_attempt=?,last_error=NULL WHERE symbol=?", (started, symbol))
            return identity
        except Exception:
            self.lock.release()
            raise

    def start_refresh(self, symbol):
        identity = self._begin(symbol)
        self.thread = threading.Thread(target=self._run, args=(identity, symbol), daemon=True)
        self.thread.start()
        return {'accepted': True, 'job': self._job(identity)}

    def refresh(self, symbol):
        """Synchronous equivalent for an explicit local command; network errors are in job."""
        identity = self._begin(symbol)
        self._run(identity, symbol)
        return {'accepted': True, 'job': self._job(identity)}

    def _job(self, identity=None):
        with self.connect() as db:
            row = db.execute('SELECT * FROM feed_jobs WHERE id=?', (identity,)).fetchone() if identity else db.execute('SELECT * FROM feed_jobs ORDER BY rowid DESC LIMIT 1').fetchone()
        if not row:
            return None
        return {'id': row['id'], 'symbol': row['symbol'], 'status': row['status'], 'phase': row['phase'],
                'requests': row['requests'], 'startedAtUtc': row['started'], 'finishedAtUtc': row['finished'],
                'lastError': row['last_error'], 'cooldownUntilUtc': row['cooldown_until'],
                'serverTimeUtc': ms_iso(row['server_ms']) if row['server_ms'] is not None else None,
                'summary': json.loads(row['summary']) if row['summary'] else None}

    def _fetch(self, identity, symbol, path, params):
        requested, request_id = self._now().isoformat(), str(uuid.uuid4())
        error_message = None
        try:
            response = self.transport(path, dict(params))
            status, headers, body = response['status'], response.get('headers', {}), response['body']
            if not isinstance(body, bytes):
                raise MarketFeedError('行情transport必须返回原始bytes')
        except Exception as exc:
            status, headers, body = None, {}, b''
            error_message = str(exc)
        fetched = self._now().isoformat()
        sha = hashlib.sha256(body).hexdigest()
        with self.connect() as db:
            db.execute('INSERT INTO feed_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       (request_id, identity, symbol, path, encode(params), requested, fetched, status, encode(headers), body, sha, error_message))
            db.execute('UPDATE feed_jobs SET requests=requests+1 WHERE id=?', (identity,))
        if error_message:
            raise MarketFeedError(error_message)
        if status in (429, 418):
            lower_headers = {str(k).lower(): v for k, v in headers.items()}
            try:
                seconds = max(1, int(lower_headers.get('retry-after', 180 if status == 418 else 60)))
            except (ValueError, TypeError):
                seconds = 180 if status == 418 else 60
            try:
                cooldown = (self._now() + timedelta(seconds=seconds)).isoformat()
            except OverflowError:
                cooldown = (self._now() + timedelta(days=365)).isoformat()
            with self.connect() as db:
                db.execute("INSERT INTO feed_state VALUES ('cooldown_until',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (cooldown,))
                db.execute('UPDATE feed_jobs SET cooldown_until=? WHERE id=?', (cooldown, identity))
            raise MarketFeedError(f'币安HTTP {status}，已记录退避时间，不会自动重试', 429, cooldown)
        if status != 200:
            raise MarketFeedError(f'币安行情HTTP {status}，旧缓存保留')
        if len(body) > MAX_BODY_BYTES:
            raise MarketFeedError('行情响应超过允许大小，旧缓存保留')
        try:
            payload = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MarketFeedError('币安行情返回无效JSON，旧缓存保留') from exc
        return payload, request_id

    def _ranges(self, symbol, today_ms):
        first = day_ms(START_DATE)
        with self.connect() as db:
            db.execute('BEGIN')
            rows = [dict(r) for r in db.execute('SELECT open_ms,is_closed,conflicted FROM candles WHERE symbol=? ORDER BY open_ms', (symbol,))]
            state = db.execute('SELECT * FROM symbol_state WHERE symbol=?', (symbol,)).fetchone()
        if not rows or not state['range_end']:
            return [(first, today_ms + DAY_MS - 1)]
        recent_start = max(first, min(today_ms, rows[-1]['open_ms']) - 2 * DAY_MS)
        ranges = [(recent_start, today_ms + DAY_MS - 1)]
        accepted = {r['open_ms'] for r in rows if r['is_closed'] or r['conflicted']}
        old_end = min(day_ms(parse_day(state['range_end'])), recent_start - DAY_MS)
        gaps = [value for value in range(first, old_end + 1, DAY_MS) if value not in accepted]
        # Bounded, oldest-first contiguous repairs. Remaining gaps stay marked.
        repairs = []
        for value in gaps:
            if repairs and value == repairs[-1][1] + 1 and value - repairs[-1][0] < MAX_PAGE_DAYS * DAY_MS:
                repairs[-1] = (repairs[-1][0], value + DAY_MS - 1)
            elif len(repairs) < MAX_REPAIR_PAGES:
                repairs.append((value, value + DAY_MS - 1))
            else:
                break
        return ranges + repairs

    def _run(self, identity, symbol):
        try:
            with self.connect() as db:
                db.execute("UPDATE feed_jobs SET status='running',phase='server_time' WHERE id=?", (identity,))
            server, _ = self._fetch(identity, symbol, '/api/v3/time', {})
            server_ms = server.get('serverTime') if isinstance(server, dict) else None
            if type(server_ms) is not int or not day_ms(START_DATE) <= server_ms < day_ms(date(2100, 1, 1)):
                raise MarketFeedError('币安服务器时间无效或超出支持范围')
            with self.connect() as db:
                previous_ms = db.execute('SELECT server_ms FROM symbol_state WHERE symbol=?', (symbol,)).fetchone()['server_ms']
            if previous_ms is not None and server_ms < previous_ms:
                raise MarketFeedError('币安服务器时间早于上次成功观察，旧缓存保留')
            today = ms_day(server_ms)
            ranges = self._ranges(symbol, day_ms(today))
            page_count = sum((end - start) // (MAX_PAGE_DAYS * DAY_MS) + 1 for start, end in ranges)
            if page_count > MAX_PAGES:
                raise MarketFeedError('行情分页超过本次有界请求数量，旧缓存保留')
            with self.connect() as db:
                db.execute("UPDATE feed_jobs SET phase='daily_klines',server_ms=? WHERE id=?", (server_ms, identity))
            staged, invalid, duplicate_conflicts = {}, [], []
            for start, end in ranges:
                cursor = start
                while cursor <= min(end, server_ms):
                    upper = min(cursor + MAX_PAGE_DAYS * DAY_MS - 1, end, server_ms)
                    params = {'symbol': symbol, 'interval': '1d', 'timeZone': '8',
                              'startTime': cursor, 'endTime': upper, 'limit': MAX_PAGE_DAYS}
                    payload, request_id = self._fetch(identity, symbol, '/api/v3/klines', params)
                    if not isinstance(payload, list) or len(payload) > MAX_PAGE_DAYS:
                        raise MarketFeedError('币安K线列表格式或数量无效，旧缓存保留')
                    for raw in payload:
                        try:
                            normalized = normalize_bar(raw, server_ms, cursor, upper)
                        except (ValueError, TypeError) as exc:
                            opened = raw[0] if isinstance(raw, list) and raw and type(raw[0]) is int else None
                            invalid.append((request_id, opened, str(exc), raw))
                            continue
                        normalized['request_id'] = request_id
                        opened = normalized['open_ms']
                        if opened in staged and price_identity(staged[opened]) != price_identity(normalized):
                            duplicate_conflicts.append((opened, dict(staged[opened]), normalized, request_id))
                            staged[opened]['conflicted'] = 1
                        elif opened not in staged:
                            staged[opened] = normalized
                    # Advance the requested calendar interval, never compress
                    # missing bars or loop based on the last returned candle.
                    cursor += MAX_PAGE_DAYS * DAY_MS
            self._commit(identity, symbol, server_ms, staged, invalid, duplicate_conflicts)
        except Exception as exc:
            rate_limited = isinstance(exc, MarketFeedError) and exc.status_code == 429
            status = 'rate_limited' if rate_limited else 'failed'
            message = str(exc) if isinstance(exc, MarketFeedError) else f'行情处理失败：{type(exc).__name__}: {exc}'
            with self.connect() as db:
                db.execute('UPDATE feed_jobs SET status=?,phase=?,finished=?,last_error=? WHERE id=?',
                           (status, 'finished', self._now().isoformat(), message, identity))
                db.execute('UPDATE symbol_state SET status=?,last_error=? WHERE symbol=?', (status, message, symbol))
        finally:
            self.lock.release()

    def _commit(self, identity, symbol, server_ms, staged, invalid, duplicate_conflicts):
        finished, today = self._now().isoformat(), ms_day(server_ms)
        conflict_count, inserted, changed = 0, 0, 0
        with self.connect() as db:
            for request_id, opened, message, raw in invalid:
                db.execute('INSERT INTO feed_invalid_rows VALUES (?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), identity, symbol, request_id, opened, message, encode(raw)))
            for opened, previous, candidate, request_id in duplicate_conflicts:
                db.execute('INSERT INTO candle_conflicts VALUES (?,?,?,?,?,?,?)',
                           (str(uuid.uuid4()), symbol, opened, encode(previous), encode(candidate), request_id, finished))
                conflict_count += 1
            for opened, bar in staged.items():
                existing_row = db.execute('SELECT * FROM candles WHERE symbol=? AND open_ms=?', (symbol, opened)).fetchone()
                existing = dict(existing_row) if existing_row else None
                if existing and existing['is_closed'] and price_identity(existing) != price_identity(bar):
                    db.execute('INSERT INTO candle_conflicts VALUES (?,?,?,?,?,?,?)',
                               (str(uuid.uuid4()), symbol, opened, encode(existing), encode(bar), bar['request_id'], finished))
                    db.execute('UPDATE candles SET conflicted=1 WHERE symbol=? AND open_ms=?', (symbol, opened))
                    conflict_count += 1
                    continue
                if existing:
                    bar['conflicted'] = max(existing['conflicted'], bar['conflicted'])
                    # Once confirmed closed, a stale response cannot reopen it.
                    bar['is_closed'] = max(existing['is_closed'], bar['is_closed'])
                    changed += int(price_identity(existing) != price_identity(bar) or existing['is_closed'] != bar['is_closed'])
                else:
                    inserted += 1
                db.execute('''INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(symbol,open_ms) DO UPDATE SET close_ms=excluded.close_ms,open=excluded.open,
                  high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,
                  is_closed=excluded.is_closed,conflicted=excluded.conflicted,
                  request_id=excluded.request_id,updated=excluded.updated''',
                           (symbol, opened, bar['close_ms'], bar['open'], bar['high'], bar['low'], bar['close'],
                            bar['volume'], bar['is_closed'], bar['conflicted'], bar['request_id'], finished))
            db.execute('DELETE FROM feed_gaps WHERE symbol=?', (symbol,))
            stored = {r['open_ms']: dict(r) for r in db.execute('SELECT * FROM candles WHERE symbol=?', (symbol,))}
            invalid_by_day = {}
            for _, opened, message, _ in invalid:
                if opened is not None and (opened + OFFSET_MS) % DAY_MS == 0:
                    invalid_by_day.setdefault(opened, []).append(message)
            for opened in range(day_ms(START_DATE), day_ms(today) + 1, DAY_MS):
                item = stored.get(opened)
                reasons = (['source_conflict'] if item and item['conflicted'] else
                           ['not_closed'] if item and not item['is_closed'] else
                           (invalid_by_day.get(opened) or ['not_closed' if opened == day_ms(today) else 'missing_bar']) if not item else [])
                if reasons:
                    db.execute('INSERT INTO feed_gaps VALUES (?,?,?,?)', (symbol, opened, encode(reasons), finished))
            summary = {'inserted': inserted, 'changedPartialOrClosed': changed, 'invalidRows': len(invalid),
                       'newConflicts': conflict_count, 'stagedBars': len(staged), 'rangeEnd': today.isoformat()}
            db.execute('''UPDATE symbol_state SET status='ready',last_success=?,last_error=NULL,server_ms=?,
                          range_start=?,range_end=?,generation=generation+1 WHERE symbol=?''',
                       (finished, server_ms, START_DATE.isoformat(), today.isoformat(), symbol))
            db.execute("UPDATE feed_jobs SET status='succeeded',phase='finished',finished=?,summary=? WHERE id=?", (finished, encode(summary), identity))

    def _snapshot(self, symbol):
        validate_symbol(symbol)
        with self.connect() as db:
            # sqlite3 starts transactions implicitly for writes, not SELECTs.
            # Pin state, candles and gaps to one committed cache generation.
            db.execute('BEGIN')
            state = dict(db.execute('SELECT * FROM symbol_state WHERE symbol=?', (symbol,)).fetchone())
            candles = {row['open_ms']: dict(row) for row in db.execute('''SELECT c.*,r.body_sha256 FROM candles c
                         LEFT JOIN feed_requests r ON c.request_id=r.id WHERE c.symbol=? ORDER BY c.open_ms''', (symbol,))}
            gaps = {row['open_ms']: json.loads(row['reasons']) for row in db.execute('SELECT * FROM feed_gaps WHERE symbol=?', (symbol,))}
        return state, candles, gaps

    def _coverage(self, state, candles, gaps):
        if not state['range_start']:
            return {'total': 0, 'complete': 0, 'missing': 0, 'pending': 0, 'conflicts': 0}
        total = (date.fromisoformat(state['range_end']) - date.fromisoformat(state['range_start'])).days + 1
        conflicts = sum(bool(r['conflicted']) for r in candles.values())
        complete = sum(bool(r['is_closed']) and not r['conflicted'] for r in candles.values())
        pending = sum('not_closed' in reasons for reasons in gaps.values())
        return {'total': total, 'complete': complete, 'missing': total - complete - conflicts - pending,
                'pending': pending, 'conflicts': conflicts}

    def status(self):
        with self.connect() as db:
            cooldown = self._cooldown(db)
        symbols = []
        for symbol in SYMBOLS:
            state, candles, gaps = self._snapshot(symbol)
            symbols.append({'symbol': symbol, 'available': bool(state['range_start']), 'status': state['status'],
                            'range': {'start': state['range_start'], 'end': state['range_end']},
                            'coverage': self._coverage(state, candles, gaps),
                            'lastSuccessAtUtc': state['last_success'], 'lastAttemptAtUtc': state['last_attempt'],
                            'lastError': state['last_error']})
        return {'version': VERSION, 'provider': 'Binance', 'manualOnly': True, 'job': self._job(),
                'symbols': symbols, 'cooldownUntilUtc': cooldown, 'frozenResearchChanged': False}

    def _row(self, day, stored, reasons):
        opened = day_ms(day)
        calendar = day_calendar(day.isoformat())
        closed = bool(stored and stored['is_closed'])
        complete = bool(closed and not stored['conflicted'])
        return {'date': day.isoformat(), 'openTimeUtc': ms_iso(opened),
                'closeTimeUtcExclusive': ms_iso(opened + DAY_MS), 'atLocal': day.isoformat() + 'T00:00:00+08:00',
                **{key: float(stored[key]) if complete else None for key in ('open', 'high', 'low', 'close', 'volume')},
                'complete': complete, 'isClosed': closed, 'errors': reasons,
                'sourceRefs': [f'request:{stored["request_id"]};sha256:{stored["body_sha256"]}'] if stored else [],
                'pillars': calendar['pillars'], 'elements': element_labels(calendar['pillars'])}

    def history(self, symbol):
        state, candles, gaps = self._snapshot(symbol)
        available = bool(state['range_start'])
        rows = []
        if available:
            start, end = date.fromisoformat(state['range_start']), date.fromisoformat(state['range_end'])
            current = start
            while current <= end:
                opened = day_ms(current)
                rows.append(self._row(current, candles.get(opened), gaps.get(opened, [])))
                current += timedelta(days=1)
        return {'schemaVersion': 1, 'version': VERSION, 'available': available, 'symbol': symbol,
                'venue': 'Binance', 'marketType': 'spot', 'interval': '1d', 'timezone': 'UTC+8', 'labelAt': 'dayStart',
                'range': {'start': state['range_start'], 'end': state['range_end']},
                'coverage': self._coverage(state, candles, gaps), 'rows': rows,
                'transitions': transitions_for(state['range_start'], state['range_end']) if available else [],
                'source': {'title': 'Binance公开日线接口 · 独立动态缓存', 'mode': 'dynamic_cache',
                           'files': ['market.sqlite3:feed_requests', 'market.sqlite3:candles'],
                           'asOf': state['range_end'], 'lastSuccessAtUtc': state['last_success'],
                           'lastAttemptAtUtc': state['last_attempt'], 'lastError': state['last_error'],
                           'serverTimeUtc': ms_iso(state['server_ms']) if state['server_ms'] is not None else None,
                           'volumeUnit': symbol.removesuffix('USDT'), 'priceUnit': 'USDT', 'generation': state['generation'],
                           'endpoint': BASE_URL + '/api/v3/klines', 'timeZoneParameter': '8',
                           'closedOnlyPlotted': True, 'rawResponsesRetained': True},
                'volumeUnit': symbol.removesuffix('USDT'), 'calendarPolicy': POLICY,
                'marketDirection': None, 'marketValidated': False, 'liveMarketData': True,
                'limits': ['手动拉取的日线缓存，并非实时推送。', '未收盘、缺口与冲突日保留时间格，不绘制蜡烛。',
                           '动态缓存不会更新已冻结的历史规则评分或自动产生预测。',
                           '干支为每日00:00快照，日内交节另列；源冲突保留旧记录并停止绘制该日。']}

    def detail(self, symbol, day, profile):
        validate_profile(symbol, profile)
        parsed = parse_day(day)
        state, candles, gaps = self._snapshot(symbol)
        if not state['range_start'] or not state['range_start'] <= day <= state['range_end']:
            raise MarketFeedError('该日期尚无动态行情缓存，请先手动更新', 400)
        opened = day_ms(parsed)
        row = self._row(parsed, candles.get(opened), gaps.get(opened, []))
        relation = calculate_relations(profile, row['atLocal'])
        return {'symbol': symbol, 'profile': profile, 'date': day, 'atLocal': row['atLocal'], 'row': row,
                'calendar': relation['calendar'], 'features': calculate_features(relation['relationships'], relation['calendar']),
                'transitions': transitions_for(day, day), 'marketDirection': None, 'marketValidated': False,
                'sourceMode': 'dynamic_cache', 'frozenRuleReviewApplied': False}
