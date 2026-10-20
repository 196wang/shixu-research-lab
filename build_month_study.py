"""Freeze the new study BEFORE computation, reusing the previous public snapshot."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT = 'exploration_v16/run-001/snapshot.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, data):
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def inputs():
    previous = json.loads((HERE/'exploration_v16/run-001/registration.json').read_text(encoding='utf-8'))
    names = set(previous['inputHashes'])
    names.update(('month_study.py', 'month_study_protocol.json', 'test_month_study.py',
                  'build_month_study.py', 'month_study_contract.md'))
    return {name: sha(HERE/name) for name in sorted(names)}


def main():
    from historical_exploration_store import read_artifact
    from month_study import build_report
    # Verify the inherited code and data before accepting the previous source.
    previous = read_artifact('snapshot', HERE)
    protocol = json.loads((HERE/'month_study_protocol.json').read_text(encoding='utf-8'))
    folder = HERE/'month_study_v17'
    target = folder/'run-001'
    if target.exists() or (folder/'index.json').exists():
        raise SystemExit('Study already frozen. Preserve it; do not silently rerun.')
    target.mkdir(parents=True)
    generated = datetime.now(timezone.utc)
    write_new(target/'protocol.json', protocol)
    registration = {
        'version': '0.17.0', 'studyId': protocol['studyId'], 'run': 'run-001',
        'frozenAtUtc': generated.isoformat(), 'snapshotPath': SNAPSHOT,
        'snapshotCapturedAtUtc': previous['capturedAtUtc'], 'snapshotFileSha256': sha(HERE/SNAPSHOT),
        'protocolFileSha256': sha(target/'protocol.json'), 'inputHashes': inputs(),
        'historicalExploration': True, 'registeredPrediction': False,
        'note': 'Protocol fixed before this calculation; all data are already historical and were previously accessible. This is not preregistered out-of-sample validation.'}
    write_new(target/'registration.json', registration)
    print(json.dumps({'stage': 'frozen', 'at': generated.isoformat(), 'inputs': len(registration['inputHashes'])}), flush=True)
    snapshot = (previous['state'], {int(k):v for k,v in previous['candles'].items()},
                {int(k):v for k,v in previous['gaps'].items()})
    report = build_report(snapshot, protocol, generated,
                          progress=lambda v: print(json.dumps(v, ensure_ascii=False), flush=True))
    assert inputs() == registration['inputHashes'], 'Inputs changed during computation'
    assert sha(HERE/SNAPSHOT) == registration['snapshotFileSha256'], 'Snapshot changed during computation'
    details = report.pop('details')
    report['registration'] = {'frozenAtUtc': generated.isoformat(),
                              'sha256': sha(target/'registration.json'), 'run': 'run-001'}
    report['sourceSnapshot'] = {'path': SNAPSHOT, 'sha256': registration['snapshotFileSha256'],
                               'capturedAtUtc': previous['capturedAtUtc']}
    report['files'] = {'details': '/api/month-study-details', 'protocol': '/api/month-study-protocol',
                       'registration': '/api/month-study-registration', 'snapshot': '/api/exploration-snapshot'}
    write_new(target/'details.json', details)
    write_new(target/'report.json', report)
    files = ('report.json', 'details.json', 'protocol.json', 'registration.json')
    write_new(folder/'index.json', {'schemaVersion': 1, 'run': 'run-001',
                                  'artifacts': {name: sha(target/name) for name in files},
                                  'completedAtUtc': datetime.now(timezone.utc).isoformat()})
    print(json.dumps({'stage': 'completed', 'report': str(target/'report.json')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
