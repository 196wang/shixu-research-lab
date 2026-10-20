"""Temporary ledger tests; no production forecasts, network, or cache writes."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import prediction_ledger as p


class Clock:
    def __init__(self, value='2026-09-27T12:00:00+08:00'):
        self.set(value)

    def set(self, value):
        self.value = datetime.fromisoformat(value)

    def __call__(self):
        return self.value


class Market:
    def __init__(self):
        self.states = {s: {'symbol': s, 'generation': 1, 'last_success': '2026-09-27T04:00:00+00:00'} for s in ('BTCUSDT', 'ETHUSDT')}
        self.candles = {s: {} for s in self.states}
        self.gaps = {s: {} for s in self.states}
        self.calls = []

    def _snapshot(self, symbol):
        self.calls.append(symbol)
        return deepcopy(self.states[symbol]), deepcopy(self.candles[symbol]), deepcopy(self.gaps[symbol])

    def put(self, day, close='110', high='120', low='80', symbol='BTCUSDT', **changes):
        day = date.fromisoformat(day)
        at = datetime(day.year, day.month, day.day, tzinfo=p.LOCAL)
        key = int(at.timestamp() * 1000)
        row = {'symbol': symbol, 'open_ms': key, 'close_ms': key + 86_400_000 - 1,
               'open': '100', 'close': close, 'high': high, 'low': low, 'volume': '1',
               'is_closed': 1, 'conflicted': 0, 'request_id': 'fixture-' + day.isoformat(),
               'body_sha256': 'a' * 64, 'updated': (at + timedelta(days=1)).isoformat()}
        row.update(changes)
        self.candles[symbol][key] = row


def body(**changes):
    result = {'symbol': 'BTCUSDT', 'kind': 'up', 'targetDate': '2026-10-01', 'horizonDays': 1,
              'title': '候选判断', 'note': '待复核',
              'rules': [{'id': 'R1', 'family': 'BAZI', 'direction': 'up', 'version': '1'}], 'invalidation': None}
    result.update(changes)
    return result


class PredictionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock, self.market = Clock(), Market()
        self.ledger = p.PredictionLedger(self.temp.name, self.market, self.clock)

    def test_create_deduplicates_and_only_server_sets_metadata(self):
        first = self.ledger.create(body())
        duplicate = self.ledger.create(body())
        self.assertTrue(first['created'])
        self.assertFalse(duplicate['created'])
        record = first['record']
        self.assertEqual(record['id'], record['rootId'])
        self.assertEqual(record['id'], duplicate['record']['id'])
        self.assertTrue(record['prospectiveEligible'])
        self.assertFalse(record['lateRevision'])
        self.assertEqual(record['version'], 1)
        self.assertIsNone(record['parentId'])
        self.assertEqual(self.market.calls, [])
        self.assertIn('confluenceSnapshot', record)
        self.assertEqual(len(record['recordSha256']), 64)
        with self.assertRaises(p.PredictionLedgerError):
            self.ledger.create({**body(), 'createdAtUtc': '1900-01-01T00:00:00+00:00'})

    def test_body_validation_and_normalized_duplicate_rules(self):
        for invalid in (body(symbol='OTHER'), body(horizonDays=True), body(kind='high', horizonDays=3),
                        body(targetDate='20261001'), body(title=' '), body(invalidation={'type': 'close_below', 'price': '1e99999999'}),
                        body(invalidation={'type': 'close_below', 'price': 1}), body(rules=[body()['rules'][0]] * 2)):
            with self.subTest(invalid=invalid):
                with self.assertRaises(p.PredictionLedgerError):
                    self.ledger.create(invalid)
        first = self.ledger.create(body(invalidation={'type': 'close_below', 'price': '070.000'}))
        self.assertEqual(first['record']['invalidation']['price'], '70')
        again = self.ledger.create(body(invalidation={'type': 'close_below', 'price': '70'}))
        self.assertFalse(again['created'])
        with self.assertRaises(p.PredictionLedgerError):
            self.ledger.revise(None, body())

    def test_deadlines_and_final_write_gate(self):
        self.clock.set('2026-09-28T00:00:00+08:00')
        with self.assertRaises(p.PredictionLedgerError):
            self.ledger.create(body(kind='high'))  # Oct 1 minus 3 days.
        self.clock.set('2026-10-01T00:00:00+08:00')
        with self.assertRaises(p.PredictionLedgerError):
            self.ledger.create(body())
        early = datetime.fromisoformat('2026-09-30T23:59:59+08:00')
        late = datetime.fromisoformat('2026-10-01T00:00:00+08:00')
        with patch.object(self.ledger, '_now', side_effect=[early, early, late]):
            with self.assertRaises(p.PredictionLedgerError):
                self.ledger.create(body())
        self.assertEqual(self.ledger.list()['records'], [])

    def test_revisions_keep_original_and_compare_both_deadlines(self):
        original = self.ledger.create(body())['record']
        early = self.ledger.revise(original['id'], body(title='早修订', kind='down'))['record']
        self.assertEqual((early['rootId'], early['parentId'], early['version']), (original['id'], original['id'], 2))
        self.assertTrue(early['prospectiveEligible'])
        self.clock.set('2026-10-02T00:00:00+08:00')
        late = self.ledger.revise(early['id'], body(title='后移目标', targetDate='2026-11-01'))['record']
        self.assertTrue(late['lateRevision'])
        self.assertFalse(late['prospectiveEligible'])
        self.assertEqual(late['originalWindowStartAt'], original['windowStartAt'])
        self.assertEqual(late['confluenceSnapshot']['activeRoots'], 0)
        repeated = self.ledger.revise(early['id'], body(title='后移目标', targetDate='2026-11-01'))
        self.assertFalse(repeated['created'])
        self.assertEqual(self.ledger.list()['records'][0]['kind'], 'up')
        self.assertEqual(len(self.ledger.list()['records']), 3)

    def test_manual_invalidation_appends_and_keeps_original_scored(self):
        record = self.ledger.create(body())['record']
        event = self.ledger.invalidate(record['id'], {'reason': '条件改变'})
        self.assertTrue(event['created'])
        self.assertEqual(event['invalidation']['confluenceSnapshot']['activeRoots'], 0)
        self.assertFalse(self.ledger.invalidate(record['id'], {'reason': '条件改变'})['created'])
        self.market.put('2026-10-01')
        self.clock.set('2026-10-02T00:00:00+08:00')
        result = self.ledger.evaluate()['ledger']
        self.assertEqual(result['latestReports'][0]['score']['status'], 'correct')
        self.assertEqual(result['summary']['originals']['counts']['correct'], 1)
        self.assertEqual(result['summary']['originals']['counts']['invalidated'], 1)
        self.assertEqual(result['summary']['originals']['directionMetrics']['correctRatePct'], 100)

    def test_late_revised_answer_does_not_improve_original_rate(self):
        record = self.ledger.create(body())['record']
        self.market.put('2026-10-01', close='90')
        self.clock.set('2026-10-02T00:00:00+08:00')
        self.ledger.revise(record['id'], body(kind='down', title='迟到修订'))
        result = self.ledger.evaluate()['ledger']
        original, revised = result['summary']['originals'], result['summary']['revisions']
        self.assertEqual(original['counts']['incorrect'], 1)
        self.assertEqual(original['directionMetrics']['correctRatePct'], 0)
        self.assertEqual(revised['counts']['correct'], 1)
        self.assertEqual(revised['directionMetrics']['evaluated'], 0)
        self.assertIsNone(revised['directionMetrics']['correctRatePct'])

    def test_evaluate_per_asset_once_and_preserve_all_report_snapshots(self):
        a = self.ledger.create(body())['record']
        self.ledger.create(body(symbol='ETHUSDT'))
        self.ledger.revise(a['id'], body(title='第二版本', kind='down'))
        self.clock.set('2026-10-02T00:00:00+08:00')
        self.market.put('2026-10-01')
        self.market.put('2026-10-01', symbol='ETHUSDT')
        before = deepcopy((self.market.states, self.market.candles, self.market.gaps))
        first = self.ledger.evaluate()
        self.assertEqual(self.market.calls, ['BTCUSDT', 'ETHUSDT'])
        self.assertEqual(first['evaluation']['reportCount'], 3)
        saved = deepcopy(self.ledger.export()['reports'])
        self.market.states['BTCUSDT']['generation'] = 2
        self.ledger.evaluate()
        exported = self.ledger.export()
        self.assertEqual(exported['reports'][:3], saved)
        self.assertEqual(len(exported['reports']), 6)
        for report in exported['reports'][3:]:
            previous = next(r for r in saved if r['recordId'] == report['recordId'])
            self.assertEqual(report['previousReportSha256'], previous['contentSha256'])
            self.assertIn('selectedDays', report['marketSnapshot'])
            self.assertIn('confluenceSnapshot', report)
        self.assertEqual(before[1:], (self.market.candles, self.market.gaps))

    def test_automatic_invalidation_maps_to_record_and_confluence(self):
        record = self.ledger.create(body(invalidation={'type': 'close_below', 'price': '95'}))['record']
        self.market.put('2026-09-28', close='90')
        self.clock.set('2026-09-29T00:00:00+08:00')
        result = self.ledger.evaluate()
        self.assertTrue(result['ledger']['records'][0]['invalidated'])
        self.assertEqual(result['ledger']['latestReports'][0]['score']['status'], 'pending')
        self.assertEqual(result['evaluation']['confluenceSnapshot']['activeRoots'], 0)
        self.assertEqual(result['ledger']['records'][0]['id'], record['id'])

    def test_timing_tolerance_summary_separate_from_direction(self):
        self.ledger.create(body(kind='high', targetDate='2026-10-05'))
        first = date(2026, 9, 30)
        for i in range(11):
            self.market.put((first + timedelta(days=i)).isoformat(), high='130' if i == 6 else '120')
        self.clock.set('2026-10-11T00:00:00+08:00')
        result = self.ledger.evaluate()['ledger']
        score = result['latestReports'][0]['score']
        self.assertEqual(score['status'], 'within_tolerance')
        self.assertEqual(score['timeResult']['signedErrorDays'], 1)
        summary = result['summary']['originals']
        self.assertEqual(summary['counts']['within'], 1)
        self.assertEqual(summary['timeMetrics']['withinToleranceHitRatePct'], 100)
        self.assertEqual(summary['directionMetrics']['evaluated'], 0)

    def test_read_only_get_append_guards_and_hash_tamper(self):
        record = self.ledger.create(body())['record']
        before = self.ledger.path.read_bytes()
        self.ledger.list()
        self.ledger.export()
        self.assertEqual(before, self.ledger.path.read_bytes())
        with self.ledger.connect() as db:
            for sql in ('DELETE FROM prediction_versions', 'UPDATE prediction_versions SET version=2'):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)
        with self.ledger.connect() as db:
            db.execute('DROP TRIGGER prediction_versions_no_update')
            db.execute("UPDATE prediction_versions SET record_sha='tampered' WHERE id=?", (record['id'],))
        with self.assertRaises(p.PredictionLedgerError) as raised:
            self.ledger.export()
        self.assertEqual(raised.exception.status_code, 503)

    def test_clock_rollback_rejects_create_revision_invalidation_and_review(self):
        record = self.ledger.create(body())['record']
        self.clock.set('2026-09-27T11:59:59+08:00')
        for call in (lambda: self.ledger.create(body(title='另一个')),
                     lambda: self.ledger.revise(record['id'], body(title='修订')),
                     lambda: self.ledger.invalidate(record['id'], {'reason': '失效'}), self.ledger.evaluate):
            with self.assertRaises(p.PredictionLedgerError) as raised:
                call()
            self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.market.calls, [])

    def test_concurrent_identical_create_is_single_root(self):
        other = p.PredictionLedger(self.temp.name, self.market, self.clock)
        results, errors = [], []
        def save(ledger):
            try:
                results.append(ledger.create(body()))
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=save, args=(ledger,)) for ledger in (self.ledger, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r['created'] for r in results), [False, True])
        self.assertEqual(len(self.ledger.list()['records']), 1)

    def test_empty_ledger_and_failed_market_read_do_not_append(self):
        with self.assertRaises(p.PredictionLedgerError):
            self.ledger.evaluate()
        self.ledger.create(body())
        with patch.object(self.market, '_snapshot', side_effect=RuntimeError('fixture')):
            with self.assertRaises(p.PredictionLedgerError) as raised:
                self.ledger.evaluate()
            self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.ledger.list()['evaluationCount'], 0)

    def test_implementation_changed_report_is_descriptive_only(self):
        self.ledger.create(body())
        self.market.put('2026-10-01')
        self.clock.set('2026-10-02T00:00:00+08:00')
        changed = p.implementation_hashes(self.ledger.base)
        changed['prediction_scoring.py'] = 'b' * 64
        with patch.object(p, 'implementation_hashes', return_value=changed):
            result = self.ledger.evaluate()['ledger']
        self.assertTrue(result['latestReports'][0]['implementationChangedSinceRegistration'])
        original = result['summary']['originals']
        self.assertEqual(original['counts']['correct'], 1)
        self.assertEqual(original['algorithmChangedCount'], 1)
        self.assertEqual(original['directionMetrics']['evaluated'], 0)
        self.assertIsNone(original['directionMetrics']['correctRatePct'])

    def test_implementation_change_during_invalidation_rolls_back(self):
        record = self.ledger.create(body())['record']
        old = p.implementation_hashes(self.ledger.base)
        changed = {**old, 'prediction_confluence.py': 'b' * 64}
        with patch.object(p, 'implementation_hashes', side_effect=[old, changed]):
            with self.assertRaises(p.PredictionLedgerError) as raised:
                self.ledger.invalidate(record['id'], {'reason': '实现发生变化'})
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.ledger.list()['invalidations'], [])

    def test_confirmed_automatic_invalidation_does_not_revive_after_missing_data(self):
        self.ledger.create(body(invalidation={'type': 'close_below', 'price': '95'}))
        self.market.put('2026-09-28', close='90')
        self.clock.set('2026-09-29T00:00:00+08:00')
        first = self.ledger.evaluate()['ledger']
        original_report = first['latestReports'][0]['id']
        self.assertTrue(first['records'][0]['invalidated'])
        self.market.put('2026-09-28', close='90', conflicted=1)
        self.market.put('2026-09-29')
        self.clock.set('2026-09-30T00:00:00+08:00')
        latest = self.ledger.evaluate()['ledger']
        self.assertIsNone(latest['latestReports'][0]['score']['invalidation']['invalidated'])
        self.assertTrue(latest['records'][0]['invalidated'])
        self.assertTrue(latest['records'][0]['automaticInvalidated'])
        self.assertEqual(latest['records'][0]['automaticInvalidationReportId'], original_report)
        self.assertEqual(latest['latestReports'][0]['confluenceSnapshot']['activeRoots'], 0)


if __name__ == '__main__':
    unittest.main()
