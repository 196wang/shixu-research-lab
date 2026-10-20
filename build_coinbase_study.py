"""Freeze source and fixed protocol before retrospective Coinbase study run."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import uuid

from coinbase_feed import CoinbaseMarketFeed, local_open, ms
from coinbase_study import HERE, build, load_protocol
from gann_timing import snapshot_hash

DATA = HERE.parent / '时序研究室_v0.5' / 'local_data'
OUT = HERE / 'coinbase_study_v21' / 'run-001'
SOURCES = ('build_coinbase_study.py', 'coinbase_study.py', 'coinbase_study_protocol.json',
           'coinbase_feed.py', 'exploration_generators.py', 'month_study.py',
           'calendar_adapter.py', 'calendar_browser.py', 'astro_events.py', 'gann_timing.py')


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False), encoding='utf-8')


def run(data_dir=DATA, output=OUT):
    protocol = load_protocol()
    if Path(output).exists():
        raise FileExistsError('固定回测 run-001 已存在，拒绝覆盖')
    generated = datetime.now(timezone.utc)
    feed = CoinbaseMarketFeed(data_dir)
    snapshots = {symbol: feed._snapshot(symbol) for symbol in protocol['symbols']}
    source_start, source_end = date.fromisoformat(protocol['sourceStartDate']), date.fromisoformat(protocol['endDate'])
    span_days = (source_end - source_start).days + 1
    coverage = {}
    for symbol, (_, candles, gaps) in snapshots.items():
        absent = []
        for offset in range(span_days):
            day = source_start + timedelta(days=offset)
            key = ms(local_open(day))
            if key not in candles or candles[key]['conflicted'] or key in gaps:
                absent.append(day.isoformat())
        if len(absent) > span_days * 0.01:
            raise ValueError(f'{symbol} 历史覆盖不足：{len(absent)}/{span_days} 日缺失，停止固定回测')
        coverage[symbol] = {'spanDays': span_days, 'missingDays': len(absent), 'missingDates': absent}
    raw = []
    with feed.connect() as db:
        for row in db.execute('SELECT id,symbol,start_utc,end_utc,http_status,body_sha256 FROM requests ORDER BY symbol,start_utc,id'):
            raw.append(dict(row))
    registration = {'studyId': protocol['studyId'], 'capturedAtUtc': generated.isoformat(),
                    'protocolSha256': snapshot_hash(protocol), 'coverage': coverage,
                    'snapshotSha256': {s: snapshot_hash(v) for s, v in snapshots.items()},
                    'rawRequestCount': len(raw), 'rawRequestManifestSha256': snapshot_hash(raw),
                    'implementationSha256': {n: hashlib.sha256((HERE / n).read_bytes()).hexdigest() for n in SOURCES},
                    'registeredPrediction': False, 'independentBlindSample': False}
    temp = Path(output).parent / ('building-' + uuid.uuid4().hex)
    temp.mkdir(parents=True, exist_ok=False)
    write_json(temp / 'protocol.json', protocol)
    write_json(temp / 'registration.json', registration)
    write_json(temp / 'snapshot.json', snapshots)
    write_json(temp / 'raw-request-manifest.json', raw)
    report, details = build(snapshots, protocol, generated)
    write_json(temp / 'report.json', report)
    write_json(temp / 'details.json', details)
    write_json(temp / 'manifest.json', {'studyId': protocol['studyId'], 'capturedAtUtc': generated.isoformat(),
               'fileSha256': {name: hashlib.sha256((temp / name).read_bytes()).hexdigest()
                              for name in ('protocol.json', 'registration.json', 'snapshot.json',
                                           'raw-request-manifest.json', 'report.json', 'details.json')}})
    temp.rename(output)
    return report


if __name__ == '__main__':
    result = run()
    print(json.dumps({'studyId': result['studyId'], 'generatedAtUtc': result['generatedAtUtc'],
                      'symbols': [s['symbol'] for s in result['symbols']]}, ensure_ascii=False))
