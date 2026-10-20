"""Read-only artifact and HTTP integrity tests, using disposable fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request, error
from unittest.mock import patch

from app_server import make_server
from month_study_store import read_month_artifact, MonthStudyIntegrityError, FILES, SNAPSHOT_PATH


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MonthStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.run = self.base/'month_study_v17/run-001'
        self.run.mkdir(parents=True)
        (self.base/'algorithm.py').write_text('# fixed\n', encoding='utf-8')
        write(self.base/SNAPSHOT_PATH, {'source': 'frozen'})
        for name in ('report.json', 'details.json', 'protocol.json'):
            write(self.run/name, {'fixture': name, 'status': 'exploratory'})
        self.registration = {'inputHashes': {'algorithm.py': sha(self.base/'algorithm.py')},
                             'snapshotPath': SNAPSHOT_PATH, 'snapshotFileSha256': sha(self.base/SNAPSHOT_PATH),
                             'protocolFileSha256': sha(self.run/'protocol.json')}
        write(self.run/'registration.json', self.registration)
        self.refresh_manifest()

    def tearDown(self):
        self.tmp.cleanup()

    def refresh_manifest(self):
        write(self.base/'month_study_v17/index.json', {'run': 'run-001',
              'artifacts': {name: sha(self.run/name) for name in FILES.values()}})

    def test_reads_do_not_rewrite_sources_or_artifacts(self):
        before = {str(p): sha(p) for p in self.base.rglob('*') if p.is_file()}
        for kind in FILES:
            self.assertIsInstance(read_month_artifact(kind, self.base), dict)
        self.assertEqual(before, {str(p): sha(p) for p in self.base.rglob('*') if p.is_file()})

    def test_changed_artifact_or_dependency_fails_closed(self):
        write(self.run/'report.json', {'modified': True})
        with self.assertRaises(MonthStudyIntegrityError): read_month_artifact(base=self.base)
        self.refresh_manifest()
        (self.base/'algorithm.py').write_text('# changed', encoding='utf-8')
        with self.assertRaises(MonthStudyIntegrityError): read_month_artifact(base=self.base)

    def test_changed_inherited_snapshot_fails_closed(self):
        write(self.base/SNAPSHOT_PATH, {'modified': True})
        with self.assertRaises(MonthStudyIntegrityError): read_month_artifact(base=self.base)

    def test_protocol_and_reference_must_match_freeze(self):
        write(self.run/'protocol.json', {'changed': True})
        self.refresh_manifest()
        with self.assertRaises(MonthStudyIntegrityError): read_month_artifact(base=self.base)
        self.registration['protocolFileSha256'] = sha(self.run/'protocol.json')
        self.registration['snapshotPath'] = '../outside.json'
        write(self.run/'registration.json', self.registration)
        self.refresh_manifest()
        with self.assertRaises(MonthStudyIntegrityError): read_month_artifact(base=self.base)

    def test_path_escape_and_bad_manifest_rejected(self):
        write(self.base/'month_study_v17/index.json', {'run': '../../outside', 'artifacts': {}})
        with self.assertRaises(MonthStudyIntegrityError): read_month_artifact(base=self.base)
        self.refresh_manifest()
        self.registration['inputHashes'] = {'../outside.py': 'a'*64}
        write(self.run/'registration.json', self.registration)
        self.refresh_manifest()
        with self.assertRaises(MonthStudyIntegrityError): read_month_artifact(base=self.base)

    def test_not_run_and_unknown_kind(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(read_month_artifact(base=empty)['status'], 'not_run')
        with self.assertRaises(ValueError): read_month_artifact('any', self.base)


class MonthHttpTests(unittest.TestCase):
    def test_get_routes_never_refresh_or_write_and_reject_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0, directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
            before = {p.name: sha(p) for p in Path(directory).glob('*.sqlite3')}

            def get(path):
                try:
                    with request.urlopen(origin+path, timeout=10) as reply:
                        return reply.status, json.load(reply), reply.headers
                except error.HTTPError as exc:
                    return exc.code, json.load(exc), exc.headers
            try:
                with patch('app_server.read_month_artifact', return_value={'status': 'exploratory', 'test': True}) as read, \
                     patch.object(server.market, 'start_refresh', side_effect=AssertionError('unexpected refresh')):
                    for suffix in ('', '-details', '-protocol', '-registration'):
                        status, body, headers = get('/api/month-study'+suffix)
                        self.assertEqual(status, 200)
                        self.assertTrue(body['test'])
                        if suffix: self.assertIn('attachment', headers['Content-Disposition'])
                        self.assertEqual(get('/api/month-study'+suffix+'?start=2020-01-01')[0], 400)
                    self.assertEqual(read.call_count, 4)
                with patch('app_server.read_month_artifact', side_effect=MonthStudyIntegrityError('changed')):
                    self.assertEqual(get('/api/month-study')[0], 503)
                self.assertEqual(before, {p.name: sha(p) for p in Path(directory).glob('*.sqlite3')})
            finally:
                server.shutdown(); server.server_close(); thread.join(5)


if __name__ == '__main__':
    unittest.main()
