"""End-to-end ledger HTTP contracts in disposable stores; never production."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request, error
from unittest.mock import patch
from app_server import make_server


class PredictionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(0, self.tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.old_hashes = self.hashes()
        self.target = (datetime.now(timezone.utc) + timedelta(days=20)).date().isoformat()

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(self.tmp.name).glob('*.sqlite3') if p.name != 'predictions.sqlite3'}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
        self.assertEqual(self.hashes(), self.old_hashes)
        self.tmp.cleanup()

    def call(self, path, body=None, origin=True):
        data = None if body is None else json.dumps(body).encode()
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = self.origin
        req = request.Request(self.origin+path, data=data, headers=headers)
        try:
            with request.urlopen(req, timeout=60) as reply:
                return reply.status, json.load(reply)
        except error.HTTPError as exc:
            return exc.code, json.load(exc)

    def body(self):
        return {'symbol': 'BTCUSDT','kind':'up','targetDate':self.target,'horizonDays':1,
                'title':'API integration fixture','note':'Disposable test only','rules':[], 'invalidation':None}

    def test_append_revision_invalidation_review_export_and_original_preserved(self):
        code, saved = self.call('/api/predictions', self.body())
        self.assertEqual(code, 201, saved)
        original = saved['record']
        self.assertEqual(self.call('/api/predictions', self.body())[0], 200)
        changed = {**self.body(), 'kind': 'down', 'title':'Revised fixture'}
        code, revised = self.call('/api/predictions/'+original['id']+'/revise', changed)
        self.assertEqual(code, 201, revised)
        self.assertEqual(revised['record']['parentId'], original['id'])
        code, invalidated = self.call('/api/predictions/'+revised['record']['id']+'/invalidate', {'reason':'Observed condition no longer applies'})
        self.assertEqual(code, 201, invalidated)
        with patch.object(self.server.market, 'start_refresh', side_effect=AssertionError('network')):
            code, evaluated = self.call('/api/predictions-evaluate', {})
        self.assertEqual(code, 200, evaluated)
        ledger = self.call('/api/predictions')[1]
        self.assertEqual(len(ledger['records']), 2)
        original_again = next(r for r in ledger['records'] if r['id'] == original['id'])
        self.assertEqual(original_again['kind'], 'up')
        self.assertEqual(ledger['summary']['originals']['total'], 1)
        self.assertEqual(ledger['summary']['revisions']['total'], 1)
        self.assertEqual(self.call('/api/prediction-confluence?symbol=BTCUSDT')[1]['rows'], [])
        exported = self.call('/api/predictions-export')[1]
        self.assertEqual(len(exported['reports']), 2)
        self.assertTrue(all(r['score']['status'] == 'pending' for r in exported['reports']))

    def test_reject_queries_untrusted_origin_invalid_body_and_backdating(self):
        self.assertEqual(self.call('/api/predictions', self.body(), origin=False)[0],403)
        self.assertEqual(self.call('/api/predictions', {**self.body(), 'createdAtUtc':'2020-01-01T00:00:00Z'})[0],400)
        self.assertEqual(self.call('/api/predictions', {**self.body(), 'targetDate':'2020-01-01'})[0],409)
        for path in ['/api/predictions?x=1','/api/predictions-export?x=1','/api/prediction-confluence?symbol=OTHER',
                     '/api/prediction-confluence?symbol=BTCUSDT&symbol=ETHUSDT',
                     '/api/prediction-candidates?symbol=BTCUSDT&start=2026-09-28&end=2026-10-01&x=1']:
            self.assertEqual(self.call(path)[0],400,path)
        self.assertEqual(self.call('/api/predictions-evaluate', {'asOfUtc':'2099-01-01T00:00:00Z'})[0],400)

    def test_candidates_never_write_any_store(self):
        db = Path(self.tmp.name)/'predictions.sqlite3'
        before = hashlib.sha256(db.read_bytes()).hexdigest()
        with patch.object(self.server.market, 'start_refresh', side_effect=AssertionError('network')):
            code, result = self.call('/api/prediction-candidates?symbol=BTCUSDT&start=2026-10-10&end=2026-10-11')
        self.assertEqual(code,200,result)
        self.assertTrue(result['candidates'])
        self.assertEqual(self.call('/api/predictions')[1]['records'],[])
        self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(),before)


if __name__ == '__main__': unittest.main()
