"""Checks for the published plan and an explicitly synthetic scoring example."""
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
import hashlib
import json
import shutil
import threading
import unittest
import uuid
from unittest.mock import Mock, patch
from urllib.request import Request, urlopen

from app_server import Handler, make_server
from coinbase_feed import local_open, ms
from coinbase_forward import CoinbaseForward, CoinbaseForwardError, digest, verify


ROOT = Path(__file__).resolve().parent


@contextmanager
def audit_test_directory():
    # tempfile.TemporaryDirectory uses restrictive Windows ACLs in the local
    # sandbox. A normal workspace directory also works on GitHub Actions.
    directory = ROOT / ('test_public_audit_' + uuid.uuid4().hex)
    directory.mkdir()
    try:
        yield directory
    finally:
        if directory.resolve().parent != ROOT.resolve():
            raise AssertionError('audit test directory escaped the workspace')
        shutil.rmtree(directory)


class PublicReleaseTests(unittest.TestCase):
    def test_published_registration_seal_and_protocol(self):
        raw = (ROOT / 'public_data/coinbase_forward_registration.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '1742c8550a401356f9e500896ec234fc0ba0684b5942655a65cb1d5f37daf978')
        plan = json.loads(raw)
        protocol = json.loads((ROOT / 'coinbase_forward_protocol.json').read_text(encoding='utf-8'))
        verify(plan)
        self.assertEqual(plan['contentSha256'], 'ccf4191d4e4b1c66a318d1300e669bbf01d4bb804d4c935387c5a32c74fec690')
        self.assertEqual(plan['protocol'], protocol)
        self.assertEqual(plan['protocolSha256'], digest(protocol))
        self.assertEqual(plan['scheduleSha256'], digest(plan['schedule']))
        self.assertEqual((plan['startDate'], plan['endDate']), ('2026-10-19', '2026-11-17'))
        self.assertEqual(len(plan['schedule']), 30)
        self.assertEqual([row['date'] for row in plan['schedule']], [
            (date(2026, 10, 19) + timedelta(days=i)).isoformat() for i in range(30)])
        self.assertFalse(plan['marketValidated'])
        self.assertFalse(plan['independentBlindSample'])

    def test_public_plan_is_visible_without_local_registration(self):
        study = CoinbaseForward(ROOT / 'unused_local_registration_for_test', None, base=ROOT,
                                clock=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc))
        status = study.status()
        self.assertIsNone(status['registration'])
        self.assertIsNone(status['latestEvaluation'])
        self.assertEqual(status['evaluationCount'], 0)
        self.assertEqual(status['publicRegistration']['contentSha256'],
                         'ccf4191d4e4b1c66a318d1300e669bbf01d4bb804d4c935387c5a32c74fec690')

    def test_synthetic_scoring_fixture(self):
        case = json.loads((ROOT / 'examples/synthetic_forward_case.json').read_text(encoding='utf-8'))
        target = date.fromisoformat(case['targetDate'])
        candles = {}
        for offset in range(-case['neighborDaysEachSide'], case['neighborDaysEachSide'] + 1):
            day = target + timedelta(days=offset)
            open_ms = ms(local_open(day))
            prices = case['targetCandle'] if offset == 0 else case['ordinaryCandle']
            candles[open_ms] = {'symbol': 'BTCUSD', 'open_ms': open_ms,
                                'close_ms': open_ms + 86400000 - 1, **prices,
                                'is_closed': 1, 'conflicted': 0, 'request_id': 'synthetic',
                                'body_sha256': 'a' * 64, 'updated': '2026-11-01T00:00:00+00:00'}
        registration = {'registeredAtUtc': case['registeredAtUtc'],
                        'startDate': case['targetDate'], 'endDate': case['targetDate'],
                        'schedule': [{'date': case['targetDate'], 'boundaryExcluded': False,
                                      'directionRuleIds': [case['directionRuleId']],
                                      'timeEvents': [{'ruleId': case['timeRuleId'], 'eventId': 'synthetic'}]}]}
        score = CoinbaseForward(ROOT / 'unused_local_registration_for_test', None, base=ROOT)._score(
            registration, ({'symbol': 'BTCUSD'}, candles, {}), datetime(2026, 11, 1, tzinfo=timezone.utc))
        row, expected = score['rows'][0], case['expected']
        self.assertEqual(row['directionStatus'], expected['directionStatus'])
        self.assertEqual(row['directionResults'][case['directionRuleId']], expected['directionResult'])
        self.assertEqual(row['timeStatus'], expected['timeStatus'])
        self.assertEqual(row['timing']['hit'], expected['timingHit'])
        self.assertEqual(row['timing']['errorDays'], expected['errorDays'])

    def test_public_replay_uses_cached_prices_without_writing_registration(self):
        target = date(2026, 10, 19)
        opened = ms(local_open(target))
        bar = {'symbol': 'BTCUSD', 'open_ms': opened, 'close_ms': opened + 86400000 - 1,
               'open': '100', 'high': '101', 'low': '89', 'close': '90',
               'is_closed': 1, 'conflicted': 0, 'request_id': 'synthetic-source-a',
               'body_sha256': 'a' * 64, 'updated': '2026-10-20T00:00:00+00:00'}
        state = {'symbol': 'BTCUSD', 'provider': 'Coinbase Exchange', 'product': 'BTC-USD',
                 'marketType': 'spot', 'dayTimezone': 'UTC+8',
                 'last_success': '2026-10-20T00:00:00+00:00'}
        market = Mock()
        market.path = ROOT / 'unused_local_registration_for_test' / 'market.sqlite3'
        market._snapshot.return_value = (state, {opened: bar}, {})
        store = ROOT / 'unused_local_registration_for_test'
        self.assertFalse(store.exists())
        study = CoinbaseForward(store, market, base=ROOT,
                                clock=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc))
        first = study.preview_public()
        self.assertEqual(first['mode'], 'public_read_only')
        self.assertEqual(first['registrationSha256'],
                         'ccf4191d4e4b1c66a318d1300e669bbf01d4bb804d4c935387c5a32c74fec690')
        self.assertEqual(first['rows'][0]['directionStatus'], 'scored')
        self.assertEqual(first['rows'][0]['directionResults']['DAY-STEM-FIRE-DOWN'], 'correct')
        self.assertEqual(first['rows'][0]['timeStatus'], 'late_registration')
        evidence = first['rows'][0]['marketEvidence']
        self.assertEqual((evidence['open'], evidence['high'], evidence['low'], evidence['close']),
                         ('100', '101', '89', '90'))
        self.assertEqual(evidence['candleSha256'], 'a' * 64)
        self.assertEqual(first['rows'][1]['marketEvidence']['status'], 'missing')
        self.assertFalse(store.exists())
        market.refresh.assert_not_called()
        bar['request_id'], bar['body_sha256'] = 'synthetic-source-b', 'b' * 64
        second = study.preview_public()
        self.assertEqual(first['priceInputSha256'], second['priceInputSha256'])
        self.assertNotEqual(first['rawCacheSnapshotSha256'], second['rawCacheSnapshotSha256'])
        self.assertFalse(store.exists())
        market._snapshot.return_value = ({**state, 'provider': 'other'}, {opened: bar}, {})
        with self.assertRaises(CoinbaseForwardError):
            study.preview_public()

    def test_explicit_public_market_refresh_only_fetches_closed_plan_span(self):
        market = Mock()
        study = CoinbaseForward(ROOT / 'unused_local_registration_for_test', market, base=ROOT,
                                clock=lambda: datetime(2026, 10, 23, 12, tzinfo=timezone.utc))
        market.refresh_range.return_value = {'range': {'start': '2026-10-14', 'end': '2026-10-22'}}
        result = study.refresh_public_market()
        self.assertEqual(result['range']['start'], '2026-10-14')
        market.refresh_range.assert_called_once_with('BTCUSD', date(2026, 10, 14),
                                                     date(2026, 10, 22))
        self.assertFalse((ROOT / 'unused_local_registration_for_test').exists())

    def test_public_auto_audit_waits_for_close_then_appends_once_per_input(self):
        with audit_test_directory() as base:
            market = Mock()
            market.path = base / 'coinbase_market.sqlite3'
            market._snapshot.return_value = ({'symbol': 'BTCUSD'}, {}, {})
            fresh = [False]
            market.refresh_range.side_effect = lambda *_: fresh.__setitem__(0, True)
            now = [datetime(2026, 10, 22, 16, 19, tzinfo=timezone.utc)]
            study = CoinbaseForward(base, market, base=ROOT, clock=lambda: now[0])
            review = {'mode': 'public_read_only', 'priceInputSha256': 'a' * 64,
                      'rawCacheSnapshotSha256': 'b' * 64,
                      'scoreImplementationSha256': {'coinbase_forward.py': 'c' * 64},
                      'summary': {'directionScoredDays': 4, 'directionMissingDays': 0},
                      'rows': []}
            study.preview_public = Mock(return_value=review)

            def fake_inspect(_snapshot, first, last, _now):
                return {day: {'valid': fresh[0] or day != first}
                        for day in (first + timedelta(days=i)
                                    for i in range((last - first).days + 1))}

            with patch('coinbase_forward.inspect', fake_inspect):
                self.assertIsNone(study.auto_public_tick())
                market.refresh_range.assert_not_called()
                now[0] = datetime(2026, 10, 22, 16, 21, tzinfo=timezone.utc)
                first = study.auto_public_tick()
                self.assertTrue(first['created'])
                market.refresh_range.assert_called_once_with('BTCUSD', date(2026, 10, 14),
                                                             date(2026, 10, 22))
                again = study.auto_public_tick()
                self.assertFalse(again['created'])
            self.assertFalse((base / 'coinbase_forward/registration.json').exists())
            self.assertEqual(len(list((base / 'public_forward_audit').glob('*.json'))), 1)
            status = study.status()
            self.assertEqual(status['publicAudit']['count'], 1)
            self.assertEqual(status['publicAudit']['recent'][0]['scoredDays'], 4)
            audit_path = next((base / 'public_forward_audit').glob('*.json'))
            record = json.loads(audit_path.read_text(encoding='utf-8'))
            self.assertEqual(record['registrationSha256'], status['publicRegistration']['contentSha256'])
            verify(record)
            record['review']['summary']['directionScoredDays'] = 99
            audit_path.write_text(json.dumps(record), encoding='utf-8')
            with self.assertRaises(CoinbaseForwardError):
                study.status()

    def test_status_exposes_separate_market_and_registration_paths(self):
        market = Mock()
        market.path = ROOT / 'unused_local_market_for_test' / 'coinbase_market.sqlite3'
        study = CoinbaseForward(ROOT / 'unused_local_registration_for_test', market, base=ROOT,
                                clock=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc))
        sources = study.status()['dataSources']
        self.assertEqual(sources['marketCachePath'], str(market.path.resolve()))
        self.assertEqual(sources['localRegistrationPath'], str(
            (ROOT / 'unused_local_registration_for_test/coinbase_forward/registration.json').resolve()))
        self.assertFalse(sources['marketAndRegistrationSameDirectory'])

    def test_public_review_http_route_reads_without_local_record(self):
        market = Mock()
        market.path = ROOT / 'unused_local_market_for_test' / 'coinbase_market.sqlite3'
        market._snapshot.return_value = ({'symbol': 'BTCUSD', 'provider': 'Coinbase Exchange',
                                          'product': 'BTC-USD', 'marketType': 'spot',
                                          'dayTimezone': 'UTC+8', 'last_success': None}, {}, {})
        store = ROOT / 'unused_local_registration_for_test'
        study = CoinbaseForward(store, market, base=ROOT,
                                clock=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.coinbase_forward = study
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            with urlopen(origin + '/api/coinbase-forward-public-review', timeout=5) as response:
                self.assertEqual(response.status, 200)
                replay = json.load(response)
            self.assertEqual(replay['mode'], 'public_read_only')
            self.assertEqual(len(replay['rows']), 30)
            with urlopen(origin + '/api/coinbase-forward-status', timeout=5) as response:
                status = json.load(response)
            self.assertIsNone(status['registration'])
            self.assertEqual(status['evaluationCount'], 0)
            self.assertEqual(replay['registrationSha256'], status['publicRegistration']['contentSha256'])
            market.refresh_range.return_value = {'complete': 8, 'missing': 0,
                                                 'range': {'start': '2026-10-14', 'end': '2026-10-21'}}
            request = Request(origin + '/api/coinbase-forward-refresh-market', data=b'{}',
                              headers={'Origin': origin, 'Content-Type': 'application/json'},
                              method='POST')
            with urlopen(request, timeout=5) as response:
                self.assertEqual(json.load(response)['complete'], 8)
            market.refresh_range.assert_called_once_with('BTCUSD', date(2026, 10, 14),
                                                         date(2026, 10, 21))
            self.assertFalse(store.exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def test_server_never_inherits_neighboring_legacy_registration(self):
        store = ROOT / 'unused_local_registration_for_test'
        original_exists = Path.exists

        def pretend_legacy_exists(path):
            if path.name == 'registration.json' and '时序研究室_v0.5' in path.parts:
                return True
            return original_exists(path)

        with ExitStack() as stack:
            for service in ('Store', 'MarketFeed', 'CoinbaseMarketFeed', 'MarketMultiframe',
                            'ProspectiveStudy', 'MonthInteractionProspective',
                            'PredictionLedger', 'ScoringMarketRouter'):
                stack.enter_context(patch('app_server.' + service))
            stack.enter_context(patch.object(Path, 'exists', pretend_legacy_exists))
            server = make_server(0, store)
            try:
                self.assertEqual(server.coinbase_forward.directory, store / 'coinbase_forward')
                self.assertFalse((store / 'coinbase_forward/registration.json').exists())
            finally:
                server.server_close()


if __name__ == '__main__':
    unittest.main()
