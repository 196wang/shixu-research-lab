"""HTTP/persistence boundaries for prospective validation, without real registration."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib import request, error
from unittest.mock import patch

from app_server import make_server


class ProspectiveApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(0, self.tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.before = {name: self.hash(name) for name in ('research.sqlite3', 'market.sqlite3')}

    def hash(self, name):
        return hashlib.sha256((Path(self.tmp.name) / name).read_bytes()).hexdigest()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertEqual({name: self.hash(name) for name in self.before}, self.before)
        self.tmp.cleanup()

    def call(self, path, body=None, origin=None):
        raw = None if body is None else json.dumps(body).encode()
        headers = {} if raw is None else {'Content-Type': 'application/json', 'Origin': origin or self.origin}
        try:
            with request.urlopen(request.Request(self.origin + path, data=raw, headers=headers), timeout=15) as response:
                return response.status, json.load(response)
        except error.HTTPError as exc:
            return exc.code, json.load(exc)

    def test_read_status_never_registers_scores_or_downloads(self):
        study = self.server.prospective
        with patch.object(study, 'register', side_effect=AssertionError('read registered')), \
             patch.object(study, 'evaluate', side_effect=AssertionError('read scored')), \
             patch.object(self.server.market, 'start_refresh', side_effect=AssertionError('read downloaded')):
            code, data = self.call('/api/prospective-status')
            self.assertEqual(code, 200)
            self.assertIsInstance(data, dict)
            self.assertEqual(self.call('/api/records')[1]['records'], [])

    def test_no_client_control_over_protocol_clock_or_asset(self):
        with patch.object(self.server.prospective, 'register') as register, \
             patch.object(self.server.prospective, 'evaluate') as evaluate:
            for path in ('/api/prospective-register', '/api/prospective-evaluate'):
                for body in ([], {'symbol': 'ETHUSDT'}, {'start': '2020-01-01'}, {'now': '2026-09-01'}, {'direction': 'up'}):
                    self.assertEqual(self.call(path, body)[0], 400)
                self.assertEqual(self.call(path + '?x=1', {})[0], 400)
                self.assertEqual(self.call(path, {}, origin='https://example.com')[0], 403)
            register.assert_not_called()
            evaluate.assert_not_called()

    def test_explicit_posts_and_export_are_distinct(self):
        study = self.server.prospective
        with patch.object(study, 'register', return_value={'registered': True}) as register, \
             patch.object(study, 'evaluate', return_value={'snapshot': 'test'}) as evaluate, \
             patch.object(study, 'export', return_value={'appendOnly': True}) as export:
            self.assertEqual(self.call('/api/prospective-register', {}), (200, {'registered': True}))
            self.assertEqual(self.call('/api/prospective-evaluate', {}), (200, {'snapshot': 'test'}))
            self.assertEqual(self.call('/api/prospective-export'), (200, {'appendOnly': True}))
            self.assertEqual(self.call('/api/prospective-status?symbol=ETHUSDT')[0], 400)
            self.assertEqual(self.call('/api/prospective-export?all=true')[0], 400)
            register.assert_called_once_with()
            evaluate.assert_called_once_with()
            export.assert_called_once_with()


if __name__ == '__main__':
    unittest.main(verbosity=2)
