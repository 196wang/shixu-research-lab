"""Local historical BTC candles with fixed-calendar labels; no market execution."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import parse_qs

from bazi_engine import BRANCH_ELEMENT, stem_info
from bazi_features import calculate_features
from calendar_adapter import LOCAL, POLICY, calculate_calendar, calculate_relations
from calendar_browser import terms_in_year

HERE = Path(__file__).resolve().parent
VERSION = '0.10.0'
SYMBOL = 'BTCUSDT'
PROFILE = 'BTC-BZ-V01'
START = date(2020, 1, 1)
END = date(2026, 9, 26)
FROZEN_DAILY_SHA256 = '182a156b7aed3988d376f2656773cfb93caa8f1e66b613787deabdb7e0b6c0cc'
FROZEN_FILES = ('daily_input.json', 'calendar_adapter.py', 'bazi_engine.py', 'bazi_features.py',
                'profiles.json', 'market_recovery.json', 'checks/data_audit.json')
BUILD_FILES = ('calendar_browser.py', 'historical_chart.py', 'build_chart_history.py')
PRICE_KEYS = ('open', 'high', 'low', 'close')


class ChartIntegrityError(RuntimeError):
    """Source or generated-file verification failed: do not silently serve candles."""


def file_sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ChartIntegrityError(f'历史K线依赖文件不可读：{Path(path).name}') from exc


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ChartIntegrityError(f'历史K线文件不可读或格式错误：{Path(path).name}') from exc


def content_sha(document):
    return hashlib.sha256(json.dumps({k: v for k, v in document.items() if k != 'contentSha256'},
                                    sort_keys=True, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def verify_sources(base=HERE):
    base = Path(base)
    registration = read_json(base / 'execution_registration.json')
    registered = registration.get('inputHashes', {})
    if registered.get('daily_input.json') != FROZEN_DAILY_SHA256:
        raise ChartIntegrityError('历史K线注册中的行情源哈希不符合本版本固定数据')
    hashes = {}
    for name in FROZEN_FILES:
        actual = file_sha(base / name)
        if actual != registered.get(name):
            raise ChartIntegrityError(f'历史K线源文件哈希不匹配：{name}；请恢复原文件后重建')
        hashes[name] = actual
    for name in BUILD_FILES:
        hashes[name] = file_sha(base / name)
    return hashes


def parse_chart_query(query, detail=False):
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    expected = {'symbol', 'date', 'profile'} if detail else {'symbol'}
    if any(len(v) != 1 for v in values.values()):
        raise ValueError('参数不能重复')
    if set(values) != expected:
        raise ValueError('历史K线参数不完整或含未知参数')
    args = {key: val[0] for key, val in values.items()}
    if args['symbol'] != SYMBOL:
        raise ValueError('本地历史行情仅有BTCUSDT现货，未提供该交易对')
    if detail:
        if args['profile'] != PROFILE:
            raise ValueError('BTCUSDT历史图表仅支持匹配的BTC四柱版本01')
        parse_chart_date(args['date'])
    return args


def parse_chart_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('日期必须为YYYY-MM-DD')
    value = date.fromisoformat(value)
    if not START <= value <= END:
        raise ValueError(f'本地历史行情仅覆盖{START}至{END}')
    return value


def element_labels(pillars):
    return {key: {'stem': stem_info(value[0])['element'], 'branch': BRANCH_ELEMENT[value[1]]}
            for key, value in pillars.items()}


def convert_row(raw):
    """Validate the frozen aggregate without filling incomplete days or computing returns."""
    day = parse_chart_date(raw.get('date'))
    local = datetime(day.year, day.month, day.day, tzinfo=LOCAL)
    end = local + timedelta(days=1)
    start_utc, end_utc = local.astimezone(timezone.utc), end.astimezone(timezone.utc)
    if raw.get('openTimeUtc') != start_utc.isoformat() or raw.get('endExclusiveUtc') != end_utc.isoformat():
        raise ChartIntegrityError(f'{day}行情日界不是UTC+8的00:00–次日00:00')
    calendar = calculate_calendar(local.isoformat())
    if raw.get('dayGanzhi') != calendar['pillars']['day']:
        raise ChartIntegrityError(f'{day}源日柱与固定历法不一致')
    source_errors = raw.get('sourceErrors', [])
    if not isinstance(source_errors, list) or any(not isinstance(e, str) for e in source_errors):
        raise ChartIntegrityError(f'{day}源错误记录格式不正确')
    errors = list(source_errors)
    if type(raw.get('hourCount')) is not int or raw['hourCount'] != 24:
        errors.append('hour_count_not_24')
    prices = {}
    for key in PRICE_KEYS:
        value = raw.get(key)
        try:
            if value is None or isinstance(value, (bool, list, dict)):
                raise ValueError('missing_or_invalid')
            number = Decimal(str(value))
            if not number.is_finite() or number <= 0 or not math.isfinite(float(number)):
                raise ValueError('not_positive_finite')
            prices[key] = number
        except (ValueError, InvalidOperation, OverflowError):
            errors.append(f'{key}:missing_or_invalid_price')
    if len(prices) == 4 and not prices['low'] <= min(prices['open'], prices['close']) <= max(prices['open'], prices['close']) <= prices['high']:
        errors.append('invalid_ohlc_order')
    complete = not errors
    sources = raw.get('sourceArchives')
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
        raise ChartIntegrityError(f'{day}缺少源文件引用')
    return {'date': day.isoformat(), 'openTimeUtc': start_utc.isoformat(),
            'closeTimeUtcExclusive': end_utc.isoformat(), 'atLocal': local.isoformat(),
            **{key: float(prices[key]) if complete else None for key in PRICE_KEYS},
            'volume': None, 'isClosed': True, 'complete': complete,
            'hourCount': raw.get('hourCount'), 'errors': errors, 'sourceRefs': list(sources),
            'pillars': calendar['pillars'], 'elements': element_labels(calendar['pillars'])}


def build_transitions(start=START, end=END):
    values = []
    for year in range(start.year, end.year + 1):
        for name, boundary in terms_in_year(year):
            if not start <= boundary.date() <= end:
                continue
            before = calculate_calendar((boundary - timedelta(seconds=1)).isoformat())['pillars']
            after = calculate_calendar(boundary.isoformat())['pillars']
            values.append({'termName': name, 'atLocal': boundary.isoformat(),
                           'atUtc': boundary.astimezone(timezone.utc).isoformat(),
                           'changesYear': before['year'] != after['year'],
                           'changesMonth': before['month'] != after['month'],
                           'beforePillars': before, 'afterPillars': after,
                           'precisionNote': '固定库计算到秒；外部核对仅到分钟，临界秒未独立核验'})
    return sorted(values, key=lambda item: item['atLocal'])


def build_history(base=HERE):
    base = Path(base)
    hashes = verify_sources(base)
    document = read_json(base / 'daily_input.json')
    meta = document.get('metadata', {})
    for field, value in {'symbol': SYMBOL, 'marketType': 'spot', 'exchange': 'Binance',
                         'interval': '1h', 'timezone': 'Asia/Shanghai'}.items():
        if meta.get(field) != value:
            raise ChartIntegrityError(f'行情元数据不匹配：{field}')
    source_rows = document.get('dailyRows')
    total = (END - START).days + 1
    if not isinstance(source_rows, list) or len(source_rows) != total:
        raise ChartIntegrityError('历史日期数量不完整，禁止压缩或补造日期')
    rows = []
    for index, raw in enumerate(source_rows):
        expected = (START + timedelta(days=index)).isoformat()
        if raw.get('date') != expected:
            raise ChartIntegrityError(f'历史日期顺序或连续性错误：应为{expected}')
        rows.append(convert_row(raw))
    missing = [r['date'] for r in rows if not r['complete']]
    recovery = read_json(base / 'market_recovery.json')
    audit = read_json(base / 'checks/data_audit.json')
    if len(missing) != 18 or missing != [r['date'] for r in recovery['missingDays']]:
        raise ChartIntegrityError('历史缺口与原有18日掩码不一致')
    if audit.get('dailySha256') != hashes['daily_input.json'] or audit.get('concreteBlockers'):
        raise ChartIntegrityError('原数据审查证据与本文件不匹配')
    artifact = {'schemaVersion': 1, 'version': VERSION, 'symbol': SYMBOL, 'venue': 'Binance',
                'marketType': 'spot', 'interval': '1d', 'timezone': 'UTC+8', 'labelAt': 'dayStart',
                'range': {'start': START.isoformat(), 'end': END.isoformat()},
                'coverage': {'total': total, 'complete': total - len(missing), 'missing': len(missing)},
                'source': {'title': 'Binance现货1小时档案及已留档的同源修复数据，按UTC+8聚合日线',
                           'files': list(hashes), 'inputSha256': hashes['daily_input.json'],
                           'inputHashes': hashes, 'asOf': END.isoformat(),
                           'sourceInterval': meta['interval'], 'datasetVersion': meta.get('datasetVersion'),
                           'recoveryCheckedAtUtc': recovery['checkedAtUtc'],
                           'metadataFlagsPreserved': {k: meta.get(k) for k in ('hourlyAggregationValidated', 'registered', 'calendarValidated')},
                           'metadataInterpretation': 'hourlyAggregationValidated=false表示整段仍有缺口/冲突；registered=false是导出时标志，外部注册哈希仍有效。',
                           'priorAudit': {'file': 'checks/data_audit.json', 'auditedAtUtc': audit['auditedAtUtc'],
                                          'reconstructedDays': audit['sourceReconstruction']['dailyRowsReconstructedAndCompared'],
                                          'archiveCount': audit['sourceReconstruction']['archiveCountSha256AndCrcVerified'],
                                          'recoveredHours': len(recovery['addedHours']),
                                          'performedInThisBuild': False},
                           'thisBuildChecks': ['固定输入哈希', '每日UTC+8起止', '24小时计数与缺口掩码', 'OHLC正数及范围', '源日柱与固定库一致'],
                           'independentMarketRevalidationThisBuild': False},
                'rows': rows, 'transitions': build_transitions(), 'calendarPolicy': POLICY,
                'marketDirection': None, 'marketValidated': False, 'liveMarketData': False,
                'limits': ['仅展示已冻结的BTCUSDT历史现货日线，没有实时行情。',
                           '18个不完整日保留时间格且OHLC为空；没有补价或跨缺口连线。',
                           '源日线未保存成交量，因此volume为null。',
                           '干支是北京时间每日00:00快照；日内交节另列准确计算时刻。',
                           '五行颜色与规则匹配不表示涨跌预测；本次没有重做研究评分。',
                           '本轮复用既有源档案审查证据，并未重新独立核验全部交易所小时原始数据。']}
    if hashes != verify_sources(base):
        raise ChartIntegrityError('建库过程中源文件发生变化，结果未保存')
    artifact['contentSha256'] = content_sha(artifact)
    return artifact


@lru_cache(maxsize=2)
def _decode_history(text):
    try:
        document = json.loads(text)
        if content_sha(document) != document.get('contentSha256'):
            raise ChartIntegrityError('历史K线派生文件内容哈希不匹配；请重建chart_history.json')
        if document.get('symbol') != SYMBOL or document.get('interval') != '1d':
            raise ChartIntegrityError('历史K线派生文件类型不匹配')
        return document
    except (ValueError, TypeError) as exc:
        raise ChartIntegrityError('历史K线派生文件格式错误；请重建chart_history.json') from exc


def load_history(base=HERE):
    base = Path(base)
    hashes = verify_sources(base)
    try:
        data = _decode_history((base / 'chart_history.json').read_text(encoding='utf-8'))
    except OSError as exc:
        raise ChartIntegrityError('缺少chart_history.json，请先运行build_chart_history.py') from exc
    if data.get('source', {}).get('inputHashes') != hashes:
        raise ChartIntegrityError('历史K线源文件或构建代码已变化，请重建chart_history.json')
    return data


def chart_detail(symbol, day, profile, base=HERE):
    if symbol != SYMBOL or profile != PROFILE:
        raise ValueError('历史图表仅支持BTCUSDT与BTC四柱版本01的匹配组合')
    target = parse_chart_date(day)
    data = load_history(base)
    row = data['rows'][(target - START).days]
    if row['date'] != day:
        raise ChartIntegrityError('历史K线日期索引错误')
    relations = calculate_relations(profile, row['atLocal'])
    return {'symbol': symbol, 'profile': profile, 'date': day, 'atLocal': row['atLocal'],
            'row': deepcopy(row), 'calendar': relations['calendar'],
            'features': calculate_features(relations['relationships'], relations['calendar']),
            'transitions': [deepcopy(t) for t in data['transitions'] if t['atLocal'][:10] == day],
            'marketDirection': None, 'marketValidated': False}
