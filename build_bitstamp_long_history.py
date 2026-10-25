"""Fetch auditable Bitstamp BTC/USD hourly history for 2013-era backtests.

Raw exchange responses stay in ignored local_data/. The public repository
contains only this reproducible collector and derived research summaries.
"""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import time as clock
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'local_data' / 'bitstamp_btcusd_hourly_2013_2026_v1'
RAW = OUT / 'raw_chunks'
FIRST_DAY, LAST_DAY = date(2013, 1, 1), date(2026, 10, 16)
LOCAL = timezone(timedelta(hours=8))
UTC = timezone.utc
STEP, LIMIT = 3600, 1000
FIRST_HOUR = int(datetime.combine(FIRST_DAY, time(), LOCAL).timestamp())
LAST_HOUR = int((datetime.combine(LAST_DAY + timedelta(days=1), time(), LOCAL) - timedelta(hours=1)).timestamp())
URL = 'https://www.bitstamp.net/api/v2/ohlc/btcusd/'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def checked_rows(body, first, last):
    value = json.loads(body)
    if value.get('data', {}).get('pair') != 'BTC/USD':
        raise ValueError('unexpected Bitstamp pair')
    rows = value['data']['ohlc']
    expected = list(range(first, last + STEP, STEP))
    if [int(row['timestamp']) for row in rows] != expected:
        raise ValueError(f'noncontinuous response: {first}..{last}, got {len(rows)}')
    for row in rows:
        opened, high, low, closed = (Decimal(row[key]) for key in ('open', 'high', 'low', 'close'))
        volume = Decimal(row['volume'])
        if not (0 < low <= min(opened, closed) <= max(opened, closed) <= high and volume >= 0):
            raise ValueError('invalid OHLC/volume')
    return rows


def fetch_chunk(first, last, ordinal):
    params = urllib.parse.urlencode({'step': STEP, 'limit': (last - first) // STEP + 1,
                                     'start': first - STEP, 'end': last})
    url = URL + '?' + params
    path = RAW / f'{ordinal:04d}.json'
    if path.exists():
        body = path.read_bytes()
        return checked_rows(body, first, last), body, url, True
    last_error = None
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'ShixuResearch/1.0 historical-read-only'})
            with urllib.request.urlopen(request, timeout=35) as response:
                body = response.read()
            rows = checked_rows(body, first, last)
            path.write_bytes(body)
            return rows, body, url, False
        except Exception as error:
            last_error = error
            clock.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f'Bitstamp chunk {ordinal} failed: {last_error}')


def build():
    RAW.mkdir(parents=True, exist_ok=True)
    chunks, hourly = [], {}
    ordinal, first = 0, FIRST_HOUR
    while first <= LAST_HOUR:
        last = min(first + (LIMIT - 1) * STEP, LAST_HOUR)
        rows, body, url, cached = fetch_chunk(first, last, ordinal)
        chunks.append({'ordinal': ordinal, 'firstUtc': datetime.fromtimestamp(first, UTC).isoformat(),
                       'lastUtc': datetime.fromtimestamp(last, UTC).isoformat(),
                       'url': url, 'responseSha256': sha(body), 'rowCount': len(rows)})
        for row in rows:
            ts = int(row['timestamp'])
            if ts in hourly:
                raise ValueError('duplicate hour')
            hourly[ts] = row
        if ordinal % 10 == 0 or last == LAST_HOUR:
            print(f'chunk {ordinal + 1}: through {datetime.fromtimestamp(last, UTC).date()} '
                  f'({"cached" if cached else "downloaded"})', flush=True)
        ordinal += 1
        first = last + STEP
        if not cached:
            clock.sleep(0.15)

    days = {}
    for offset in range((LAST_DAY - FIRST_DAY).days + 1):
        day = FIRST_DAY + timedelta(days=offset)
        first_ts = int(datetime.combine(day, time(), LOCAL).timestamp())
        rows = [hourly.get(first_ts + i * STEP) for i in range(24)]
        available = [row for row in rows if row is not None]
        if len(available) != 24:
            days[day.isoformat()] = {'quality': 'missing_hours', 'hourCount': len(available)}
            continue
        active = sum(Decimal(row['volume']) > 0 for row in available)
        days[day.isoformat()] = {
            'quality': 'strict' if active == 24 else 'thin', 'hourCount': 24,
            'activeHourCount': active, 'open': available[0]['open'],
            'high': str(max(Decimal(row['high']) for row in available)),
            'low': str(min(Decimal(row['low']) for row in available)),
            'close': available[-1]['close'],
            'volume': str(sum(Decimal(row['volume']) for row in available)),
            'sourceHourSha256': sha(canonical(available)),
        }
    captured = datetime.now(UTC).isoformat()
    manifest = {'provider': 'Bitstamp public OHLC API', 'market': 'BTC/USD spot',
                'frequency': '1h', 'timezoneOfOutputDays': 'Asia/Shanghai',
                'firstDay': FIRST_DAY.isoformat(), 'lastDay': LAST_DAY.isoformat(),
                'capturedAtUtc': captured, 'chunks': chunks,
                'hourCount': len(hourly), 'dayCount': len(days),
                'strictDays': sum(row['quality'] == 'strict' for row in days.values()),
                'thinDays': sum(row['quality'] == 'thin' for row in days.values()),
                'missingDays': sum(row['quality'] == 'missing_hours' for row in days.values()),
                'zeroVolumeHours': sum(Decimal(row['volume']) == 0 for row in hourly.values()),
                'rawDataLocalOnly': True}
    daily_body = json.dumps(days, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    (OUT / 'daily.json').write_bytes(daily_body)
    manifest['dailySha256'] = sha(daily_body)
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    result = build()
    print(json.dumps({key: result[key] for key in ('hourCount', 'dayCount', 'strictDays', 'thinDays',
                                                   'missingDays', 'zeroVolumeHours', 'dailySha256')}, indent=2))
