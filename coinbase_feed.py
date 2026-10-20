"""Manual Coinbase Exchange BTC/USD and ETH/USD cache for Beijing-day scoring.

The TradingView iframe remains display-only. This module fetches the same
exchange's public hourly candles and aggregates only complete UTC+8 days.
Raw HTTP responses and provenance are retained separately from legacy USDT.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib import error, parse, request
import hashlib
import json
import sqlite3
import threading
import uuid

LOCAL = timezone(timedelta(hours=8))
UTC = timezone.utc
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
BASE = 'https://api.exchange.coinbase.com'
SYMBOLS = {'BTCUSD': 'BTC-USD', 'ETHUSD': 'ETH-USD'}
MAX_BODY_BYTES = 2_000_000
MAX_DAYS = 180
PAGE_HOURS = 240  # below the documented 300-candle limit


class CoinbaseFeedError(RuntimeError):
    def __init__(self, message, status_code=503):
        super().__init__(message)
        self.status_code = status_code


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def ms(at):
    return int(at.timestamp() * 1000)


def local_open(day):
    return datetime.combine(day, time(), LOCAL)


def price(value, positive=True):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('invalid_number')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid_number') from exc
    if not result.is_finite() or (result <= 0 if positive else result < 0):
        raise ValueError('invalid_number')
    return result


def token(value):
    # Canonicalize equivalent JSON numbers before comparing repeated fetches.
    return format(value.normalize(), 'f')


def default_transport(product, start, end):
    if product not in SYMBOLS.values() or start.tzinfo is None or end.tzinfo is None:
        raise CoinbaseFeedError('Coinbase 请求参数无效', 400)
    query = parse.urlencode({'start': start.astimezone(UTC).isoformat().replace('+00:00', 'Z'),
                             'end': end.astimezone(UTC).isoformat().replace('+00:00', 'Z'),
                             'granularity': 3600})
    url = BASE + '/products/' + product + '/candles?' + query

    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    req = request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'LocalResearchDesk/0.20'}, method='GET')
    opener = request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=20) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            status = response.status
    except error.HTTPError as exc:
        body, status = exc.read(MAX_BODY_BYTES + 1), exc.code
    except (OSError, error.URLError) as exc:
        raise CoinbaseFeedError('Coinbase 行情请求失败：' + type(exc).__name__) from exc
    if len(body) > MAX_BODY_BYTES:
        raise CoinbaseFeedError('Coinbase 响应超过大小限制')
    return status, body


def parse_page(body, start, end):
    try:
        rows = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CoinbaseFeedError('Coinbase 响应不是有效 JSON') from exc
    if not isinstance(rows, list) or len(rows) > 300:
        raise CoinbaseFeedError('Coinbase 蜡烛数量或结构异常')
    parsed = {}
    for row in rows:
        if not isinstance(row, list) or len(row) != 6 or type(row[0]) is not int:
            raise CoinbaseFeedError('Coinbase 小时线结构异常')
        stamp = row[0]
        if stamp % 3600 or not ms(start) <= stamp * 1000 < ms(end):
            # The API documents that some returned buckets may precede start.
            if stamp % 3600:
                raise CoinbaseFeedError('Coinbase 小时线时间戳异常')
            continue
        low, high, opened, closed, volume = (price(row[i], positive=i != 5) for i in range(1, 6))
        if not low <= min(opened, closed) <= max(opened, closed) <= high:
            raise CoinbaseFeedError('Coinbase 小时线价格顺序异常')
        normalized = {'low': token(low), 'high': token(high), 'open': token(opened),
                      'close': token(closed), 'volume': token(volume)}
        if stamp in parsed and parsed[stamp] != normalized:
            raise CoinbaseFeedError('单次 Coinbase 响应内存在冲突小时线')
        parsed[stamp] = normalized
    return parsed


def aggregate(day, hours):
    first = int(local_open(day).timestamp())
    stamps = [first + i * 3600 for i in range(24)]
    if any(stamp not in hours for stamp in stamps):
        return None, ['missing_hour']
    rows = [hours[stamp][0] for stamp in stamps]
    refs = sorted({hours[stamp][1] for stamp in stamps})
    result = {'open_ms': ms(local_open(day)), 'close_ms': ms(local_open(day) + DAY) - 1,
              'open': rows[0]['open'], 'high': token(max(Decimal(r['high']) for r in rows)),
              'low': token(min(Decimal(r['low']) for r in rows)), 'close': rows[-1]['close'],
              'volume': token(sum((Decimal(r['volume']) for r in rows), Decimal(0))),
              'request_id': 'coinbase-pages:' + hashlib.sha256(encode(refs).encode()).hexdigest()[:24],
              'body_sha256': hashlib.sha256(encode([(stamp, hours[stamp][0]) for stamp in stamps]).encode()).hexdigest(),
              'source_request_ids': encode(refs), 'is_closed': 1, 'conflicted': 0}
    return result, []


class CoinbaseMarketFeed:
    def __init__(self, data_dir, transport=None, clock=None):
        self.path = Path(data_dir) / 'coinbase_market.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.transport = transport or default_transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lock = threading.Lock()
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,
                start_utc TEXT NOT NULL,end_utc TEXT NOT NULL,fetched_utc TEXT NOT NULL,
                http_status INTEGER NOT NULL,body BLOB NOT NULL,body_sha256 TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS candles(symbol TEXT NOT NULL,open_ms INTEGER NOT NULL,
                close_ms INTEGER NOT NULL,open TEXT NOT NULL,high TEXT NOT NULL,low TEXT NOT NULL,
                close TEXT NOT NULL,volume TEXT NOT NULL,is_closed INTEGER NOT NULL,
                conflicted INTEGER NOT NULL,request_id TEXT NOT NULL,body_sha256 TEXT NOT NULL,
                source_request_ids TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(symbol,open_ms));
              CREATE TABLE IF NOT EXISTS gaps(symbol TEXT NOT NULL,open_ms INTEGER NOT NULL,
                reasons TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(symbol,open_ms));
              CREATE TABLE IF NOT EXISTS state(symbol TEXT PRIMARY KEY,status TEXT NOT NULL,
                range_start TEXT,range_end TEXT,last_attempt TEXT,last_success TEXT,last_error TEXT,
                generation INTEGER NOT NULL DEFAULT 0);
            ''')
            for symbol in SYMBOLS:
                db.execute("INSERT OR IGNORE INTO state(symbol,status) VALUES (?,'empty')", (symbol,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def refresh(self, symbol='BTCUSD', days=90):
        if symbol not in SYMBOLS or type(days) is not int or not 1 <= days <= MAX_DAYS:
            raise CoinbaseFeedError('仅支持 BTCUSD/ETHUSD，范围为 1–180 个完整北京时间日', 400)
        last = self.clock().astimezone(LOCAL).date() - DAY
        first = last - (days - 1) * DAY
        return self.refresh_range(symbol, first, last)

    def refresh_range(self, symbol, first, last):
        """Fetch an explicit completed-day span; used by bounded historical backfill."""
        if (symbol not in SYMBOLS or type(first) is not date or type(last) is not date
                or first > last or not 1 <= (last - first).days + 1 <= MAX_DAYS
                or last >= self.clock().astimezone(LOCAL).date()):
            raise CoinbaseFeedError('历史回补仅支持 1–180 个已经收盘的北京时间日', 400)
        days = (last - first).days + 1
        if not self.lock.acquire(blocking=False):
            raise CoinbaseFeedError('已有 Coinbase 更新正在进行', 409)
        try:
            start, end = local_open(first).astimezone(UTC), local_open(last + DAY).astimezone(UTC)
            pages, hours = [], {}
            cursor = start
            while cursor < end:
                stop = min(end, cursor + PAGE_HOURS * HOUR)
                status, body = self.transport(SYMBOLS[symbol], cursor, stop)
                if status != 200:
                    raise CoinbaseFeedError(f'Coinbase 返回 HTTP {status}')
                identity = str(uuid.uuid4())
                fetched = self.clock().astimezone(UTC).isoformat()
                parsed = parse_page(body, cursor, stop)
                pages.append((identity, symbol, cursor.isoformat(), stop.isoformat(), fetched,
                              status, body, hashlib.sha256(body).hexdigest()))
                for stamp, row in parsed.items():
                    if stamp in hours and hours[stamp][0] != row:
                        raise CoinbaseFeedError('不同 Coinbase 响应中的小时线冲突，未写入')
                    hours[stamp] = (row, identity)
                cursor = stop
            finished = self.clock().astimezone(UTC).isoformat()
            bars, gaps = {}, {}
            for i in range(days):
                day = first + i * DAY
                bar, reasons = aggregate(day, hours)
                opened = ms(local_open(day))
                if bar:
                    bars[opened] = bar
                else:
                    gaps[opened] = reasons
            conflicts = 0
            with self.connect() as db:
                for page in pages:
                    db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?)', page)
                for opened, bar in bars.items():
                    existing = db.execute('SELECT * FROM candles WHERE symbol=? AND open_ms=?', (symbol, opened)).fetchone()
                    if existing and any(existing[key] != bar[key] for key in ('open', 'high', 'low', 'close', 'volume')):
                        conflicts += 1
                        db.execute('UPDATE candles SET conflicted=1 WHERE symbol=? AND open_ms=?', (symbol, opened))
                        gaps[opened] = ['source_conflict']
                        continue
                    if not existing:
                        db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                   (symbol, *[bar[key] for key in ('open_ms','close_ms','open','high','low','close','volume','is_closed','conflicted','request_id','body_sha256','source_request_ids')], finished))
                for opened in (ms(local_open(first + i * DAY)) for i in range(days)):
                    if opened in gaps:
                        db.execute('INSERT OR REPLACE INTO gaps VALUES (?,?,?,?)',
                                   (symbol, opened, encode(gaps[opened]), finished))
                    else:
                        db.execute('DELETE FROM gaps WHERE symbol=? AND open_ms=?', (symbol, opened))
                db.execute('''UPDATE state SET status='ready',
                             range_start=CASE WHEN range_start IS NULL OR range_start>? THEN ? ELSE range_start END,
                             range_end=CASE WHEN range_end IS NULL OR range_end<? THEN ? ELSE range_end END,
                             last_attempt=?,last_success=?,last_error=NULL,generation=generation+1
                             WHERE symbol=?''', (first.isoformat(), first.isoformat(), last.isoformat(),
                                               last.isoformat(), finished, finished, symbol))
            return {'symbol': symbol, 'provider': 'Coinbase Exchange', 'days': days,
                    'range': {'start': first.isoformat(), 'end': last.isoformat()},
                    'complete': len(bars) - conflicts, 'missing': len(gaps), 'conflicts': conflicts,
                    'requestCount': len(pages), 'lastSuccessAtUtc': finished}
        finally:
            self.lock.release()

    def _snapshot(self, symbol):
        if symbol not in SYMBOLS:
            raise CoinbaseFeedError('Coinbase 交易对无效', 400)
        with self.connect() as db:
            db.execute('BEGIN')
            state = dict(db.execute('SELECT * FROM state WHERE symbol=?', (symbol,)).fetchone())
            state.update({'provider': 'Coinbase Exchange', 'product': SYMBOLS[symbol],
                          'marketType': 'spot', 'dayTimezone': 'UTC+8'})
            candles = {row['open_ms']: dict(row) for row in db.execute(
                'SELECT * FROM candles WHERE symbol=? ORDER BY open_ms', (symbol,))}
            gaps = {row['open_ms']: json.loads(row['reasons']) for row in db.execute(
                'SELECT * FROM gaps WHERE symbol=?', (symbol,))}
        return state, candles, gaps

    def status(self):
        symbols = []
        for symbol in SYMBOLS:
            state, candles, gaps = self._snapshot(symbol)
            symbols.append({'symbol': symbol, 'status': state['status'],
                            'range': {'start': state['range_start'], 'end': state['range_end']},
                            'completeDays': sum(r['is_closed'] and not r['conflicted'] for r in candles.values()),
                            'gapDays': len(gaps), 'lastSuccessAtUtc': state['last_success'],
                            'generation': state['generation']})
        return {'provider': 'Coinbase Exchange', 'marketType': 'spot', 'timezone': 'UTC+8',
                'manualOnly': True, 'symbols': symbols}

    def history(self, symbol, limit=31):
        if type(limit) is not int or not 1 <= limit <= 180:
            raise CoinbaseFeedError('历史预览范围须为 1–180 日', 400)
        state, candles, gaps = self._snapshot(symbol)
        if not state['range_end']:
            return {'symbol': symbol, 'available': False, 'rows': [], 'provider': 'Coinbase Exchange'}
        end = date.fromisoformat(state['range_end'])
        first = max(date.fromisoformat(state['range_start']), end - (limit - 1) * DAY)
        rows = []
        cursor = first
        while cursor <= end:
            opened = ms(local_open(cursor))
            bar = candles.get(opened)
            rows.append({'date': cursor.isoformat(),
                         'open': bar['open'] if bar and bar['is_closed'] and not bar['conflicted'] else None,
                         'high': bar['high'] if bar and bar['is_closed'] and not bar['conflicted'] else None,
                         'low': bar['low'] if bar and bar['is_closed'] and not bar['conflicted'] else None,
                         'close': bar['close'] if bar and bar['is_closed'] and not bar['conflicted'] else None,
                         'volume': bar['volume'] if bar and bar['is_closed'] and not bar['conflicted'] else None,
                         'gaps': gaps.get(opened, []),
                         'sourceSha256': bar['body_sha256'] if bar else None})
            cursor += DAY
        return {'symbol': symbol, 'available': True, 'provider': 'Coinbase Exchange',
                'timezone': 'UTC+8', 'range': {'start': first.isoformat(), 'end': end.isoformat()},
                'rows': rows, 'lastSuccessAtUtc': state['last_success']}


class ScoringMarketRouter:
    """Keep old USDT records on their historical cache; use Coinbase for USD."""
    def __init__(self, legacy, coinbase):
        self.legacy, self.coinbase = legacy, coinbase

    def _snapshot(self, symbol):
        return self.coinbase._snapshot(symbol) if symbol in SYMBOLS else self.legacy._snapshot(symbol)
