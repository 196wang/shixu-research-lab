"""Historical display correctness and read-only endpoint tests; no market scoring."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error, parse, request

from app_server import make_server
from historical_chart import (HERE, BUILD_FILES, FROZEN_FILES, FROZEN_DAILY_SHA256,
                              ChartIntegrityError, build_history, chart_detail, convert_row,
                              load_history, parse_chart_query, read_json, verify_sources)


class HistoricalChartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.history = load_history()
        cls.source = read_json(HERE / 'daily_input.json')

    def test_all_dates_retained_and_complete_candles_match_frozen_prices(self):
        self.assertEqual(self.history['coverage'], {'total': 2461, 'complete': 2443, 'missing': 18})
        for index, (source, row) in enumerate(zip(self.source['dailyRows'], self.history['rows'])):
            with self.subTest(date=row['date']):
                self.assertEqual(row['date'], source['date'])
                self.assertEqual(row['sourceRefs'], source['sourceArchives'])
                self.assertIsNone(row['volume'])
                self.assertTrue(row['isClosed'])
                if row['complete']:
                    self.assertEqual(source['hourCount'], 24)
                    for key in ('open', 'high', 'low', 'close'):
                        self.assertEqual(row[key], float(source[key]))
                else:
                    self.assertTrue(row['errors'])
                    self.assertTrue(all(row[key] is None for key in ('open', 'high', 'low', 'close')))
                if index:
                    delta = datetime.fromisoformat(row['atLocal']) - datetime.fromisoformat(self.history['rows'][index - 1]['atLocal'])
                    self.assertEqual(delta, timedelta(days=1))

    def test_utc8_boundaries_and_closed_last_day(self):
        first, last = self.history['rows'][0], self.history['rows'][-1]
        self.assertEqual(first['openTimeUtc'], '2019-12-31T16:00:00+00:00')
        self.assertEqual(first['closeTimeUtcExclusive'], '2020-01-01T16:00:00+00:00')
        self.assertEqual(first['atLocal'], '2020-01-01T00:00:00+08:00')
        self.assertEqual(last['closeTimeUtcExclusive'], '2026-09-26T16:00:00+00:00')
        self.assertTrue(last['complete'])
        self.assertIn('BTCUSDT-1h-API-2026-09-26.json', last['sourceRefs'])
        self.assertEqual(self.history['source']['priorAudit']['recoveredHours'], 16)

    def test_missing_conflict_day_remains_null_and_reason_visible(self):
        row = next(r for r in self.history['rows'] if r['date'] == '2020-12-21')
        self.assertFalse(row['complete'])
        self.assertIsNone(row['open'])
        self.assertIn('official_source_revision_conflict', row['errors'])

    def test_lichun_day_uses_midnight_pillar_and_preserves_afternoon_boundary(self):
        row = next(r for r in self.history['rows'] if r['date'] == '2024-02-04')
        transition = next(t for t in self.history['transitions'] if t['atLocal'].startswith('2024-02-04'))
        self.assertEqual(row['pillars']['year'], '癸卯')
        self.assertEqual(transition['atLocal'], '2024-02-04T16:27:07+08:00')
        self.assertEqual(transition['atUtc'], '2024-02-04T08:27:07+00:00')
        self.assertEqual(transition['afterPillars']['year'], '甲辰')
        self.assertTrue(transition['changesYear'])
        self.assertTrue(transition['changesMonth'])

    def test_jie_changes_month_but_zhongqi_does_not(self):
        transitions = self.history['transitions']
        jingzhe = next(t for t in transitions if t['atLocal'].startswith('2024-03-05'))
        chunfen = next(t for t in transitions if t['atLocal'].startswith('2024-03-20'))
        self.assertEqual(jingzhe['termName'], '惊蛰')
        self.assertTrue(jingzhe['changesMonth'])
        self.assertFalse(chunfen['changesMonth'])
        self.assertEqual(chunfen['termName'], '春分')
        self.assertEqual(len(transitions), 162)
        self.assertEqual([t['atLocal'] for t in transitions], sorted(t['atLocal'] for t in transitions))

    def test_elements_label_stem_and_branch_separately(self):
        row = self.history['rows'][0]
        self.assertEqual(row['pillars']['day'], '癸卯')
        self.assertEqual(row['elements']['day'], {'stem': '水', 'branch': '木'})
        self.assertIsNone(self.history['marketDirection'])
        self.assertFalse(self.history['marketValidated'])

    def test_invalid_ohlc_not_drawable_and_bad_time_is_hard_failure(self):
        raw = deepcopy(self.source['dailyRows'][0])
        raw['high'] = '1'
        row = convert_row(raw)
        self.assertFalse(row['complete'])
        self.assertIn('invalid_ohlc_order', row['errors'])
        self.assertTrue(all(row[k] is None for k in ('open', 'high', 'low', 'close')))
        for field, value in [('open', True), ('close', 'NaN'), ('low', '-1')]:
            raw = deepcopy(self.source['dailyRows'][0])
            raw[field] = value
            self.assertFalse(convert_row(raw)['complete'])
        raw = deepcopy(self.source['dailyRows'][0])
        raw['openTimeUtc'] = '2020-01-01T00:00:00+00:00'
        with self.assertRaisesRegex(ChartIntegrityError, '日界'):
            convert_row(raw)

    def test_metadata_claims_preserve_previous_flags_and_audit_scope(self):
        source = self.history['source']
        self.assertEqual(source['inputSha256'], FROZEN_DAILY_SHA256)
        self.assertFalse(source['metadataFlagsPreserved']['registered'])
        self.assertFalse(source['metadataFlagsPreserved']['hourlyAggregationValidated'])
        self.assertFalse(source['priorAudit']['performedInThisBuild'])
        self.assertFalse(source['independentMarketRevalidationThisBuild'])
        self.assertEqual(source['sourceInterval'], '1h')
        self.assertEqual(self.history['interval'], '1d')

    def test_generation_is_reproducible(self):
        rebuilt = build_history()
        self.assertEqual(rebuilt['contentSha256'], self.history['contentSha256'])
        self.assertEqual(rebuilt, self.history)

    def test_source_hash_failure_refuses_to_serve_stale_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ('execution_registration.json', 'chart_history.json') + FROZEN_FILES + BUILD_FILES:
                target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(HERE / name, target)
            with (base / 'daily_input.json').open('a', encoding='utf-8') as stream:
                stream.write(' ')
            with self.assertRaisesRegex(ChartIntegrityError, '源文件哈希不匹配：daily_input.json'):
                load_history(base)

    def test_query_validation_and_detail_has_full_features(self):
        good = {'symbol': 'BTCUSDT', 'date': '2024-02-04', 'profile': 'BTC-BZ-V01'}
        self.assertEqual(parse_chart_query(parse.urlencode(good), detail=True), good)
        bad = ['symbol=ETHUSDT', 'symbol=BTCUSDT&symbol=', 'symbol=BTCUSDT&x=1', 'symbol=']
        for query in bad:
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_chart_query(query)
        for overrides in ({'profile': 'ETH-BAZI-A'}, {'date': '2026-09-27'}, {'date': '2024-02-30'}, {'date': '20240204'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                parse_chart_query(parse.urlencode({**good, **overrides}), detail=True)
        value = chart_detail(**{'symbol': good['symbol'], 'day': good['date'], 'profile': good['profile']})
        self.assertEqual(value['calendar']['pillars'], value['row']['pillars'])
        self.assertIsInstance(value['features']['features'], list)
        self.assertEqual(value['atLocal'], '2024-02-04T00:00:00+08:00')
        self.assertIsNone(value['marketDirection'])


class HistoricalChartRouteTests(unittest.TestCase):
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

    def call(self, path):
        try:
            with request.urlopen(self.origin + path, timeout=15) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_read_only_routes_avoid_full_astro_calculation(self):
        with (patch('app_server.calculate', side_effect=AssertionError('No full calculation')),
              patch('app_server.calculate_sky', side_effect=AssertionError('No sky lookup'))):
            status, history = self.call('/api/chart-history?symbol=BTCUSDT')
            self.assertEqual(status, 200)
            self.assertEqual(history['coverage']['missing'], 18)
            status, detail = self.call('/api/chart-detail?symbol=BTCUSDT&date=2020-02-09&profile=BTC-BZ-V01')
            self.assertEqual(status, 200)
            self.assertFalse(detail['row']['complete'])
        self.assertEqual(self.server.store.list(), [])

    def test_route_invalid_symbol_profile_duplicate_and_source_error(self):
        for path in ('/api/chart-history?symbol=ETHUSDT', '/api/chart-history?symbol=BTCUSDT&symbol=',
                     '/api/chart-detail?symbol=BTCUSDT&date=2020-01-01&profile=ETH-BAZI-A',
                     '/api/chart-detail?symbol=BTCUSDT&date=2030-01-01&profile=BTC-BZ-V01'):
            self.assertEqual(self.call(path)[0], 400)
        with patch('app_server.load_history', side_effect=ChartIntegrityError('源文件哈希不匹配')):
            status, value = self.call('/api/chart-history?symbol=BTCUSDT')
        self.assertEqual(status, 503)
        self.assertIn('源文件哈希不匹配', value['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
