import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request, error

from app_server import make_server, Store, calculate


class ResearchDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server(0, cls.tmp.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)
        cls.tmp.cleanup()

    def call(self, path, data=None, origin=None, host=None):
        headers = {}
        raw = None
        if data is not None:
            raw = json.dumps(data).encode()
            headers = {'Content-Type': 'application/json', 'Origin': origin or self.origin}
        if host:
            headers['Host'] = host
        req = request.Request(self.origin + path, data=raw, headers=headers)
        try:
            with request.urlopen(req, timeout=15) as response:
                return response.status, response.read()
        except error.HTTPError as exc:
            return exc.code, exc.read()

    def test_page_is_app_and_private_files_not_served(self):
        status, html = self.call('/')
        self.assertEqual(status, 200)
        self.assertIn('排盘与规则', html.decode())
        self.assertEqual(self.call('/local_data/research.sqlite3')[0], 404)
        self.assertEqual(self.call('/../profiles.json')[0], 404)

    def test_wrong_host_and_cross_site_write_are_rejected(self):
        self.assertEqual(self.call('/health', host='untrusted.example')[0], 403)
        self.assertEqual(self.call('/api/records', {}, origin='https://example.com')[0], 403)

    def test_explicit_offset_and_duplicate_parameters(self):
        for query in ('profile=BTC-BZ-V01&at=2024-02-04T12:00:00',
                      'profile=BTC-BZ-V01&at=2024-02-04T12:00:00%2B08:60',
                      'profile=BTC-BZ-V01&at=2024-02-04T12:00:00-00:00',
                      'profile=BTC-BZ-V01&profile=ETH-BAZI-A&at=2024-02-04T12:00:00Z'):
            with self.subTest(query=query):
                self.assertEqual(self.call('/api/calculate?' + query)[0], 400)

    def test_eth_versions_keep_distinct_reference_stems_same_transits(self):
        a = calculate('ETH-BAZI-A', '2024-02-04T15:30:00Z')
        b = calculate('ETH-BAZI-B', '2024-02-04T15:30:00Z')
        self.assertEqual(a['bazi']['relationships']['natalDayReference']['stem'], '戊')
        self.assertEqual(b['bazi']['relationships']['natalDayReference']['stem'], '辛')
        self.assertEqual(a['indian']['positions'], b['indian']['positions'])
        self.assertIsNone(a['indian']['vimshottari'])
        self.assertFalse(b['indian']['natal']['assetNatalDasaAvailable'])

    def test_append_review_does_not_rewrite_initial_snapshot_or_note(self):
        body = {'profile': 'BTC-BZ-V01', 'at': '2024-02-04T15:30:00Z', 'scenario': False,
                'note': 'TEST ONLY: 原判断', 'judgement': 'down',
                'windowStart': '2024-02-05T00:00:00+08:00', 'windowEnd': '2024-02-06T00:00:00+08:00'}
        code, raw = self.call('/api/records', body)
        self.assertEqual(code, 201)
        first = json.loads(raw)
        self.assertEqual(first['timing_class'], 'historical_or_already_started')
        code, raw = self.call('/api/records/' + first['id'] + '/reviews', {'outcome': 'contradicted', 'note': 'TEST ONLY: 与原判断相反'})
        self.assertEqual(code, 201)
        second = json.loads(raw)
        self.assertEqual(first['snapshot'], second['snapshot'])
        self.assertEqual(first['created'], second['created'])
        self.assertEqual(second['note'], body['note'])
        self.assertEqual(second['reviews'][0]['outcome'], 'contradicted')
        fresh = Store(Path(self.tmp.name) / 'research.sqlite3').get(first['id'])
        self.assertEqual(fresh['reviews'], second['reviews'])

    def test_direction_requires_valid_window_and_hash(self):
        body = {'profile': 'BTC-BZ-V01', 'at': '2024-02-04T15:30:00Z', 'judgement': 'up'}
        self.assertEqual(self.call('/api/records', body)[0], 400)
        body.update(windowStart='2024-02-05T00:00:00Z', windowEnd='2024-02-04T00:00:00Z')
        self.assertEqual(self.call('/api/records', body)[0], 400)
        body.update(windowEnd='2024-02-06T00:00:00Z', snapshotSha256='wrong')
        self.assertEqual(self.call('/api/records', body)[0], 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
