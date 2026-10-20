"""Read-only hourly BTC/ETH inspection with midnight and 23:00 day boundaries.

This is a descriptive diagnostic. It does not change the sealed daily study or
the registered prospective cohort, and OHLC cannot reveal intrahour ordering.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

from calendar_adapter import calculate_calendar
from coinbase_feed import LOCAL, SYMBOLS, parse_page

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


def _hours_from_db(path, symbol, start, end):
    if not path.is_file():
        return {}
    result = {}
    with sqlite3.connect(str(path)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        pages = db.execute('''SELECT start_utc,end_utc,body FROM requests
                              WHERE symbol=? AND start_utc<? AND end_utc>?''',
                           (symbol, end.astimezone(timezone.utc).isoformat(),
                            start.astimezone(timezone.utc).isoformat())).fetchall()
        for page in pages:
            page_start = datetime.fromisoformat(page['start_utc'])
            page_end = datetime.fromisoformat(page['end_utc'])
            for stamp, bar in parse_page(page['body'], page_start, page_end).items():
                at = datetime.fromtimestamp(stamp, timezone.utc).astimezone(LOCAL)
                if start <= at < end:
                    if stamp in result and result[stamp] != bar:
                        raise ValueError('Coinbase 小时线来源冲突，不能用于日内对照')
                    result[stamp] = bar
    return result


def _session(label, start, hours):
    stamps = [int((start + i * HOUR).timestamp()) for i in range(24)]
    missing = [(start + i * HOUR).strftime('%m-%d %H:00') for i, stamp in enumerate(stamps)
               if stamp not in hours]
    result = {'label': label, 'startAt': start.isoformat(), 'endAt': (start + DAY).isoformat(),
              'completeHours': 24 - len(missing), 'missingHours': missing, 'complete': not missing}
    if missing:
        return result
    rows = [hours[stamp] for stamp in stamps]
    opened, closed = Decimal(rows[0]['open']), Decimal(rows[-1]['close'])
    high = max(Decimal(row['high']) for row in rows)
    low = min(Decimal(row['low']) for row in rows)
    high_hours = [(start + i * HOUR).strftime('%m-%d %H:00') for i, row in enumerate(rows)
                  if Decimal(row['high']) == high]
    low_hours = [(start + i * HOUR).strftime('%m-%d %H:00') for i, row in enumerate(rows)
                 if Decimal(row['low']) == low]
    pct = lambda value: round(float(value / opened * 100), 3)
    result.update({'open': str(opened), 'high': str(high), 'low': str(low), 'close': str(closed),
                   'closeReturnPct': pct(closed - opened), 'rangePct': pct(high - low),
                   'upExcursionPct': pct(high - opened), 'downExcursionPct': pct(opened - low),
                   'highHours': high_hours, 'lowHours': low_hours,
                   'extremeOrder': 'high_first' if max(high_hours) < min(low_hours) else
                                   'low_first' if max(low_hours) < min(high_hours) else 'unknown'})
    return result


def inspect_intraday(symbol, day_text, data_dirs):
    if symbol not in SYMBOLS:
        raise ValueError('仅支持 BTCUSD 和 ETHUSD')
    try:
        day = date.fromisoformat(day_text)
    except (TypeError, ValueError) as exc:
        raise ValueError('日期应为 YYYY-MM-DD') from exc
    if day.isoformat() != day_text or not date(2022, 1, 2) <= day <= datetime.now(LOCAL).date() - DAY:
        raise ValueError('仅可查看已结束且有历史源的北京时间日')
    midnight = datetime.combine(day, time(), LOCAL)
    zi_start = midnight - HOUR
    end = midnight + DAY
    hours = {}
    source_count = 0
    for directory in dict.fromkeys(Path(path) for path in data_dirs):
        path = directory / 'coinbase_market.sqlite3'
        if path.is_file():
            source_count += 1
            for stamp, bar in _hours_from_db(path, symbol, zi_start, end).items():
                if stamp in hours and hours[stamp] != bar:
                    raise ValueError('两个本地行情缓存的小时线冲突，不能用于日内对照')
                hours[stamp] = bar
    pillar = calculate_calendar((midnight + timedelta(hours=12)).isoformat())['pillars']['day']
    late_zi_current_pillar = calculate_calendar((zi_start + timedelta(minutes=30)).isoformat())['pillars']['day']
    table = []
    for i in range(25):
        at = zi_start + i * HOUR
        row = hours.get(int(at.timestamp()))
        table.append({'at': at.isoformat(), 'localHour': at.strftime('%m-%d %H:00'),
                      'inZiSession': i < 24, 'inMidnightSession': i >= 1,
                      'open': row['open'] if row else None, 'high': row['high'] if row else None,
                      'low': row['low'] if row else None, 'close': row['close'] if row else None})
    return {'symbol': symbol, 'date': day.isoformat(), 'dayPillar': pillar,
            'lateZiCurrentPillar': late_zi_current_pillar, 'lateZiAlternativePillar': pillar,
            'market': 'Coinbase Exchange USD spot', 'timezone': 'UTC+8',
            'calendarPolicy': '研究站当前日柱为 00:00 换日；23:00 为并列假设，未改历史规则',
            'sourceCount': source_count, 'sourceType': 'cached Coinbase hourly responses',
            'retrospective': True, 'marketValidatedPrediction': False,
            'midnight': _session('00:00 换日', midnight, hours),
            'ziHour': _session('23:00 换日', zi_start, hours), 'hours': table,
            'limits': ['小时 OHLC 不能确定同一小时内高点和低点出现的先后。',
                       '高低点相对开盘的幅度是事后路径描述，不等于可交易收益。',
                       '缺少任一小时则该口径的完整日内指标不计算。']}
