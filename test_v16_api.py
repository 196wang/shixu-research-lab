"""Freeze integrity and read-only HTTP checks in disposable fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request, error
from unittest.mock import patch
from app_server import make_server
from historical_exploration_store import read_artifact, ExplorationIntegrityError, FILES

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding='utf-8')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

class ExplorationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.run = self.base/'exploration_v16/run-001'
        self.run.mkdir(parents=True)
        (self.base/'algorithm.py').write_text('# fixed fixture\n', encoding='utf-8')
        for name in ('report.json','details.json','snapshot.json','protocol.json'):
            write(self.run/name, {'fixture':name, 'status':'exploratory'})
        write(self.run/'registration.json', {'inputHashes':{'algorithm.py':sha(self.base/'algorithm.py')},
              'snapshotFileSha256':sha(self.run/'snapshot.json'), 'protocolFileSha256':sha(self.run/'protocol.json')})
        self.refresh_manifest()

    def refresh_manifest(self):
        write(self.base/'exploration_v16/index.json', {'run':'run-001',
              'artifacts':{name:sha(self.run/name) for name in FILES.values()}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_verified_reads_all_artifacts_without_writes(self):
        before = {str(p):sha(p) for p in self.base.rglob('*') if p.is_file()}
        for kind in FILES:
            self.assertIsInstance(read_artifact(kind,self.base),dict)
        self.assertEqual(before,{str(p):sha(p) for p in self.base.rglob('*') if p.is_file()})

    def test_mutated_result_or_dependency_fails_closed(self):
        write(self.run/'details.json', {'tampered':True})
        with self.assertRaises(ExplorationIntegrityError): read_artifact(base=self.base)
        self.refresh_manifest()
        (self.base/'algorithm.py').write_text('# changed', encoding='utf-8')
        with self.assertRaises(ExplorationIntegrityError): read_artifact(base=self.base)

    def test_snapshot_and_protocol_must_match_registration(self):
        write(self.run/'snapshot.json', {'different':True})
        self.refresh_manifest()
        with self.assertRaises(ExplorationIntegrityError): read_artifact(base=self.base)

    def test_path_escape_and_missing_manifest_fields_rejected(self):
        index = self.base/'exploration_v16/index.json'
        write(index, {'run':'../../outside','artifacts':{}})
        with self.assertRaises(ExplorationIntegrityError): read_artifact(base=self.base)
        self.refresh_manifest()
        write(self.run/'registration.json', {'inputHashes':{'../outside.py':'a'*64},
              'snapshotFileSha256':sha(self.run/'snapshot.json'), 'protocolFileSha256':sha(self.run/'protocol.json')})
        self.refresh_manifest()
        with self.assertRaises(ExplorationIntegrityError): read_artifact(base=self.base)

    def test_not_run_and_unknown_kind(self):
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(read_artifact(base=empty)['status'],'not_run')
        with self.assertRaises(ValueError): read_artifact('anything',self.base)

class ExplorationHttpTests(unittest.TestCase):
    def test_routes_are_reads_and_reject_mutable_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0,directory)
            thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            origin = 'http://127.0.0.1:'+str(server.server_port)
            before = {p.name:sha(p) for p in Path(directory).glob('*.sqlite3')}
            def get(path):
                try:
                    with request.urlopen(origin+path,timeout=10) as reply: return reply.status,json.load(reply),reply.headers
                except error.HTTPError as exc: return exc.code,json.load(exc),exc.headers
            try:
                with patch('app_server.read_artifact',return_value={'status':'exploratory','test':True}) as read, \
                     patch.object(server.market,'start_refresh',side_effect=AssertionError('network')):
                    for suffix in ('','-details','-protocol','-snapshot','-registration'):
                        status,body,headers=get('/api/exploration'+suffix)
                        self.assertEqual(status,200);self.assertTrue(body['test'])
                        if suffix: self.assertIn('attachment',headers['Content-Disposition'])
                        self.assertEqual(get('/api/exploration'+suffix+'?start=2020-01-01')[0],400)
                    self.assertEqual(read.call_count,5)
                with patch('app_server.read_artifact',side_effect=ExplorationIntegrityError('changed')):
                    self.assertEqual(get('/api/exploration')[0],503)
                self.assertEqual(before,{p.name:sha(p) for p in Path(directory).glob('*.sqlite3')})
            finally:
                server.shutdown();server.server_close();thread.join(5)

if __name__ == '__main__': unittest.main()
