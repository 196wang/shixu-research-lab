"""Timing research HTTP contracts, with disposable stores and no external requests."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request, error
from unittest.mock import patch

from app_server import make_server


class TimingApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(0, self.tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.before = self.hashes()

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in Path(self.tmp.name).glob('*.sqlite3')}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertEqual(self.hashes(), self.before)
        self.tmp.cleanup()

    def call(self, path):
        try:
            with request.urlopen(self.origin + path, timeout=30) as reply:
                return reply.status, json.load(reply)
        except error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_unknown_duplicate_and_missing_params_rejected(self):
        for endpoint, query in [('/api/gann-timing', 'symbol=BTCUSDT&start=2026-09-01&end=2026-09-27'),
                                ('/api/astro-events', 'start=2026-09-01&end=2026-09-27')]:
            for suffix in ('', '?' + query + '&start=2026-09-02', '?' + query + '&direction=up',
                           '?' + query.replace('start=2026-09-01', 'start=')):
                with self.subTest(endpoint=endpoint, suffix=suffix):
                    self.assertEqual(self.call(endpoint + suffix)[0], 400)

    def test_no_network_or_registration_or_reassessment_from_reads(self):
        with (patch.object(self.server.market, 'start_refresh', side_effect=AssertionError('network')),
             patch.object(self.server.prospective, 'register', side_effect=AssertionError('register')),
             patch.object(self.server.prospective, 'evaluate', side_effect=AssertionError('evaluate'))):
            code, data = self.call('/api/gann-timing?symbol=BTCUSDT&start=2026-09-01&end=2026-09-27')
            self.assertEqual(code, 200)
            self.assertEqual(data['anchors'], [])
            self.assertEqual(data['projections'], [])
            self.assertFalse(data['marketValidated'])
            code, sky = self.call('/api/astro-events?start=2026-09-27&end=2026-10-26')
            self.assertEqual(code, 200)
            self.assertTrue(sky['events'])
            self.assertTrue(all(e['direction'] is None for e in sky['events']))
            self.assertFalse(sky['marketValidated'])
            self.assertEqual(self.call('/api/records')[1]['records'], [])

    def test_bounds_are_errors_not_silent_truncation(self):
        paths = [
            '/api/gann-timing?symbol=OTHER&start=2026-09-01&end=2026-09-27',
            '/api/gann-timing?symbol=BTCUSDT&start=2026-01-01&end=2026-09-27',
            '/api/gann-timing?symbol=BTCUSDT&start=2026-09-28&end=2026-09-27',
            '/api/astro-events?start=2026-01-01&end=2026-09-27',
            '/api/astro-events?start=2026-09-28&end=2026-09-27',
            '/api/astro-events?start=2026-02-30&end=2026-03-01',
            '/api/astro-events?start=1900-01-01&end=1900-01-02',
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.call(path)[0], 400)

    def test_engine_unavailable_is_explicit(self):
        with patch('astro_events.calculate_astro_events', side_effect=RuntimeError('test engine unavailable')):
            code, data = self.call('/api/astro-events?start=2026-09-27&end=2026-09-27')
        self.assertEqual(code, 503)
        self.assertIn('test engine unavailable', data['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
