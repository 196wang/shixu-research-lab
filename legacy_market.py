"""Read official BTCUSDT spot hourly archives, checksum, and aggregate UTC+8 days."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, date, time, timezone, timedelta
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import urllib.error
import http.client
import time as time_module
import zipfile

from calendar_adapter import day_label, LOCAL, POLICY

HERE = Path(__file__).resolve().parent
RAW = HERE / 'market_raw'
BASE = 'https://data.binance.vision/data/spot'
START, END = date(2020, 1, 1), date(2026, 9, 26)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fetch_bytes(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Local-research/0.4'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time_module.sleep(attempt + 1)


def jobs():
    result = []
    y, m = 2019, 12
    while (y, m) <= (2026, 8):
        result.append(('monthly', f'{y:04}-{m:02}'))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    result += [('daily', f'2026-09-{d:02}') for d in range(1, 27)]
    return result


def fetch_job(job):
    kind, period = job
    filename = f'BTCUSDT-1h-{period}.zip'
    url = f'{BASE}/{kind}/klines/BTCUSDT/1h/{filename}'
    path = RAW / filename
    checksum_path = RAW / (filename + '.CHECKSUM')
    record = {'period': period, 'archiveType': kind, 'filename': filename, 'url': url,
              'checksumUrl': url + '.CHECKSUM', 'fetchedAtUtc': datetime.now(timezone.utc).isoformat()}
    try:
        cached = path.exists() and checksum_path.exists()
        checksum = checksum_path.read_bytes() if cached else fetch_bytes(url + '.CHECKSUM')
        expected = checksum.decode('utf-8').split()[0].lower()
        if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('invalid checksum text')
        data = path.read_bytes() if cached else fetch_bytes(url)
        if sha(data) != expected:
            raise ValueError('SHA256 mismatch')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if z.testzip() is not None:
                raise ValueError('ZIP CRC mismatch')
        if not cached:
            path.write_bytes(data)
            checksum_path.write_bytes(checksum)
        record.update(status='verified', sha256=expected, bytes=len(data), reusedCache=cached)
    except Exception as exc:
        record.update(status='unavailable', error=f'{type(exc).__name__}: {exc}')
    return record


def normalize_hour(row, filename, row_number):
    if len(row) != 12:
        raise ValueError('unexpected columns')
    raw_open, raw_close = int(row[0]), int(row[6])
    unit = 1000000 if raw_open >= 100000000000000 else 1000
    if raw_open < 100000000000 or raw_open % (3600 * unit):
        raise ValueError('invalid timestamp or hourly boundary')
    if raw_close != raw_open + 3600 * unit - 1:
        raise ValueError('invalid hourly close timestamp')
    opened = raw_open // unit
    expected_unit = 1000000 if opened >= 1735689600 else 1000
    if unit != expected_unit:
        raise ValueError('timestamp unit differs from documented 2025 transition')
    prices = [Decimal(x) for x in row[1:5]]
    o, h, low, c = prices
    if any(not p.is_finite() or p <= 0 for p in prices) or not low <= min(o, c) <= max(o, c) <= h:
        raise ValueError('invalid OHLC')
    if opened + 3600 > int(datetime.now(timezone.utc).timestamp()):
        raise ValueError('unclosed hour')
    return {'openTimeUtcSeconds': opened, 'rawOpenTimestamp': raw_open, 'rawCloseTimestamp': raw_close,
            'openTimeUtc': datetime.fromtimestamp(opened, timezone.utc).isoformat(),
            'closeTimeUtc': datetime.fromtimestamp(raw_close / unit, timezone.utc).isoformat(timespec='microseconds'),
            'open': str(o), 'high': str(h), 'low': str(low), 'close': str(c),
            'source': filename, 'sourceRow': row_number, 'sourceTimestampUnit': 'microseconds' if unit == 1000000 else 'milliseconds'}


def aggregate_day(day, hours):
    opened = int(datetime.combine(day, time(), LOCAL).timestamp())
    expected = [opened + i * 3600 for i in range(24)]
    present = [hours[t] for t in expected if t in hours]
    errors = []
    if len(present) != 24:
        errors.append(f'missing_hours:{24-len(present)}')
    for hour in present:
        errors.extend(hour.get('errors', []))
    row = {'date': day.isoformat(), 'dayGanzhi': day_label(day.isoformat()), 'hourCount': len(present),
           'sourceErrors': sorted(set(errors)),
           'sourceArchives': sorted({h['source'] for h in present}),
           'openTimeUtc': datetime.fromtimestamp(opened, timezone.utc).isoformat(),
           'endExclusiveUtc': datetime.fromtimestamp(opened + 86400, timezone.utc).isoformat()}
    if len(present) == 24 and not errors:
        row.update(open=present[0]['open'], close=present[-1]['close'],
                   high=str(max(Decimal(h['high']) for h in present)),
                   low=str(min(Decimal(h['low']) for h in present)))
    return row


def build_daily(manifest):
    hours, parse_errors, duplicates = {}, [], []
    for item in manifest['files']:
        if item['status'] != 'verified':
            continue
        data = (RAW / item['filename']).read_bytes()
        if sha(data) != item['sha256']:
            raise RuntimeError('local archive changed after verification')
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = [n for n in z.namelist() if n.endswith('.csv')]
            if len(members) != 1:
                raise RuntimeError('unexpected archive members')
            for n, row in enumerate(csv.reader(io.StringIO(z.read(members[0]).decode('utf-8'))), 1):
                try:
                    hour = normalize_hour(row, item['filename'], n)
                    stamp = hour['openTimeUtcSeconds']
                    if stamp in hours:
                        duplicates.append({'openTimeUtcSeconds': stamp, 'source': item['filename'], 'row': n})
                        hours[stamp].setdefault('errors', []).append('duplicate_hour')
                    else:
                        hours[stamp] = hour
                except (ValueError, ArithmeticError) as exc:
                    parse_errors.append({'source': item['filename'], 'row': n, 'error': str(exc)})
    rows = []
    day = START
    while day <= END:
        rows.append(aggregate_day(day, hours))
        day += timedelta(days=1)
    all_valid = not parse_errors and not duplicates and all(r['hourCount'] == 24 and not r['sourceErrors'] for r in rows)
    # Individual valid days may still be examined when the complete requested span has gaps.
    audit = {'checkedAtUtc': datetime.now(timezone.utc).isoformat(), 'period': [str(START), str(END)],
             'hoursRead': len(hours), 'daysExpected': len(rows),
             'completeDays': sum(r['hourCount'] == 24 and not r['sourceErrors'] for r in rows),
             'parseErrors': parse_errors, 'duplicateHours': duplicates, 'entireSpanComplete': all_valid,
             'validationMethodImplemented': '24 exact consecutive hourly open times; documented units; SHA256+CRC; OHLC; closed bars',
             'missingDays': [{'date': r['date'], 'errors': r['sourceErrors']} for r in rows if r['sourceErrors']]}
    metadata = {'timezone': 'Asia/Shanghai', 'symbol': 'BTCUSDT', 'marketType': 'spot', 'exchange': 'Binance',
                'interval': '1h', 'calendarValidated': False, 'hourlyAggregationValidated': all_valid,
                'registered': False, 'scorerFixturesValidated': False, 'researchDataType': 'real_market',
                'calendarEngine': POLICY, 'rawHashes': [{'filename': x['filename'], 'sha256': x['sha256']} for x in manifest['files'] if x['status'] == 'verified'],
                'knownHistory': '2020–2024探索；2025–2026已读案例，不能称独立样本外'}
    write(HERE / 'daily_input.json', {'metadata': metadata, 'dailyRows': rows})
    write(HERE / 'market_validation.json', audit)
    with (HERE / 'hourly_normalized.jsonl').open('w', encoding='utf-8') as stream:
        for stamp in sorted(hours):
            stream.write(json.dumps(hours[stamp], ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in audit.items() if k not in ('missingDays', 'parseErrors', 'duplicateHours')}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-only', action='store_true')
    parser.add_argument('--aggregate-only', action='store_true')
    args = parser.parse_args()
    RAW.mkdir(exist_ok=True)
    if not args.aggregate_only:
        manifest = {'source': BASE, 'symbol': 'BTCUSDT', 'marketType': 'spot', 'interval': '1h', 'files': []}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(fetch_job, job) for job in jobs()]
            for i, future in enumerate(as_completed(futures), 1):
                manifest['files'].append(future.result())
                if i % 15 == 0 or i == len(futures):
                    write(HERE / 'market_downloads.json', manifest)
                    print(json.dumps({'finished': i, 'total': len(futures), 'verified': sum(x['status'] == 'verified' for x in manifest['files'])}), flush=True)
        manifest['files'].sort(key=lambda x: x['period'])
        write(HERE / 'market_downloads.json', manifest)
    else:
        manifest = json.loads((HERE / 'market_downloads.json').read_text(encoding='utf-8'))
    if not args.download_only:
        build_daily(manifest)


if __name__ == '__main__':
    main()
