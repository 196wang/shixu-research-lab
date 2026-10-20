"""HTTP contract tests use disposable stores and never contact the exchange."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request, error
from unittest.mock import patch

from app_server import make_server


class MarketApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(0, self.tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.notes = Path(self.tmp.name) / 'research.sqlite3'
        self.before = hashlib.sha256(self.notes.read_bytes()).hexdigest()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertEqual(hashlib.sha256(self.notes.read_bytes()).hexdigest(), self.before)
        self.tmp.cleanup()

    def call(self, path, body=None, origin=None):
        raw = None if body is None else json.dumps(body).encode()
        headers = {} if raw is None else {'Content-Type': 'application/json', 'Origin': origin or self.origin}
        try:
            with request.urlopen(request.Request(self.origin + path, data=raw, headers=headers), timeout=15) as reply:
                return reply.status, json.load(reply)
        except error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_empty_cache_and_status_are_read_only_without_network(self):
        with patch.object(self.server.market, 'start_refresh', side_effect=AssertionError('GET started download')):
            self.assertEqual(self.call('/api/market-status')[0], 200)
            code, data = self.call('/api/market-history?symbol=ETHUSDT')
            self.assertEqual(code, 200)
            self.assertFalse(data['available'])
            self.assertEqual(data['rows'], [])
            self.assertEqual(self.call('/api/records')[1]['records'], [])

    def test_refresh_is_same_origin_post_and_rejects_ambiguous_inputs(self):
        with patch.object(self.server.market, 'start_refresh', return_value={'accepted': True, 'job': {'state': 'running'}}) as start:
            self.assertEqual(self.call('/api/market-refresh', {'symbol': 'BTCUSDT'}, origin='https://example.com')[0], 403)
            for body in ({}, [], {'symbol': 'BTCUSDT', 'url': 'https://example.com'}):
                self.assertEqual(self.call('/api/market-refresh', body)[0], 400)
            self.assertEqual(self.call('/api/market-refresh?symbol=ETHUSDT', {'symbol': 'BTCUSDT'})[0], 400)
            start.assert_not_called()
            self.assertEqual(self.call('/api/market-refresh', {'symbol': 'BTCUSDT'})[0], 202)
            start.assert_called_once_with('BTCUSDT')

    def test_market_parameter_validation_does_not_fall_back_to_btc(self):
        for path in ('/api/market-history?symbol=ETHBTC',
                     '/api/market-history?symbol=BTCUSDT&symbol=ETHUSDT',
                     '/api/market-history?symbol=BTCUSDT&date=2026-09-20',
                     '/api/market-history', '/api/market-status?symbol=BTCUSDT',
                     '/api/market-detail?symbol=ETHUSDT&date=2026-09-20&profile=BTC-BZ-V01'):
            with self.subTest(path=path):
                self.assertEqual(self.call(path)[0], 400)

    def test_calendar_evidence_and_frozen_history_remain_separate(self):
        code, evidence = self.call('/api/calendar-accuracy')
        self.assertEqual(code, 200)
        self.assertIn('summary', evidence)
        self.assertEqual(self.call('/api/calendar-accuracy?at=2026-09-20')[0], 400)
        code, historical = self.call('/api/chart-history?symbol=BTCUSDT')
        self.assertEqual(code, 200)
        self.assertEqual(historical['range']['end'], '2026-09-26')
        self.assertFalse(historical['liveMarketData'])
        self.assertEqual(self.call('/api/chart-history?symbol=ETHUSDT')[0], 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
