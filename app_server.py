"""Loopback research desk. Calculations and append-only notes remain on this computer."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlsplit, parse_qs
import uuid

from calendar_adapter import calculate_relations
from calendar_browser import calendar_browser, parse_browser_query
from historical_chart import ChartIntegrityError, chart_detail, load_history, parse_chart_query
from rule_events import load_rule_events, parse_event_query
from market_feed import MarketFeed, MarketFeedError
from coinbase_feed import CoinbaseMarketFeed, CoinbaseFeedError, ScoringMarketRouter
from market_multiframe import MarketMultiframe, parse_bars_query
from prospective import ProspectiveStudy, ProspectiveError
from month_interaction_prospective import MonthInteractionProspective, MonthInteractionError
from confluence_study import read as read_confluence
from prediction_ledger import PredictionLedger, PredictionLedgerError
from historical_exploration_store import read_artifact, ExplorationIntegrityError
from month_study_store import read_month_artifact, MonthStudyIntegrityError
from coinbase_study_store import read as read_coinbase_study, CoinbaseStudyIntegrityError
from coinbase_calendar_backtest import read_calendar as read_coinbase_calendar
from coinbase_rule_compare import read_comparison as read_coinbase_rule_comparison
from coinbase_intraday import inspect_intraday
from coinbase_forward import CoinbaseForward, CoinbaseForwardError
from jie_observation import jie_windows
from chart_candidates import chart_candidates, parse_chart_candidates_query, parse_chart_candidate_options, ChartCandidatesError, candidate_root_conflict
from calendar_accuracy import load_accuracy_evidence, CalendarAccuracyEvidenceError
from bazi_features import calculate_features
from astro_adapter import calculate_sky, parse_instant

HERE = Path(__file__).resolve().parent
VERSION = '0.19.0'
PROFILE_NAMES = {'BTC-BZ-V01': 'BTC · 四柱版本 01', 'ETH-BAZI-A': 'ETH · 四柱版本 A',
                 'ETH-BAZI-B': 'ETH · 四柱版本 B', 'PERSON-BZ-H01': '人物盘 · 时辰假设'}
CALC_LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def load(name):
    return json.loads((HERE / name).read_text(encoding='utf-8-sig'))


def structure_study():
    if not (HERE / 'structure_results.json').is_file():
        return {'status': 'not_run', 'hypothesisId': 'H-BZ-STRUCT-002', 'periods': [], 'marketValidated': False}
    return load('structure_results.json')


def source_catalog():
    refs = [
        {'id': 'bazi-calendar', 'title': '历法与日界：lunar-python', 'url': 'https://github.com/6tail/lunar-python',
         'supports': '日期转干支与节气，不提供行情方向'},
        {'id': 'astronomy', 'title': '基础星位：Astronomy Engine', 'url': 'https://github.com/cosinekitty/astronomy',
         'supports': '地心天文位置；五种相位及1°容许度是本项目研究设置'},
        {'id': 'swiss', 'title': 'Lahiri 与计算标志：Swiss Ephemeris', 'url': 'https://www.astro.com/swisseph/swephprg.htm',
         'supports': '恒星黄道和平均月交点的计算约定'},
        {'id': 'indian-textbook', 'title': 'D9 与运限：P. V. R. Narasimha Rao 教材', 'url': 'https://www.vedicastrologer.org/articles/vedic_astro_textbook.pdf',
         'supports': '§6.2.9、§16.1–16.3；只取算法定义，固定年长另行声明'},
    ]
    return refs


@lru_cache(maxsize=64)
def calculate(profile, at, scenario=False):
    if profile not in PROFILE_NAMES:
        raise ValueError('请选择已记录的盘版本')
    instant = parse_instant(at)
    if not 1992 <= instant.year <= 2099:
        raise ValueError('本版研究范围为1992–2099年')
    with CALC_LOCK:
        bazi = calculate_relations(profile, at)
        bazi['features'] = calculate_features(bazi['relationships'], bazi['calendar'])
        sky = calculate_sky(at)
        try:
            from indian_adapter import calculate_indian
        except ModuleNotFoundError:
            indian = {'available': False, 'reason': '印度占星适配器尚未完成安装'}
        else:
            indian = calculate_indian(at, scenario=scenario)
    data = {'schemaVersion': 1, 'version': VERSION, 'input': {'profile': profile, 'at': at, 'scenario': scenario},
            'calculatedAtUtc': now(), 'bazi': bazi, 'tropical': sky, 'indian': indian,
            'marketValidated': False, 'marketDirection': None,
            'studyStatus': {'id': 'H-BZ-DAY-001', 'status': 'exploratory_interval_includes_zero',
                            'asOf': '2026-09-27', 'missingDays': 18, 'liveMarketData': False, 'hypothesisVersion': '0.2.0'}}
    study = structure_study()
    data['structureStudyStatus'] = {'id': 'H-BZ-STRUCT-002', 'status': study.get('status'),
                                    'marketValidated': False, 'resultsAreFixedHistory': True,
                                    'resultsSha256': hashlib.sha256(encode(study).encode()).hexdigest()}
    data['snapshotSha256'] = hashlib.sha256(encode(data).encode()).hexdigest()
    return data


def clean_text(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{name}长度或格式不符合要求')
    return value.strip()


def parse_inputs(body):
    if not isinstance(body, dict):
        raise ValueError('输入必须为对象')
    profile = clean_text(body.get('profile'), '盘版本', 64, True)
    at = clean_text(body.get('at'), '时间', 48, True)
    scenario = body.get('scenario', False)
    if type(scenario) is not bool:
        raise ValueError('运限演算开关必须为布尔值')
    return profile, at, scenario


def market_query(query, detail=False):
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    expected = {'symbol', 'date', 'profile'} if detail else {'symbol'}
    if set(values) != expected or any(len(v) != 1 for v in values.values()):
        raise ValueError('行情参数不完整、重复或含未知参数')
    return {key: value[0] for key, value in values.items()}


def timing_query(query, gann=False):
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    expected = {'symbol', 'start', 'end'} if gann else {'start', 'end'}
    if set(values) != expected or any(len(v) != 1 for v in values.values()):
        raise ValueError('时间研究参数不完整、重复或含未知参数')
    return {key: value[0] for key, value in values.items()}


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS records (
              id TEXT PRIMARY KEY, created TEXT NOT NULL, input TEXT NOT NULL,
              snapshot TEXT NOT NULL, note TEXT NOT NULL, judgement TEXT NOT NULL,
              window_start TEXT, window_end TEXT, timing_class TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS reviews (
              id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id),
              created TEXT NOT NULL, outcome TEXT NOT NULL, note TEXT NOT NULL);''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, body):
        profile, at, scenario = parse_inputs(body)
        note = clean_text(body.get('note', ''), '研究笔记', 4000)
        judgement = body.get('judgement', 'observation')
        if judgement not in ('observation', 'up', 'down', 'range', 'volatility'):
            raise ValueError('判断类型无效')
        start, end = body.get('windowStart') or None, body.get('windowEnd') or None
        if judgement != 'observation' and (not start or not end):
            raise ValueError('记录方向判断时必须指定完整验证窗口')
        if bool(start) != bool(end):
            raise ValueError('验证窗口须同时填写起止时间')
        created = now()
        timing = 'observation'
        if start:
            ds, de = parse_instant(start), parse_instant(end)
            if ds >= de:
                raise ValueError('验证窗口结束必须晚于开始')
            timing = 'recorded_before_window' if ds > parse_instant(created) else 'historical_or_already_started'
        snapshot = calculate(profile, at, scenario)
        requested_hash = body.get('snapshotSha256')
        if requested_hash and requested_hash != snapshot['snapshotSha256']:
            raise ValueError('页面结果已过期，请重新计算后保存')
        identity = str(uuid.uuid4())
        with self.connect() as db:
            db.execute('INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)',
                       (identity, created, encode(snapshot['input']), encode(snapshot), note, judgement, start, end, timing))
        return self.get(identity)

    def get(self, identity):
        with self.connect() as db:
            row = db.execute('SELECT * FROM records WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise ValueError('记录不存在')
            value = dict(row)
            value['input'] = json.loads(value['input'])
            value['snapshot'] = json.loads(value['snapshot'])
            value['reviews'] = [dict(x) for x in db.execute('SELECT * FROM reviews WHERE record_id=? ORDER BY created', (identity,))]
            value['evaluationType'] = 'manual_review_not_backtest'
            return value

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT id,created,input,note,judgement,window_start,window_end,timing_class FROM records ORDER BY created DESC LIMIT 200').fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item['input'] = json.loads(item['input'])
                item['reviews'] = [dict(x) for x in db.execute('SELECT * FROM reviews WHERE record_id=? ORDER BY created', (item['id'],))]
                result.append(item)
            return result

    def review(self, identity, body):
        self.get(identity)
        if not isinstance(body, dict) or body.get('outcome') not in ('pending', 'supported', 'contradicted', 'ambiguous'):
            raise ValueError('复盘结果无效')
        note = clean_text(body.get('note', ''), '复盘依据', 4000, True)
        with self.connect() as db:
            db.execute('INSERT INTO reviews VALUES (?,?,?,?,?)', (str(uuid.uuid4()), identity, now(), body['outcome'], note))
        return self.get(identity)


class Handler(BaseHTTPRequestHandler):
    server_version = 'ResearchDesk/0.19'
    def log_message(self, *args):
        pass

    def send(self, code, value, content_type='application/json; charset=utf-8', filename=None):
        data = value if isinstance(value, bytes) else encode(value).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self' https://s3.tradingview.com; connect-src 'self'; frame-src https://www.tradingview.com https://*.tradingview.com https://www.tradingview-widget.com; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def host_ok(self):
        hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts:
            self.send(403, {'error': '只接受本机地址'})
            return False
        return True

    def do_GET(self):
        if not self.host_ok():
            return
        route = urlsplit(self.path)
        if len(self.path) > 2048:
            return self.send(414, {'error': '请求过长'})
        static = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/history-chart.js': ('history-chart.js', 'text/javascript; charset=utf-8'),
                  '/tradingview-chart.js': ('tradingview-chart.js', 'text/javascript; charset=utf-8'),
                  '/coinbase-feed.js': ('coinbase-feed.js', 'text/javascript; charset=utf-8'),
                  '/coinbase-study.js': ('coinbase-study.js', 'text/javascript; charset=utf-8'),
                  '/bitstamp-study.js': ('bitstamp-study.js', 'text/javascript; charset=utf-8'),
                  '/coinbase-forward.js': ('coinbase-forward.js', 'text/javascript; charset=utf-8'),
                  '/coinbase-calendar-backtest.js': ('coinbase-calendar-backtest.js', 'text/javascript; charset=utf-8'),
                  '/coinbase-rule-compare.js': ('coinbase-rule-compare.js', 'text/javascript; charset=utf-8'),
                  '/coinbase-intraday.js': ('coinbase-intraday.js', 'text/javascript; charset=utf-8'),
                  '/prospective.js': ('prospective.js', 'text/javascript; charset=utf-8'),
                  '/timing.js': ('timing.js', 'text/javascript; charset=utf-8'),
                  '/time-calendar.js': ('time-calendar.js', 'text/javascript; charset=utf-8'),
                  '/calendar-price-window.js': ('calendar-price-window.js', 'text/javascript; charset=utf-8'),
                  '/prediction.js': ('prediction.js', 'text/javascript; charset=utf-8'),
                  '/exploration.js': ('exploration.js', 'text/javascript; charset=utf-8'),
                  '/rule-validation.js': ('rule-validation.js', 'text/javascript; charset=utf-8'),
                  '/month-interaction-future.js': ('month-interaction-future.js', 'text/javascript; charset=utf-8'),
                  '/confluence-study.js': ('confluence-study.js', 'text/javascript; charset=utf-8'),
                  '/month-study.js': ('month-study.js', 'text/javascript; charset=utf-8'),
                  '/chart-candidates.js': ('chart-candidates.js', 'text/javascript; charset=utf-8'),
                  '/month-theory-notes': ('month-theory-notes.html', 'text/html; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
        try:
            if route.path in static:
                name, mime = static[route.path]
                return self.send(200, (HERE / 'web' / name).read_bytes(), mime)
            if route.path == '/licenses':
                return self.send(200, (HERE / 'LICENSE_NOTICES.md').read_bytes(), 'text/plain; charset=utf-8')
            if route.path == '/source.zip':
                source = HERE / 'source_bundle.zip'
                if source.is_file():
                    return self.send(200, source.read_bytes(), 'application/zip')
                return self.send(404, {'error': '请使用本地完整资料包中的源码；发布打包尚未完成。'})
            if route.path == '/health':
                return self.send(200, {'version': VERSION, 'localOnly': True, 'marketExecutionEnabled': False})
            if route.path == '/api/rule-validation-registry':
                if route.query:
                    raise ValueError('固定规则目录不接受查询参数')
                return self.send(200, json.loads((HERE / 'rule_validation_registry.json').read_text(encoding='utf-8')))
            if route.path == '/api/month-interaction-future':
                if route.query:
                    raise ValueError('事前组合账本不接受查询参数')
                return self.send(200, self.server.month_interaction.status())
            if route.path == '/api/confluence-study':
                if route.query:
                    raise ValueError('固定共振报告不接受查询参数')
                return self.send(200, read_confluence())
            if route.path == '/api/bitstamp-long-study':
                if route.query:
                    raise ValueError('2013年长期回测不接受查询参数')
                result = load('research/bitstamp-long-summary-2013-2026.json')
                if (result.get('source', {}).get('firstDay') != '2013-01-01'
                        or result.get('source', {}).get('provider') != 'Bitstamp official public BTC/USD OHLC'):
                    raise ValueError('长期回测来源或日期不符')
                return self.send(200, result)
            if route.path == '/api/pattern-screen-2013':
                if route.query:
                    raise ValueError('固定规律筛选不接受查询参数')
                result = load('research/pattern-screen-2013-2026.json')
                source = load('research/bitstamp-long-summary-2013-2026.json')['source']
                if (result.get('dataThrough') != source['lastDay']
                        or result.get('sourceDailySha256') != source['localDailySha256']):
                    raise ValueError('规律筛选来源与长期回测不符')
                return self.send(200, result)
            if route.path == '/api/astro-pattern-screen-2013':
                if route.query:
                    raise ValueError('固定星象筛选不接受查询参数')
                result = load('research/astro-pattern-screen-2013-2026.json')
                source = load('research/bitstamp-long-summary-2013-2026.json')['source']
                if (result.get('data', {}).get('lastDay') != source['lastDay']
                        or result.get('data', {}).get('rawManifestSha256') != source['localRawManifestSha256']):
                    raise ValueError('星象筛选来源与长期回测不符')
                return self.send(200, result)
            coinbase_study_routes = {'/api/coinbase-study': 'report',
                                     '/api/coinbase-study-details': 'details',
                                     '/api/coinbase-study-protocol': 'protocol',
                                     '/api/coinbase-study-registration': 'registration',
                                     '/api/coinbase-study-manifest': 'manifest'}
            if route.path == '/api/coinbase-study-calendar':
                args = parse_qs(route.query, keep_blank_values=True, strict_parsing=True)
                if set(args) != {'symbol', 'year'} or any(len(value) != 1 for value in args.values()):
                    raise ValueError('历史日历需指定唯一 symbol 与 year')
                return self.send(200, read_coinbase_calendar(args['symbol'][0], int(args['year'][0])))
            if route.path == '/api/coinbase-rule-comparison':
                if route.query:
                    raise ValueError('固定规则对照不接受查询参数')
                return self.send(200, read_coinbase_rule_comparison())
            if route.path == '/api/coinbase-intraday':
                args = parse_qs(route.query, keep_blank_values=True, strict_parsing=True)
                if set(args) != {'symbol', 'date'} or any(len(value) != 1 for value in args.values()):
                    raise ValueError('日内对照需指定唯一 symbol 与 date')
                return self.send(200, inspect_intraday(args['symbol'][0], args['date'][0],
                    [self.server.coinbase.path.parent, HERE.parent / '时序研究室_v0.5' / 'local_data']))
            if route.path in coinbase_study_routes:
                if route.query:
                    raise ValueError('固定 Coinbase 回测不接受查询参数')
                return self.send(200, read_coinbase_study(coinbase_study_routes[route.path]))
            month_routes = {'/api/month-study':'report', '/api/month-study-details':'details',
                            '/api/month-study-protocol':'protocol', '/api/month-study-registration':'registration'}
            if route.path in month_routes:
                if route.query:
                    raise ValueError('固定月份研究不接受改参或查询参数')
                kind = month_routes[route.path]
                result = read_month_artifact(kind, HERE)
                filename = None if kind == 'report' else 'BTC-month-study-v0.17-' + kind + '.json'
                return self.send(200, result, filename=filename)
            exploration_routes = {'/api/exploration':'report', '/api/exploration-details':'details',
                                  '/api/exploration-protocol':'protocol', '/api/exploration-snapshot':'snapshot',
                                  '/api/exploration-registration':'registration'}
            if route.path in exploration_routes:
                if route.query:
                    raise ValueError('固定历史回测不接受改参或查询参数')
                kind = exploration_routes[route.path]
                result = read_artifact(kind, HERE)
                filename = None if kind == 'report' else 'BTC-exploration-v0.16-' + kind + '.json'
                return self.send(200, result, filename=filename)
            if route.path in ('/api/predictions', '/api/predictions-export'):
                if route.query:
                    raise ValueError('预测账本地址不接受查询参数')
                if route.path.endswith('-export'):
                    return self.send(200, self.server.predictions.export(), filename='prediction-ledger-v0.15.json')
                return self.send(200, self.server.predictions.list())
            if route.path == '/api/prediction-confluence':
                from prediction_confluence import describe_confluence
                args = parse_qs(route.query, keep_blank_values=True, strict_parsing=True)
                if set(args) != {'symbol'} or len(args['symbol']) != 1:
                    raise ValueError('共振标的查询无效')
                symbol = args['symbol'][0]
                if symbol not in ('BTCUSDT', 'ETHUSDT', 'BTCUSD', 'ETHUSD'):
                    raise ValueError('共振标的无效')
                ledger = self.server.predictions.list()
                return self.send(200, describe_confluence([r for r in ledger['records'] if r['symbol'] == symbol], ledger['nowUtc']))
            if route.path == '/api/prediction-candidates':
                from prediction_candidates import preview_candidates
                args = timing_query(route.query, gann=True)
                with CALC_LOCK:
                    result = preview_candidates(self.server.market, **args)
                return self.send(200, result)
            if route.path == '/api/chart-candidates':
                args = parse_chart_candidate_options(route.query)
                with CALC_LOCK:
                    result = chart_candidates(self.server.market, args['symbol'], days=args['days'])
                return self.send(200, result)
            if route.path == '/api/gann-timing':
                args = timing_query(route.query, gann=True)
                from gann_timing import calculate_gann, GannTimingError
                try:
                    return self.send(200, calculate_gann(self.server.market, **args))
                except GannTimingError as exc:
                    return self.send(exc.status_code, {'error': str(exc)})
            if route.path == '/api/astro-events':
                args = timing_query(route.query)
                try:
                    from astro_events import calculate_astro_events
                    with CALC_LOCK:
                        result = calculate_astro_events(**args)
                except (ImportError, RuntimeError) as exc:
                    return self.send(503, {'error': f'星象计算不可用：{exc}'})
                return self.send(200, result)
            if route.path in ('/api/prospective-status', '/api/prospective-export'):
                if route.query:
                    raise ValueError('前瞻验证地址不接受查询参数；首期协议固定为 BTC 四柱版本 01')
                if route.path.endswith('-export'):
                    return self.send(200, self.server.prospective.export(), filename='BTC-prospective-v0.1.0.json')
                return self.send(200, self.server.prospective.status())
            if route.path == '/api/calendar-accuracy':
                if route.query:
                    raise ValueError('排盘核验地址不接受查询参数')
                return self.send(200, load_accuracy_evidence())
            if route.path == '/api/multiframe-status':
                if route.query:
                    raise ValueError('多周期行情状态地址不接受查询参数')
                return self.send(200, self.server.multiframe.status())
            if route.path == '/api/market-bars':
                args = parse_bars_query(route.query)
                with CALC_LOCK:
                    result = self.server.multiframe.bars(**args)
                return self.send(200, result)
            if route.path == '/api/market-status':
                if route.query:
                    raise ValueError('行情状态地址不接受查询参数')
                return self.send(200, self.server.market.status())
            if route.path == '/api/coinbase-status':
                if route.query:
                    raise ValueError('Coinbase 状态地址不接受查询参数')
                return self.send(200, self.server.coinbase.status())
            if route.path == '/api/coinbase-forward-status':
                if route.query:
                    raise ValueError('前瞻计划状态不接受查询参数')
                return self.send(200, self.server.coinbase_forward.status())
            if route.path == '/api/coinbase-forward-jie-windows':
                if route.query:
                    raise ValueError('交节观察窗口不接受查询参数')
                protocol = self.server.coinbase_forward.protocol()
                first = date.fromisoformat(protocol['startDate']) - timedelta(days=30)
                last = date.fromisoformat(protocol['endDate'])
                return self.send(200, jie_windows(first, last))
            if route.path == '/api/coinbase-forward-public-review':
                if route.query:
                    raise ValueError('公开计划只读复核不接受查询参数')
                return self.send(200, self.server.coinbase_forward.preview_public())
            if route.path == '/api/coinbase-history':
                args = parse_qs(route.query, keep_blank_values=True, strict_parsing=True)
                if set(args) != {'symbol'} or len(args['symbol']) != 1:
                    raise ValueError('Coinbase 历史预览须指定唯一 symbol')
                return self.send(200, self.server.coinbase.history(args['symbol'][0]))
            if route.path == '/api/calendar-price-window':
                args = parse_qs(route.query, keep_blank_values=True, strict_parsing=True)
                if set(args) != {'symbol', 'date'} or any(len(values) != 1 for values in args.values()):
                    raise ValueError('事件价格窗口须指定唯一 symbol 和 date')
                return self.send(200, self.server.coinbase.window(args['symbol'][0], args['date'][0]))
            if route.path == '/api/market-history':
                args = market_query(route.query)
                with CALC_LOCK:
                    result = self.server.market.history(args['symbol'])
                return self.send(200, result)
            if route.path == '/api/market-detail':
                args = market_query(route.query, detail=True)
                with CALC_LOCK:
                    result = self.server.market.detail(args['symbol'], args['date'], args['profile'])
                return self.send(200, result)
            if route.path == '/api/research':
                return self.send(200, load('research_summary.json'))
            if route.path == '/api/definitions':
                return self.send(200, {**load('definition_catalog.json'), 'execution': structure_study()})
            if route.path == '/api/structure-study':
                return self.send(200, structure_study())
            if route.path == '/api/calendar-browser':
                return self.send(200, calendar_browser(parse_browser_query(route.query)))
            if route.path == '/api/chart-history':
                parse_chart_query(route.query)
                return self.send(200, load_history())
            if route.path == '/api/chart-detail':
                args = parse_chart_query(route.query, detail=True)
                return self.send(200, chart_detail(args['symbol'], args['date'], args['profile']))
            if route.path == '/api/rule-events':
                parse_event_query(route.query)
                return self.send(200, load_rule_events())
            if route.path == '/downloads/rule-events.json':
                if route.query:
                    raise ValueError('下载地址不接受查询参数')
                return self.send(200, load_rule_events(), filename='BTC-rule-events-v0.1.0.json')
            if route.path == '/downloads/structure-study.json':
                result = HERE / 'structure_results.json'
                if not result.is_file():
                    return self.send(404, {'error': '本次检验尚未执行'})
                return self.send(200, result.read_bytes(), filename='H-BZ-STRUCT-002-results.json')
            if route.path == '/api/config':
                profiles = load('profiles.json')['profiles']
                return self.send(200, {'version': VERSION, 'profiles': [{**p, 'label': PROFILE_NAMES[p['id']]} for p in profiles],
                                      'sources': source_catalog(), 'rules': load('rules.json')['rules']})
            if route.path == '/api/calculate':
                q = parse_qs(route.query)
                if any(len(values) != 1 for values in q.values()):
                    raise ValueError('参数不能重复')
                if q.get('scenario', ['false'])[0] not in ('true', 'false'):
                    raise ValueError('scenario必须为true或false')
                return self.send(200, calculate(q.get('profile', [''])[0], q.get('at', [''])[0], q.get('scenario', ['false'])[0] == 'true'))
            if route.path == '/api/records':
                return self.send(200, {'records': self.server.store.list(), 'limit': 200})
            if route.path.startswith('/api/records/'):
                return self.send(200, self.server.store.get(route.path.split('/')[-1]))
            self.send(404, {'error': '页面不存在'})
        except MonthInteractionError as exc:
            self.send(409, {'error': str(exc)})
        except (MarketFeedError, CoinbaseFeedError, ProspectiveError, PredictionLedgerError, CoinbaseForwardError) as exc:
            self.send(exc.status_code, {'error': str(exc)})
        except CalendarAccuracyEvidenceError as exc:
            self.send(503, {'error': str(exc)})
        except (ExplorationIntegrityError, MonthStudyIntegrityError, CoinbaseStudyIntegrityError, ChartCandidatesError) as exc:
            self.send(503, {'error': str(exc)})
        except ChartIntegrityError as exc:
            self.send(503, {'error': str(exc)})
        except ValueError as exc:
            self.send(400, {'error': str(exc)})
        except Exception as exc:
            self.send(500, {'error': f'计算暂不可用（{type(exc).__name__}），请检查本地依赖。'})

    def do_POST(self):
        if not self.host_ok():
            return
        allowed = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if self.headers.get('Origin') not in allowed or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(403, {'error': '仅接受本页发出的JSON请求'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 24000:
                raise ValueError('请求体长度无效')
            body = json.loads(self.rfile.read(length))
            parsed_route = urlsplit(self.path)
            route = parsed_route.path
            if route == '/api/predictions-evaluate':
                if parsed_route.query or not isinstance(body, dict) or body:
                    raise ValueError('预测复核只接受空 JSON 对象')
                return self.send(200, self.server.predictions.evaluate())
            if route == '/api/predictions':
                if parsed_route.query:
                    raise ValueError('预测登记不接受查询参数')
                with self.server.predictions.lock:
                    conflict = candidate_root_conflict(self.server.predictions, body)
                    if conflict:
                        return self.send(409, conflict)
                    result = self.server.predictions.create(body)
                return self.send(201 if result['created'] else 200, result)
            prediction_parts = route.split('/')
            if len(prediction_parts) == 5 and prediction_parts[1:3] == ['api', 'predictions'] and prediction_parts[4] in ('revise', 'invalidate'):
                if parsed_route.query:
                    raise ValueError('预测修改不接受查询参数')
                method = self.server.predictions.revise if prediction_parts[4] == 'revise' else self.server.predictions.invalidate
                result = method(prediction_parts[3], body)
                return self.send(201 if result['created'] else 200, result)
            if route in ('/api/prospective-register', '/api/prospective-evaluate'):
                if parsed_route.query or not isinstance(body, dict) or body:
                    raise ValueError('前瞻验证只接受空 JSON 对象；规则与区间由固定协议决定')
                with CALC_LOCK:
                    result = (self.server.prospective.register() if route.endswith('-register')
                              else self.server.prospective.evaluate())
                return self.send(200, result)
            if route in ('/api/month-interaction-register', '/api/month-interaction-evaluate'):
                if parsed_route.query or not isinstance(body, dict) or body:
                    raise ValueError('事前组合操作只接受空 JSON 对象')
                with CALC_LOCK:
                    result = (self.server.month_interaction.register() if route.endswith('-register')
                              else self.server.month_interaction.evaluate())
                return self.send(200, result)
            if route in ('/api/multiframe-refresh', '/api/market-refresh'):
                return self.send(410, {'error': '已停止币安行情获取；TradingView 嵌入图只供显示，不提供本站回测数据。旧缓存仍可只读查看。'})
            if route == '/api/coinbase-refresh':
                if parsed_route.query or not isinstance(body, dict) or set(body) != {'symbol', 'days'}:
                    raise ValueError('Coinbase 更新只接受 symbol 与 days')
                return self.send(200, self.server.coinbase.refresh(body['symbol'], body['days']))
            if route == '/api/coinbase-forward-refresh-market':
                if parsed_route.query or not isinstance(body, dict) or body:
                    raise ValueError('公开计划行情更新只接受空 JSON 对象')
                return self.send(200, self.server.coinbase_forward.refresh_public_market())
            if route in ('/api/coinbase-forward-register', '/api/coinbase-forward-evaluate'):
                if parsed_route.query or not isinstance(body, dict) or body:
                    raise ValueError('前瞻计划操作只接受空 JSON 对象')
                action = self.server.coinbase_forward.register if route.endswith('-register') else self.server.coinbase_forward.evaluate
                return self.send(200, action())
            if route == '/api/records':
                return self.send(201, self.server.store.add(body))
            parts = route.split('/')
            if len(parts) == 5 and parts[1:3] == ['api', 'records'] and parts[4] == 'reviews':
                return self.send(201, self.server.store.review(parts[3], body))
            self.send(404, {'error': '操作不存在'})
        except MonthInteractionError as exc:
            self.send(409, {'error': str(exc)})
        except (MarketFeedError, CoinbaseFeedError, ProspectiveError, PredictionLedgerError, CoinbaseForwardError) as exc:
            self.send(exc.status_code, {'error': str(exc)})
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception as exc:
            self.send(500, {'error': f'记录未保存（{type(exc).__name__}）'})


def make_server(port=8766, data_dir=None):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    store_dir = Path(data_dir or HERE / 'local_data')
    server.store = Store(store_dir / 'research.sqlite3')
    server.market = MarketFeed(store_dir)
    server.coinbase = CoinbaseMarketFeed(store_dir)
    # Use only the configured data directory. Public plans are reviewed separately
    # and must never silently append to a neighboring legacy registration chain.
    server.coinbase_forward = CoinbaseForward(store_dir, server.coinbase)
    server.multiframe = MarketMultiframe(store_dir, server.market)
    server.prospective = ProspectiveStudy(store_dir, server.market)
    server.month_interaction = MonthInteractionProspective(store_dir, server.market)
    server.predictions = PredictionLedger(store_dir, ScoringMarketRouter(server.market, server.coinbase), base=HERE)
    return server


def start_coinbase_forward_worker(server):
    """Hourly local refresh/review while this server process remains open."""
    def run():
        while True:
            try:
                server.coinbase_forward.auto_tick()
            except Exception as exc:
                server.coinbase_forward.last_auto_error = type(exc).__name__ + ': ' + str(exc)
            try:
                server.coinbase_forward.auto_public_tick()
            except Exception as exc:
                server.coinbase_forward.last_public_auto_error = type(exc).__name__ + ': ' + str(exc)
            # Check shortly after 00:20 Beijing if the server started earlier that night.
            beijing = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))
            delay = max(60, (20 - beijing.minute) * 60) if beijing.hour == 0 and beijing.minute < 20 else 3600
            threading.Event().wait(delay)
    thread = threading.Thread(target=run, name='coinbase-forward-hourly', daemon=True)
    thread.start()
    return thread


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    server = make_server(args.port, args.data_dir)
    start_coinbase_forward_worker(server)
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
