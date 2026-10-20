"""Synthetic review metrics and registration/route safety; no real outcome scoring."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error, parse, request

from app_server import make_server
from build_rule_events import freeze, run_once
from historical_chart import HERE, ChartIntegrityError
from rule_events import (PROTOCOL, PROFILE, RULE_IDS, RuleEventIntegrityError, STARTED_FILE,
                         digest, input_hashes, matches_at, parse_event_query,
                         summarize, verify_registration, window_outcome)


def fixture_rows(closes, opening=100, start='2024-12-27'):
    first = date.fromisoformat(start)
    return [{'date': (first + timedelta(days=i)).isoformat(), 'complete': True,
             'open': opening if i == 0 else closes[i-1], 'close': close,
             'high': max(opening, close) * 1.5, 'low': min(opening, close) * .5}
            for i, close in enumerate(closes)]


class RuleWindowTests(unittest.TestCase):
    def test_horizon_counts_trigger_day_and_return_is_percent(self):
        rows = fixture_rows([110, 90, 120])
        one = window_outcome(rows, 0, 1)
        three = window_outcome(rows, 0, 3)
        self.assertEqual(one['startDate'], one['endDate'])
        self.assertEqual(three['endDate'], '2024-12-29')
        self.assertAlmostEqual(one['returnPct'], 10)
        self.assertAlmostEqual(three['returnPct'], 20)

    def test_drawdown_manual_peak_to_close_path(self):
        rows = fixture_rows([110, 90, 120])
        value = window_outcome(rows, 0, 3)
        self.assertAlmostEqual(value['maxDrawdownPct'], (90 / 110 - 1) * 100)
        # Day highs/lows and intermediate openings are deliberately irrelevant.
        rows[1].update(high=9999, low=1, open=999)
        self.assertEqual(window_outcome(rows, 0, 3), value)
        self.assertAlmostEqual(window_outcome(fixture_rows([90]), 0, 1)['maxDrawdownPct'], -10)
        self.assertEqual(window_outcome(fixture_rows([110, 120, 130]), 0, 3)['maxDrawdownPct'], 0)

    def test_any_gap_masks_whole_window_without_imputation(self):
        rows = fixture_rows([110, 120, 130])
        rows[1].update(complete=False, open=None, close=None)
        value = window_outcome(rows, 0, 3)
        self.assertEqual(value['status'], 'missing')
        self.assertEqual(value['missingDates'], ['2024-12-28'])
        self.assertIsNone(value['returnPct'])
        self.assertIsNone(value['maxDrawdownPct'])
        self.assertEqual(window_outcome(rows, 0, 1)['status'], 'scored')

    def test_dataset_tail_and_period_boundary_pending_are_distinct(self):
        rows = fixture_rows([100] * 10)
        period = window_outcome(rows, 3, 3, period_end='2024-12-31')
        tail = window_outcome(rows, 9, 3, period_end='2025-01-05')
        self.assertEqual((period['status'], period['reason']), ('pending', 'period_boundary'))
        self.assertEqual((tail['status'], tail['reason']), ('pending', 'dataset_tail'))
        self.assertIsNone(period['returnPct'])
        self.assertIsNone(tail['maxDrawdownPct'])

    def test_window_never_reads_future_beyond_target(self):
        rows = fixture_rows([110, 90, 120, 140, 160])
        previous = window_outcome(rows, 0, 3)
        rows[3].update(close=1, complete=False)
        rows[4]['close'] = 999999
        self.assertEqual(previous, window_outcome(rows, 0, 3))

    def test_noncontinuous_calendar_or_invalid_complete_price_rejected(self):
        rows = fixture_rows([110, 120, 130])
        rows[1]['date'] = '2024-12-30'
        with self.assertRaisesRegex(RuleEventIntegrityError, '日期不连续'):
            window_outcome(rows, 0, 3)
        rows = fixture_rows([110, 120, 130])
        rows[1]['close'] = float('nan')
        with self.assertRaisesRegex(RuleEventIntegrityError, '无效价格'):
            window_outcome(rows, 0, 3)

    def test_baseline_is_exact_nontrigger_partition_same_period_and_mask(self):
        rows = fixture_rows([100, 110, 90, 100, 120, 130, 140, 150])
        rows[2]['complete'] = False
        daily = [{'date': row['date'], 'matches': [{'ruleId': 'BZ-E01'}] if i % 2 == 0 else []}
                 for i, row in enumerate(rows)]
        period = {'id': 'synthetic', 'start': '2024-12-27', 'end': '2024-12-31'}
        result = summarize(daily, rows, [period])
        item = next(x for x in result if x['ruleId'] == 'BZ-E01' and x['horizonDays'] == 1)
        self.assertEqual(item['triggered']['eligible'], 3)
        self.assertEqual(item['baseline']['eligible'], 2)
        self.assertEqual(item['triggered']['missing'], 1)
        self.assertEqual(item['baseline']['missing'], 0)
        self.assertEqual(item['triggered']['scored'] + item['baseline']['scored'], 4)
        for record in result:
            for group in ('triggered', 'baseline'):
                values = record[group]
                self.assertEqual(values['eligible'], values['scored'] + values['missing'] + values['pending'])
            self.assertEqual(record['triggered']['eligible'] + record['baseline']['eligible'], 5)
        three = next(x for x in result if x['ruleId'] == 'BZ-E01' and x['horizonDays'] == 3)
        self.assertEqual(three['triggered']['pendingReasons']['period_boundary'], 1)
        self.assertEqual(three['baseline']['pendingReasons']['period_boundary'], 1)


class RuleMatchingTests(unittest.TestCase):
    def test_day_stem_rules_have_no_price_inputs_and_correct_stems(self):
        values = [matches_at(f'2020-01-{day:02d}T00:00:00+08:00') for day in range(1, 11)]
        for value in values:
            stem = value['pillars']['day'][0]
            identities = {m['ruleId'] for m in value['matches']}
            self.assertEqual('BZ-E01' in identities, stem in '庚辛壬癸')
            self.assertEqual('BZ-E02' in identities, stem in '丙丁')
        self.assertEqual(sum(any(m['ruleId'] == 'BZ-E01' for m in v['matches']) for v in values), 4)
        self.assertEqual(sum(any(m['ruleId'] == 'BZ-E02' for m in v['matches']) for v in values), 2)

    def test_structural_rules_retain_natal_positions_and_count_day_once(self):
        # 2020-01-04 丙午, opposite both natal 子 positions.
        noon_branch = matches_at('2020-01-04T00:00:00+08:00')
        clash = [m for m in noon_branch['matches'] if m['ruleId'] == 'BZ-E03']
        self.assertEqual(len(clash), 1)
        self.assertEqual({e['natalSlot'] for e in clash[0]['edges']}, {'natal.year', 'natal.month'})
        self.assertTrue(all(e['natalBranch'] == '子' and e['flowBranch'] == '午' for e in clash[0]['edges']))
        union = matches_at('2020-01-11T00:00:00+08:00')
        match = next(m for m in union['matches'] if m['ruleId'] == 'BZ-E04')
        self.assertEqual(match['featureId'], 'BZ-S01')
        self.assertEqual(len(match['edges']), 2)

    def test_wrong_profile_and_nonmidnight_rejected(self):
        with self.assertRaises(ValueError):
            matches_at('2024-01-01T12:00:00+08:00')
        with self.assertRaises(ValueError):
            matches_at('2024-01-01T00:00:00+08:00', 'ETH-BAZI-A')
        for query in ('symbol=BTCUSDT&profile=ETH-BAZI-A', 'symbol=ETHUSDT&profile=BTC-BZ-V01',
                      'symbol=BTCUSDT&profile=BTC-BZ-V01&profile=', 'symbol=BTCUSDT',
                      'symbol=BTCUSDT&profile=BTC-BZ-V01&x=1'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_event_query(query)


class RegistrationTests(unittest.TestCase):
    def test_registration_before_run_tamper_and_repeat_guard(self):
        # Copy fixed inputs into a temporary bundle; this test never computes
        # actual outcome summaries and never touches the delivered registration.
        hashes = input_hashes()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in set(hashes) | {'execution_registration.json'}:
                destination = base / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(HERE / name, destination)
            frozen = freeze(base)
            registration = verify_registration(base)
            self.assertFalse(registration['scoringRun'])
            self.assertEqual(frozen['registrationSha256'], registration['registrationSha256'])
            with self.assertRaises(RuleEventIntegrityError):
                freeze(base)
            (base / STARTED_FILE).write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(RuleEventIntegrityError, '禁止重复'):
                run_once(base)
            original = deepcopy(registration)
            registration['protocol']['horizons'] = [1, 2]
            registration['registrationSha256'] = digest({k: v for k, v in registration.items() if k != 'registrationSha256'})
            (base / 'rule_event_registration.json').write_text(json.dumps(registration), encoding='utf-8')
            with self.assertRaisesRegex(RuleEventIntegrityError, '冻结版本'):
                verify_registration(base)
            (base / 'rule_event_registration.json').write_text(json.dumps(original), encoding='utf-8')
            with (base / 'rule_event_catalog.json').open('a', encoding='utf-8') as stream:
                stream.write(' ')
            with self.assertRaisesRegex(RuleEventIntegrityError, '输入哈希不匹配'):
                verify_registration(base)


class RuleEventRouteTests(unittest.TestCase):
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
                return response.status, json.loads(response.read()), response.headers
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read()), exc.headers

    def test_read_only_route_download_and_invalid_query(self):
        fake = {'schemaVersion': 1, 'protocol': PROTOCOL, 'daily': [], 'summary': []}
        with (patch('app_server.load_rule_events', return_value=fake),
              patch('app_server.calculate', side_effect=AssertionError('No full calculation'))):
            code, result, headers = self.call('/api/rule-events?symbol=BTCUSDT&profile=BTC-BZ-V01')
            self.assertEqual(code, 200)
            self.assertEqual(result, fake)
            code, result, headers = self.call('/downloads/rule-events.json')
            self.assertEqual(code, 200)
            self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(self.server.store.list(), [])
        self.assertEqual(self.call('/api/rule-events?symbol=BTCUSDT&profile=ETH-BAZI-A')[0], 400)
        self.assertEqual(self.call('/downloads/rule-events.json?profile=ETH-BAZI-A')[0], 400)
        with patch('app_server.load_rule_events', side_effect=RuleEventIntegrityError('注册哈希不匹配')):
            code, result, _ = self.call('/api/rule-events?symbol=BTCUSDT&profile=BTC-BZ-V01')
        self.assertEqual(code, 503)
        self.assertIn('注册哈希不匹配', result['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
