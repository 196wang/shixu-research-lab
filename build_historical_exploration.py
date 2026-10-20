"""Freeze source/protocol/code BEFORE one historical exploration; no cache writes."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3

HERE = Path(__file__).resolve().parent

def write_new(path, data):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def capture(db_path):
    with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        symbol = 'BTCUSDT'
        state = dict(db.execute('SELECT * FROM symbol_state WHERE symbol=?', (symbol,)).fetchone())
        candles = {r['open_ms']: dict(r) for r in db.execute('''SELECT c.*,r.body_sha256 FROM candles c
                 LEFT JOIN feed_requests r ON c.request_id=r.id WHERE c.symbol=? ORDER BY c.open_ms''', (symbol,))}
        gaps = {r['open_ms']: json.loads(r['reasons']) for r in db.execute('SELECT * FROM feed_gaps WHERE symbol=?', (symbol,))}
    return state, candles, gaps

def inputs():
    names = ['historical_exploration.py', 'historical_exploration_protocol.json',
             'build_historical_exploration.py', 'prediction_scoring.py', 'gann_timing.py',
             'astro_events.py', 'astro_adapter.py', 'indian_adapter.py', 'indian_math.py', 'calendar_adapter.py',
             'bazi_engine.py', 'bazi_features.py', 'rule_events.py', 'historical_chart.py',
             'market_feed.py', 'calendar_browser.py', 'profiles.json', 'rule_event_catalog.json',
             '来源溯源/v0.14/江恩计算规范.md']
    for p in HERE.glob('exploration_*.py'):
        names.append(p.name)
    for p in (HERE/'vendor').rglob('*'):
        if p.is_file() and (p.suffix in {'.py', '.pyd', '.dll', '.dat', '.se1'} or p.name == 'METADATA'):
            names.append(p.relative_to(HERE).as_posix())
    return {n: sha(HERE/n) for n in sorted(set(names))}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--run', default='run-001')
    parser.add_argument('--resume', action='store_true', help='Use an existing uncompleted freeze; code must be identical.')
    args = parser.parse_args()
    if not re.fullmatch(r'run-[0-9]{3}', args.run):
        raise SystemExit('run must be run-NNN')
    from historical_exploration import build_report, load_protocol
    from gann_timing import snapshot_hash
    parent = HERE/'exploration_v16'
    target = parent/args.run
    index = parent/'index.json'
    if index.exists():
        raise SystemExit('A published exploration already exists. Preserve it and use a new release.')
    if args.resume:
        registration = json.loads((target/'registration.json').read_text(encoding='utf-8'))
        assert inputs() == registration['inputHashes'], 'Code changed since freeze'
        assert sha(target/'snapshot.json') == registration['snapshotFileSha256']
        assert sha(target/'protocol.json') == registration['protocolFileSha256']
        packed = json.loads((target/'snapshot.json').read_text(encoding='utf-8'))
        snapshot = (packed['state'], {int(k):v for k,v in packed['candles'].items()}, {int(k):v for k,v in packed['gaps'].items()})
        protocol = json.loads((target/'protocol.json').read_text(encoding='utf-8'))
        generated = datetime.fromisoformat(registration['capturedAtUtc'])
        assert not (target/'report.json').exists(), 'Report already exists'
    else:
        target.mkdir(parents=True, exist_ok=False)
        protocol = load_protocol()
        snapshot = capture(args.data_dir/'market.sqlite3')
        generated = datetime.now(timezone.utc)
        packed = {'state':snapshot[0], 'candles':snapshot[1], 'gaps':snapshot[2],
                  'capturedAtUtc':generated.isoformat(), 'note':'Current downloaded historical data; not vintage point-in-time archive.'}
        write_new(target/'snapshot.json', packed)
        write_new(target/'protocol.json', protocol)
        registration = {'studyId':'HX-BTC-001', 'version':'0.16.0', 'run':args.run,
                        'frozenAtUtc':datetime.now(timezone.utc).isoformat(), 'capturedAtUtc':generated.isoformat(),
                        'historicalExploration':True, 'registeredPrediction':False,
                        'snapshotFileSha256':sha(target/'snapshot.json'), 'snapshotSha256':snapshot_hash(snapshot),
                        'protocolFileSha256':sha(target/'protocol.json'), 'inputHashes':inputs()}
        write_new(target/'registration.json', registration)
    print(json.dumps({'stage':'frozen','run':args.run,'files':str(target),'inputs':len(registration['inputHashes'])},ensure_ascii=False),flush=True)
    def progress(value):
        print(json.dumps(value,ensure_ascii=False),flush=True)
    report = build_report(snapshot, protocol, generated, progress=progress)
    if inputs() != registration['inputHashes']:
        raise RuntimeError('Code changed during calculation; refusing to publish mixed result')
    details = report.pop('details')
    report['registration'] = {'frozenAtUtc':registration['frozenAtUtc'], 'sha256':sha(target/'registration.json'), 'run':args.run}
    report['files'] = {'protocol':'/api/exploration-protocol', 'details':'/api/exploration-details',
                       'snapshot':'/api/exploration-snapshot','registration':'/api/exploration-registration'}
    write_new(target/'details.json', details)
    write_new(target/'report.json', report)
    artifacts = {name:sha(target/name) for name in ['report.json','details.json','snapshot.json','protocol.json','registration.json']}
    write_new(index, {'schemaVersion':1, 'run':args.run, 'artifacts':artifacts, 'completedAtUtc':datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'stage':'completed','run':args.run,'report':str(target/'report.json')},ensure_ascii=False),flush=True)

if __name__ == '__main__':
    main()
