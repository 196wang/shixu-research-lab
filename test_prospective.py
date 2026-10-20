"""Temporary-only forward-validation tests; never register the user's study."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import prospective as p


class Clock:
    def __init__(self, value='2026-09-27T12:00:00+08:00'):
        self.value = p.instant(value)

    def __call__(self):
        return self.value

    def set(self, value):
        self.value = p.instant(value)


class Market:
    def __init__(self):
        self.state = {'symbol': p.SYMBOL, 'generation': 1, 'last_success': '2026-09-27T04:00:00+00:00'}
        self.candles, self.gaps, self.reads = {}, {}, 0

    def _snapshot(self, symbol):
        assert symbol == p.SYMBOL
        self.reads += 1
        return deepcopy(self.state), deepcopy(self.candles), deepcopy(self.gaps)


def candle(day, close='100', opening='100', **changes):
    dt = p.day_start(date.fromisoformat(day))
    key = int(dt.timestamp() * 1000)
    row = {'symbol': p.SYMBOL, 'open_ms': key, 'close_ms': key + p.DAY_MS - 1,
           'open': opening, 'high': '200', 'low': '1', 'close': close, 'volume': '7',
           'is_closed': 1, 'conflicted': 0, 'request_id': 'request-fixture', 'body_sha256': 'a' * 64,
           'updated': (dt + timedelta(days=1)).isoformat()}
    row.update(changes)
    return key, row


class ProspectiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol = p.load_protocol(p.HERE)
        cls.schedule = p.make_schedule(cls.protocol)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock, self.market = Clock(), Market()
        self.hashes = {'fixture-rule': 'a' * 64}
        self.dep_patch = patch.object(p, 'dependencies', side_effect=lambda base: dict(self.hashes))
        self.dep_patch.start()
        self.addCleanup(self.dep_patch.stop)
        self.schedule_patch = patch.object(p, 'make_schedule', side_effect=lambda protocol: deepcopy(self.schedule))
        self.schedule_patch.start()
        self.addCleanup(self.schedule_patch.stop)
        self.study = p.ProspectiveStudy(self.temp.name, self.market, clock=self.clock)

    def register(self):
        return self.study.register()['status']

    def populate(self, start='2026-09-28', end='2027-01-29', close='110'):
        day, last = date.fromisoformat(start), date.fromisoformat(end)
        while day <= last:
            key, row = candle(day.isoformat(), close=close)
            self.market.candles[key] = row
            day += timedelta(days=1)

    def test_complete_schedule_and_idempotent_registration(self):
        status = self.register()
        self.assertEqual(len(status['schedule']), 95)
        self.assertEqual(status['schedule'][0]['date'], '2026-09-28')
        self.assertEqual(status['schedule'][-1]['date'], '2026-12-31')
        self.assertEqual(self.market.reads, 0)
        for day in status['schedule']:
            self.assertEqual(set(day['rules']), set(p.DIRECTIONS))
            for rule_id, direction in p.DIRECTIONS.items():
                self.assertEqual(day['rules'][rule_id]['candidateDirection'], direction)
                self.assertEqual(day['rules'][rule_id]['triggered'], rule_id in {m['ruleId'] for m in day['matches']})
        self.clock.set('2027-02-01T00:00:00+08:00')
        again = self.study.register()
        self.assertFalse(again['created'])
        self.assertEqual(again['status']['registration'], status['registration'])

    def test_deadline_exact_and_late_denied(self):
        for now in ('2026-09-28T00:00:00+08:00', '2026-10-01T00:00:00+08:00'):
            self.clock.set(now)
            with self.assertRaises(p.ProspectiveError):
                self.study.register()
        self.assertEqual(self.study.status()['status'], 'registration_closed')
        self.assertIsNone(self.study.export()['registration'])

    def test_build_crossing_deadline_denied_without_partial_registration(self):
        def crossed(protocol):
            self.clock.set('2026-09-28T00:00:00+08:00')
            return deepcopy(self.schedule)
        with patch.object(p, 'make_schedule', side_effect=crossed):
            with self.assertRaises(p.ProspectiveError):
                self.study.register()
        self.assertIsNone(self.study.export()['registration'])
        self.assertEqual(self.market.reads, 0)

    def test_final_insert_time_gate(self):
        early = p.instant('2026-09-27T23:59:59+08:00')
        late = p.instant('2026-09-28T00:00:00+08:00')
        with patch.object(self.study, '_now', side_effect=[early, early, late]):
            with self.assertRaises(p.ProspectiveError):
                self.study.register()
        self.assertIsNone(self.study.export()['registration'])

    def test_registration_uses_final_transaction_timestamp(self):
        early = p.instant('2026-09-27T12:00:00+08:00')
        final = p.instant('2026-09-27T12:00:02+08:00')
        with patch.object(self.study, '_now', side_effect=[early, early, final, final]):
            result = self.study.register()
        self.assertEqual(result['status']['registration']['registeredAtUtc'], final.isoformat())

    def test_first_evaluation_cannot_precede_registration_or_reverse_during_work(self):
        self.register()
        self.clock.set('2026-09-27T11:59:59+08:00')
        with self.assertRaises(p.ProspectiveError):
            self.study.evaluate()
        normal = p.instant('2026-09-29T00:00:00+08:00')
        reversed_at = normal - timedelta(seconds=1)
        with patch.object(self.study, '_now', side_effect=[normal, reversed_at]):
            with self.assertRaises(p.ProspectiveError):
                self.study.evaluate()
        self.assertEqual(self.study.export()['evaluations'], [])

    def test_dependency_change_prevents_reuse_but_preserves_readable_export(self):
        original = self.register()['registration']
        self.hashes['fixture-rule'] = 'b' * 64
        status = self.study.status()
        self.assertEqual(status['status'], 'integrity_error')
        self.assertFalse(status['canEvaluate'])
        with self.assertRaises(p.ProspectiveError):
            self.study.register()
        with self.assertRaises(p.ProspectiveError):
            self.study.evaluate()
        self.assertEqual(self.study.export()['registration']['id'], original['id'])
        self.assertEqual(self.market.reads, 0)

    def test_future_prices_do_not_score_before_due(self):
        self.register()
        self.populate()
        self.clock.set('2026-09-28T23:59:59+08:00')
        report = self.study.evaluate()['report']
        self.assertEqual(report['outcomeCounts']['1'], {'pending': 95, 'missing': 0, 'scored': 0})
        self.assertGreater(report['marketSnapshot']['excludedFutureRows'], 0)
        self.clock.set('2026-09-29T00:00:00+08:00')
        report = self.study.evaluate()['report']
        self.assertEqual(report['outcomeCounts']['1']['scored'], 1)
        self.assertEqual(report['outcomeCounts']['3']['scored'], 0)
        self.assertEqual(report['daily'][0]['horizons']['1']['endDate'], '2026-09-28')

    def test_decimal_directions_ties_and_close_path_drawdown(self):
        rows = dict([candle('2026-09-28', '110'), candle('2026-09-29', '90'), candle('2026-09-30', '120')])
        asof = p.instant('2026-10-01T00:00:00+08:00')
        result = p.evaluate_window('2026-09-28', 3, rows, {}, asof)
        self.assertAlmostEqual(result['returnPct'], 20)
        self.assertAlmostEqual(result['maxDrawdownPct'], (90 / 110 - 1) * 100)
        small = dict([candle('2026-09-28', '100.000000000000000001')])
        self.assertEqual(p.evaluate_window('2026-09-28', 1, small, {}, asof)['directions']['up'], 'hit')
        ties = dict([candle('2026-09-28')])
        tie = p.evaluate_window('2026-09-28', 1, ties, {}, asof)
        self.assertEqual(tie['directions'], {'up': 'tie', 'down': 'tie'})
        summary = p.group_summary([result, tie], 'up')
        self.assertEqual((summary['hits'], summary['ties'], summary['hitRatePct']), (1, 1, 50))

    def test_any_gap_conflict_unclosed_or_invalid_quote_masks_window(self):
        asof = p.instant('2026-10-01T00:00:00+08:00')
        for patch_values, gaps in [({'is_closed': 0}, {}), ({'conflicted': 1}, {}),
                                   ({'symbol': 'ETHUSDT'}, {}), ({'close': 'NaN'}, {}),
                                   ({'body_sha256': ''}, {}), ({'updated': '2027-01-01T00:00:00+08:00'}, {}),
                                   ({}, {candle('2026-09-29')[0]: ['source_conflict']})]:
            rows = dict([candle('2026-09-28'), candle('2026-09-29', **patch_values), candle('2026-09-30')])
            result = p.evaluate_window('2026-09-28', 3, rows, gaps, asof)
            self.assertEqual(result['status'], 'missing')
            self.assertEqual(result['missingDates'], ['2026-09-29'])
            self.assertIsNone(result['returnPct'])
        missing = p.evaluate_window('2026-09-28', 1, {}, {}, asof)
        self.assertEqual(missing['status'], 'missing')

    def test_cross_year_window_due_only_january_30(self):
        self.populate('2026-12-31', '2027-01-29')
        early = p.evaluate_window('2026-12-31', 30, self.market.candles, {}, p.instant('2027-01-29T23:59:59+08:00'))
        self.assertEqual(early['status'], 'pending')
        result = p.evaluate_window('2026-12-31', 30, self.market.candles, {}, p.instant('2027-01-30T00:00:00+08:00'))
        self.assertEqual(result['status'], 'scored')
        self.assertEqual(result['endDate'], '2027-01-29')
        self.assertEqual(result['endExclusiveAt'], '2027-01-30T00:00:00+08:00')

    def test_same_schedule_baselines_down_direction_and_no_direction(self):
        self.register()
        self.populate(close='90')
        self.clock.set('2027-01-30T00:00:00+08:00')
        report = self.study.evaluate()['report']
        self.assertEqual(len(report['summary']), 16)
        for item in report['summary']:
            self.assertEqual(item['triggered']['eligible'] + item['baseline']['eligible'], 95)
            self.assertEqual(item['triggered']['scored'] + item['baseline']['scored'], 95)
            if item['ruleId'] == 'BZ-E02':
                self.assertEqual(item['triggered']['hitRatePct'], 100)
                self.assertEqual(item['baseline']['hitRatePct'], 100)
            if item['candidateDirection'] is None:
                for group in ('triggered', 'baseline'):
                    for field in ('hits', 'misses', 'ties', 'hitRatePct'):
                        self.assertIsNone(item[group][field])
        self.assertEqual(report['daily'][0]['horizons']['3']['endDate'], '2026-09-30')
        self.assertEqual(report['daily'][1]['horizons']['3']['endDate'], '2026-10-01')

    def test_append_only_source_snapshot_and_read_only_gets(self):
        self.register()
        self.clock.set('2026-09-29T00:00:00+08:00')
        before_market = deepcopy(self.market.__dict__)
        first = self.study.evaluate()['report']
        self.assertEqual(first['daily'][0]['horizons']['1']['status'], 'missing')
        self.populate('2026-09-28', '2026-09-28')
        self.market.state['generation'] = 2
        second = self.study.evaluate()['report']
        self.assertEqual(second['daily'][0]['horizons']['1']['status'], 'scored')
        self.assertEqual(second['previousReportSha256'], first['contentSha256'])
        self.assertNotEqual(first['marketSnapshotSha256'], second['marketSnapshotSha256'])
        db_before = self.study.path.read_bytes()
        exported = self.study.export()
        self.study.status()
        self.assertEqual(db_before, self.study.path.read_bytes())
        self.assertEqual(exported['evaluations'][0], first)
        self.assertEqual(len(exported['evaluations']), 2)
        self.assertEqual(self.market.reads, 2)
        self.assertEqual(before_market['gaps'], self.market.gaps)
        with self.study.connect() as db:
            for sql in ('DELETE FROM prospective_registrations', 'UPDATE prospective_evaluations SET report=report'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_clock_rollback_and_corrupt_audit_chain_rejected(self):
        self.register()
        self.clock.set('2026-09-29T00:00:00+08:00')
        self.study.evaluate()
        self.clock.set('2026-09-28T23:00:00+08:00')
        with self.assertRaises(p.ProspectiveError):
            self.study.evaluate()
        with self.study.connect() as db:
            db.execute('DROP TRIGGER prospective_eval_no_update')
            db.execute("UPDATE prospective_evaluations SET report_sha='broken'")
        with self.assertRaises(p.ProspectiveError):
            self.study.export()

    def test_actual_dependency_manifest_and_protocol_tamper(self):
        self.dep_patch.stop()
        manifest = p.dependencies(p.HERE)
        for name in ('prospective.py', 'prospective_protocol.json', 'test_prospective.py', 'market_feed.py'):
            self.assertEqual(manifest[name], p.file_sha(Path(p.HERE) / name))
        self.assertGreater(len(manifest), 50)
        self.register()
        self.assertEqual(self.study.status()['status'], 'registered')
        alternate = Path(self.temp.name) / 'altered'
        alternate.mkdir()
        protocol = deepcopy(self.protocol)
        protocol['rules'][1]['candidateDirection'] = 'up'
        (alternate / 'prospective_protocol.json').write_text(json.dumps(protocol), encoding='utf-8')
        with self.assertRaises(p.ProspectiveError):
            p.load_protocol(alternate)


if __name__ == '__main__':
    unittest.main()
