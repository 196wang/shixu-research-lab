"""Disposable loopback HTTP checks, never a production server or ledger write."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from copy import deepcopy
from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from urllib import request, error

from app_server import make_server
from chart_candidates import ChartCandidatesError
from market_feed import MarketFeedError


class ChartCandidateHttpTests(unittest.TestCase):
    def test_multiframe_http_strict_readonly_and_explicit_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0, directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
            def call(path, body=None, send_origin=True):
                headers = {'Content-Type': 'application/json'}
                if send_origin: headers['Origin'] = origin
                req = request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
                try:
                    with request.urlopen(req, timeout=10) as reply:
                        return reply.status, json.load(reply)
                except error.HTTPError as exc:
                    return exc.code, json.load(exc)
            def hashes():
                return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(directory).glob('*.sqlite3')}
            try:
                before = hashes()
                with patch.object(server.multiframe, 'transport', side_effect=AssertionError('GET must not fetch')):
                    code, value = call('/api/market-bars?symbol=BTCUSDT&interval=4h')
                    self.assertEqual(code, 200)
                    self.assertFalse(value['available'])
                    self.assertEqual(value['rows'], [])
                    code, value = call('/api/multiframe-status')
                    self.assertEqual(code, 200)
                    self.assertEqual(value['fourHour']['job'], None)
                    for query in ('', '?symbol=BTCUSDT', '?symbol=BTCUSDT&interval=1m',
                                  '?symbol=BTCUSDT&interval=4h&interval=4h', '?symbol=X&interval=4h',
                                  '?symbol=BTCUSDT&interval=4h&limit=10'):
                        self.assertEqual(call('/api/market-bars' + query)[0], 400)
                    self.assertEqual(call('/api/multiframe-status?symbol=BTCUSDT')[0], 400)
                self.assertEqual(hashes(), before)
                with patch.object(server.multiframe, 'start_refresh', return_value={'accepted': True, 'job': {'id': 'fixture'}}) as refresh:
                    self.assertEqual(call('/api/multiframe-refresh', {'symbol': 'ETHUSDT', 'interval': '4h'})[0], 202)
                    refresh.assert_called_once_with('ETHUSDT', '4h')
                    self.assertEqual(call('/api/multiframe-refresh', {'symbol': 'ETHUSDT'})[0], 400)
                    self.assertEqual(call('/api/multiframe-refresh?x=1', {'symbol': 'ETHUSDT', 'interval': '4h'})[0], 400)
                    self.assertEqual(call('/api/multiframe-refresh', {'symbol': 'ETHUSDT', 'interval': '4h'}, False)[0], 403)
                self.assertEqual(call('/api/multiframe-refresh', {'symbol': 'X', 'interval': '4h'})[0], 400)
                self.assertEqual(call('/api/multiframe-refresh', {'symbol': 'BTCUSDT', 'interval': '1m'})[0], 400)
                with patch.object(server.multiframe, 'start_refresh', side_effect=MarketFeedError('cooldown', 429)):
                    self.assertEqual(call('/api/multiframe-refresh', {'symbol': 'BTCUSDT', 'interval': '4h'})[0], 429)
                self.assertEqual(hashes(), before)
            finally:
                server.shutdown(); server.server_close(); thread.join(5)

    def test_candidate_key_guard_keeps_idempotency_revisions_and_manual_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0,directory)
            server.predictions.clock=lambda:datetime(2026,9,27,tzinfo=timezone.utc)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            origin='http://127.0.0.1:'+str(server.server_port)
            body={'symbol':'BTCUSDT','kind':'up','targetDate':'2026-11-01','horizonDays':1,
                  'title':'TEST ONLY','note':'candidateKey: CC-'+'a'*32+'\nsource: original',
                  'rules':[],'invalidation':None}
            def post(path,value):
                req=request.Request(origin+path,data=json.dumps(value).encode(),headers={'Content-Type':'application/json','Origin':origin})
                try:
                    with request.urlopen(req,timeout=10) as reply:return reply.status,json.load(reply)
                except error.HTTPError as exc:return exc.code,json.load(exc)
            try:
                code,first=post('/api/predictions',body)
                self.assertEqual(code,201)
                root=first['record']['id']
                self.assertEqual(post('/api/predictions',body)[0],200)
                changed=deepcopy(body);changed['note']=changed['note'].replace('original','new source hash')
                code,conflict=post('/api/predictions',changed)
                self.assertEqual(code,409)
                self.assertEqual(conflict['existingRecordId'],root)
                self.assertEqual(conflict['rootId'],root)
                self.assertEqual(conflict['code'],'candidate_already_registered')
                code,revised=post('/api/predictions/'+root+'/revise',changed)
                self.assertEqual(code,201)
                self.assertFalse(revised['record']['isOriginal'])
                next_body=deepcopy(changed);next_body['title']='TEST ONLY new title'
                conflict=post('/api/predictions',next_body)[1]
                self.assertEqual(conflict['existingRecordId'],revised['record']['id'])
                self.assertEqual(conflict['latestRevision']['version'],2)
                manual=deepcopy(body);manual['note']='TEST ONLY manual'
                self.assertEqual(post('/api/predictions',manual)[0],201)
                manual['note']='TEST ONLY second manual'
                self.assertEqual(post('/api/predictions',manual)[0],201)
                records=server.predictions.list()['records']
                self.assertEqual(sum(r['isOriginal'] for r in records),3)
                self.assertEqual(len([r for r in records if r['rootId']==root]),2)
                # Two simultaneous changed-evidence submissions share one guard lock.
                race=deepcopy(body);race['note']='candidateKey: CC-'+'b'*32+'\nsource: one'
                race2=deepcopy(race);race2['note']=race2['note'].replace('one','two')
                results=[]
                workers=[threading.Thread(target=lambda value=value:results.append(post('/api/predictions',value)[0])) for value in (race,race2)]
                for worker in workers:worker.start()
                for worker in workers:worker.join(10)
                self.assertEqual(sorted(results),[201,409])
            finally:
                server.shutdown();server.server_close();thread.join(5)

    def test_strict_query_get_only_static_and_no_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            server = make_server(0, directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = 'http://127.0.0.1:' + str(server.server_port)
            def files():
                return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(directory).glob('*.sqlite3')}
            before = files()
            def get(path):
                try:
                    with request.urlopen(origin+path,timeout=10) as reply: return reply.status, reply.read(), reply.headers
                except error.HTTPError as exc: return exc.code, exc.read(), exc.headers
            try:
                with patch('app_server.chart_candidates', return_value={'symbol':'BTCUSDT','days':[]}) as preview, \
                     patch.object(server.market, 'start_refresh', side_effect=AssertionError('no refresh')):
                    code, raw, headers = get('/api/chart-candidates?symbol=BTCUSDT')
                    self.assertEqual(code, 200)
                    self.assertEqual(json.loads(raw)['symbol'], 'BTCUSDT')
                    self.assertEqual(headers['Cache-Control'], 'no-store')
                    self.assertEqual(preview.call_args.args, (server.market, 'BTCUSDT'))
                    self.assertEqual(preview.call_args.kwargs, {'days': 30})
                    self.assertEqual(get('/api/chart-candidates?symbol=BTCUSDT&days=365')[0], 200)
                    self.assertEqual(preview.call_args.kwargs, {'days': 365})
                    for query in ('', '?symbol=BTCUSDT&symbol=BTCUSDT','?symbol=BTCUSDT&start=2026-10-01','?symbol=ETHUSD'):
                        self.assertEqual(get('/api/chart-candidates'+query)[0], 400)
                    self.assertEqual(preview.call_count, 2)
                with patch('app_server.chart_candidates', side_effect=ChartCandidatesError('range unavailable')):
                    self.assertEqual(get('/api/chart-candidates?symbol=ETHUSDT')[0],503)
                req = request.Request(origin+'/api/chart-candidates?symbol=BTCUSDT', data=b'{}',
                    headers={'Content-Type':'application/json','Origin':origin}, method='POST')
                with self.assertRaises(error.HTTPError) as caught: request.urlopen(req,timeout=10)
                self.assertEqual(caught.exception.code,404)
                self.assertEqual(json.loads(get('/health')[1])['version'],'0.19.0')
                self.assertEqual(before, files())
            finally:
                server.shutdown(); server.server_close(); thread.join(5)


if __name__ == '__main__': unittest.main()
