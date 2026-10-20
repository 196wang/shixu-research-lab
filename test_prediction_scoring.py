"""Synthetic deterministic cases; no database, network or market conclusions."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest

import prediction_scoring as p

TARGET = date(2026, 10, 10)
DAY = timedelta(days=1)


def opened(day):
    return datetime(day.year, day.month, day.day, tzinfo=p.LOCAL)


def make_row(day, base_symbol='BTCUSDT', **changes):
    at = opened(day)
    key = int(at.timestamp() * 1000)
    row = {'symbol': base_symbol, 'open_ms': key, 'close_ms': key + 86400000 - 1,
           'open': '100', 'high': '110', 'low': '90', 'close': '100', 'volume': '1',
           'is_closed': 1, 'conflicted': 0, 'request_id': 'fixture-' + day.isoformat(),
           'body_sha256': 'a' * 64, 'updated': (at + DAY).isoformat()}
    row.update(changes)
    return row


def snapshot(symbol='BTCUSDT'):
    rows = [make_row(TARGET + i * DAY, symbol) for i in range(-12, 35)]
    return ({'symbol': symbol, 'generation': 4, 'last_success': '2026-11-15T00:00:00+00:00'},
            {row['open_ms']: row for row in rows}, {})


def put(snap, offset, **changes):
    row = make_row(TARGET + offset * DAY, snap[0]['symbol'], **changes)
    snap[1][row['open_ms']] = row
    return row['open_ms']


def record(kind='high', **changes):
    result = {'id': 'fixture-v1', 'symbol': 'BTCUSDT', 'kind': kind,
              'targetDate': TARGET.isoformat(), 'horizonDays': 3,
              'createdAtUtc': (opened(TARGET) - 10 * DAY + timedelta(hours=12)).isoformat(),
              'invalidation': None, 'prospectiveEligible': True, 'lateRevision': False}
    result.update(changes)
    return result


def score(rec=None, snap=None, asof=None):
    return p.score_prediction(rec or record(), snap or snapshot(),
                              asof or opened(TARGET) + 35 * DAY)


class TimingTests(unittest.TestCase):
    def test_exact_and_signed_three_day_edges(self):
        for kind, field, value in (('high', 'high', '130'), ('low', 'low', '70')):
            for offset in (-3, 0, 3):
                snap = snapshot()
                put(snap, offset, **{field: value})
                result = score(record(kind), snap)
                with self.subTest(kind=kind, offset=offset):
                    self.assertEqual(result['status'], 'exact' if offset == 0 else 'within_tolerance')
                    self.assertEqual(result['timeResult']['signedErrorDays'], offset)
                    self.assertEqual(result['timeResult']['absoluteErrorDays'], abs(offset))
                    self.assertFalse(result['directionAccuracyEligible'])
                    self.assertIsNone(result['directionResult'])

    def test_four_days_is_miss_not_tolerance(self):
        for offset in (-4, 4):
            snap = snapshot()
            put(snap, offset, high='130')
            result = score(record('high'), snap)
            self.assertEqual(result['status'], 'miss')
            self.assertIsNone(result['timeResult']['signedErrorDays'])

    def test_full_eleven_day_confirmation_delay(self):
        snap = snapshot()
        put(snap, 3, high='130')
        ready = opened(TARGET) + 6 * DAY
        prior = score(record('high'), snap, ready - timedelta(microseconds=1))
        after = score(record('high'), snap, ready)
        self.assertEqual(prior['status'], 'pending')
        self.assertIsNone(prior['timeResult'])
        self.assertEqual(after['status'], 'within_tolerance')
        self.assertEqual(after['timeResult']['candidates'][0]['knownAtUtc'], ready.astimezone(timezone.utc).isoformat())
        self.assertEqual(after['window']['requiredDays'], 11)
        self.assertEqual(after['window']['startDate'], '2026-10-05')
        self.assertEqual(after['window']['endDate'], '2026-10-15')

    def test_plateau_has_no_pivot(self):
        snap = snapshot()
        put(snap, 0, high='130')
        put(snap, 1, high='130')
        result = score(record('high'), snap)
        self.assertEqual(result['status'], 'miss')
        self.assertEqual(result['timeResult']['candidates'], [])

    def test_dual_high_low_is_explicit_ambiguous(self):
        snap = snapshot()
        put(snap, 0, high='130', low='70')
        for kind in ('high', 'low', 'time'):
            result = score(record(kind), snap)
            self.assertEqual(result['status'], 'miss')
            self.assertEqual(result['timeResult']['ambiguousDates'], [TARGET.isoformat()])

    def test_multiple_pivots_choose_closest_then_earlier(self):
        snap = snapshot()
        put(snap, -2, high='130')
        put(snap, 2, high='140')
        result = score(record('high'), snap)
        self.assertEqual(result['timeResult']['signedErrorDays'], -2)
        self.assertEqual([c['date'] for c in result['timeResult']['candidates']], ['2026-10-08', '2026-10-12'])
        snap = snapshot()
        put(snap, -3, high='130')
        put(snap, 1, high='140')
        self.assertEqual(score(record('high'), snap)['timeResult']['signedErrorDays'], 1)

    def test_time_matches_either_kind_without_direction(self):
        snap = snapshot()
        put(snap, -1, high='130')
        put(snap, 1, low='70')
        result = score(record('time'), snap)
        self.assertEqual(result['timeResult']['matchedKind'], 'high')
        self.assertEqual(result['timeResult']['signedErrorDays'], -1)
        self.assertFalse(result['directionAccuracyEligible'])
        self.assertIsNone(result['directionResult'])
        self.assertEqual(score(record('low'), snap)['timeResult']['signedErrorDays'], 1)

    def test_any_required_day_missing_prevents_partial_pivot_credit(self):
        snap = snapshot()
        put(snap, 0, high='130')
        del snap[1][int(opened(TARGET - 5 * DAY).timestamp() * 1000)]
        result = score(record('high'), snap)
        self.assertEqual(result['status'], 'missing')
        self.assertIsNone(result['timeResult'])
        self.assertEqual(result['dataQuality']['missingDays'][0]['date'], '2026-10-05')


class DirectionTests(unittest.TestCase):
    def test_no_shift_to_plus_minus_three_day_rally(self):
        snap = snapshot()
        put(snap, 0, close='95')
        put(snap, 1, open='95', high='200', close='190')
        put(snap, 3, high='300', close='280')
        result = score(record('up', horizonDays=1), snap)
        self.assertEqual(result['status'], 'incorrect')
        self.assertEqual(result['directionResult']['actual'], 'down')
        self.assertEqual(Decimal(result['directionResult']['returnPercent']), Decimal('-5'))
        self.assertEqual(result['window']['requiredDays'], 1)
        self.assertIsNone(result['timeResult'])

    def test_all_horizons_have_exact_ready_boundary(self):
        for horizon in (1, 3, 7, 30):
            snap = snapshot()
            put(snap, horizon - 1, close='105')
            rec = record('up', horizonDays=horizon)
            ready = opened(TARGET) + horizon * DAY
            self.assertEqual(score(rec, snap, ready - timedelta(microseconds=1))['status'], 'pending')
            result = score(rec, snap, ready)
            self.assertEqual(result['status'], 'correct')
            self.assertEqual(result['window']['requiredDays'], horizon)

    def test_decimal_direction_and_tie(self):
        snap = snapshot()
        put(snap, 0, open='100.0000000000000000000001', close='100.0000000000000000000002')
        result = score(record('up', horizonDays=1), snap)
        self.assertEqual(result['status'], 'correct')
        self.assertGreater(Decimal(result['directionResult']['returnFraction']), 0)
        result = score(record('down', horizonDays=1), snap)
        self.assertEqual(result['status'], 'incorrect')
        result = score(record('down', horizonDays=1), snapshot())
        self.assertEqual(result['status'], 'tie')
        self.assertIsNone(result['directionResult']['correct'])
        self.assertTrue(result['directionAccuracyEligible'])

    def test_correct_down_and_intermediate_gap(self):
        snap = snapshot()
        put(snap, 2, close='95')
        rec = record('down', horizonDays=3)
        self.assertEqual(score(rec, snap)['status'], 'correct')
        gap_key = int(opened(TARGET + DAY).timestamp() * 1000)
        snap[2][gap_key] = ['source_conflict']
        result = score(rec, snap)
        self.assertEqual(result['status'], 'missing')
        self.assertFalse(result['directionAccuracyEligible'])


class DataAndEligibilityTests(unittest.TestCase):
    def test_bad_prices_source_and_closed_flags(self):
        cases = [{'open': '-1'}, {'close': 'NaN'}, {'high': 'Infinity'}, {'low': '0'},
                 {'high': '95', 'close': '100'}, {'open': True}, {'is_closed': 0},
                 {'symbol': 'ETHUSDT'}, {'conflicted': 1}, {'body_sha256': 'not-a-hash'},
                 {'updated': 'without-timezone'}]
        for changes in cases:
            snap = snapshot()
            put(snap, 0, **changes)
            with self.subTest(changes=changes):
                result = score(record('up', horizonDays=1), snap)
                self.assertEqual(result['status'], 'missing')
                self.assertIsNone(result['directionResult'])

    def test_late_observed_data_blocked_asof(self):
        snap = snapshot()
        ready = opened(TARGET) + DAY
        put(snap, 0, close='105', updated=(ready + timedelta(seconds=1)).isoformat())
        result = score(record('up', horizonDays=1), snap, ready)
        self.assertEqual(result['status'], 'missing')
        self.assertIn('source_observed_after_generation', result['dataQuality']['missingDays'][0]['reasons'])
        self.assertEqual(score(record('up', horizonDays=1), snap, ready + timedelta(seconds=1))['status'], 'correct')

    def test_pathological_decimal_size_and_exponent_are_bounded(self):
        for value in ('1e-999999999', '1e999999999', '9' * 201, '0.' + '0' * 250 + '1'):
            snap = snapshot()
            put(snap, 0, open=value)
            with self.subTest(value=value[:30]):
                self.assertEqual(score(record('up', horizonDays=1), snap)['status'], 'missing')
        for value in ('1e-999999999', '9' * 201, '0.' + '0' * 250 + '1'):
            with self.assertRaises(ValueError):
                score(record('up', invalidation={'type': 'close_below', 'price': value}))

    def test_nonfinite_numeric_evidence_can_be_saved_as_missing(self):
        for value in (float('nan'), float('inf')):
            snap = snapshot()
            put(snap, 0, open=value)
            result = score(record('up', horizonDays=1), snap)
            self.assertEqual(result['status'], 'missing')
            json.dumps(result, allow_nan=False)
            self.assertIsInstance(result['evidence']['selectedDays'][0]['row']['open'], str)

    def test_future_complete_rows_cannot_score_early(self):
        snap = snapshot()
        put(snap, 0, close='105', updated=(opened(TARGET) - DAY).isoformat())
        result = score(record('up', horizonDays=1), snap, opened(TARGET) + timedelta(hours=12))
        self.assertEqual(result['status'], 'pending')
        self.assertIsNone(result['directionResult'])
        self.assertIn('not_yet_closed', result['dataQuality']['missingDays'][0]['reasons'])

    def test_duplicate_day_and_malformed_gap_metadata(self):
        snap = snapshot()
        source_key = int(opened(TARGET).timestamp() * 1000)
        next_key = source_key + 86400000
        snap[1][next_key] = deepcopy(snap[1][source_key])
        result = score(record('up', horizonDays=3), snap)
        self.assertEqual(result['status'], 'missing')
        self.assertIn('wrong_day_boundary', result['dataQuality']['missingDays'][0]['reasons'])
        snap = snapshot()
        snap[2][source_key] = 'bad'
        self.assertEqual(score(record('up', horizonDays=1), snap)['status'], 'missing')

    def test_strict_eligibility_deadline_and_late_revision_still_scored(self):
        snap = snapshot()
        put(snap, 0, high='130', close='105')
        for kind, deadline in (('high', opened(TARGET) - 3 * DAY), ('up', opened(TARGET))):
            early = record(kind, horizonDays=1, createdAtUtc=(deadline - timedelta(microseconds=1)).isoformat())
            late = record(kind, horizonDays=1, createdAtUtc=deadline.isoformat())
            self.assertTrue(score(early, snap)['prospectiveEligible'])
            result = score(late, snap)
            self.assertFalse(result['prospectiveEligible'])
            self.assertEqual(result['status'], 'exact' if kind == 'high' else 'correct')
        for changes in ({'lateRevision': True}, {'prospectiveEligible': False}):
            result = score(record('up', horizonDays=1, **changes), snap)
            self.assertEqual(result['status'], 'correct')
            self.assertFalse(result['directionAccuracyEligible'])

    def test_invalid_record_or_snapshot_rejected(self):
        invalid = [{'kind': 'long'}, {'symbol': 'XRPUSDT'}, {'targetDate': '2026-2-3'},
                   {'targetDate': '2026-02-30'}, {'horizonDays': True}, {'horizonDays': 2},
                   {'createdAtUtc': '2026-10-01T00:00:00'}, {'createdAtUtc': '2026-10-01T00:00:00-00:00'},
                   {'prospectiveEligible': 1}, {'invalidation': {'type': 'close_below', 'price': '0'}},
                   {'invalidation': {'type': 'close_above', 'price': 'NaN'}},
                   {'invalidation': {'type': 'close_above', 'price': 100}}]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                score(record(**changes))
        with self.assertRaises(ValueError):
            score(record(), snapshot('ETHUSDT'))
        with self.assertRaises(ValueError):
            score(asof=datetime(2026, 11, 1))
        with self.assertRaises(ValueError):
            score(asof=opened(TARGET) - 20 * DAY)
        with self.assertRaises(ValueError):
            p.score_prediction(record(), ({}, [], {}), opened(TARGET) + 10 * DAY)

    def test_snapshot_and_record_not_mutated_and_hash_reproducible(self):
        snap, rec = snapshot('ETHUSDT'), record('up', symbol='ETHUSDT', horizonDays=1)
        before = deepcopy((snap, rec))
        result = score(rec, snap)
        self.assertEqual((snap, rec), before)
        evidence = result['evidence']
        self.assertEqual(evidence['snapshotSha256'], p.snapshot_hash({
            'state': evidence['state'], 'asOfUtc': result['asOfUtc'], 'selectedDays': evidence['selectedDays']}))
        self.assertEqual(len(evidence['selectedDays']), 1)
        self.assertEqual(evidence['selectedDays'][0]['roles'], ['score'])
        self.assertEqual(result['definitionSha256'], p.snapshot_hash(result['definition']))


class InvalidationTests(unittest.TestCase):
    def test_first_full_day_strict_threshold_and_original_score_retained(self):
        snap = snapshot()
        created = opened(TARGET - 2 * DAY) + timedelta(hours=12)
        put(snap, -2, close='91')  # Partial day after creation must not be scanned.
        put(snap, -1, close='95')  # Equality does not invalidate.
        put(snap, 0, close='94')
        put(snap, 1, close='93')
        put(snap, 2, close='105')
        rec = record('up', createdAtUtc=created.isoformat(),
                     invalidation={'type': 'close_below', 'price': '95'})
        result = score(rec, snap)
        self.assertEqual(result['status'], 'correct')
        inv = result['invalidation']
        self.assertTrue(inv['invalidated'])
        self.assertTrue(inv['firstTriggerCertain'])
        self.assertEqual(inv['scanStartAt'], opened(TARGET - DAY).isoformat())
        self.assertEqual(inv['firstObservedTriggerDate'], TARGET.isoformat())
        self.assertEqual(inv['firstObservedTriggerAtUtc'], (opened(TARGET) + DAY).astimezone(timezone.utc).isoformat())

    def test_midnight_creation_includes_same_full_day_and_above_strict(self):
        snap = snapshot()
        created = opened(TARGET - DAY)
        put(snap, -1, close='106')
        result = score(record('up', createdAtUtc=created.isoformat(), horizonDays=1,
                       invalidation={'type': 'close_above', 'price': '105'}), snap)
        self.assertEqual(result['invalidation']['firstObservedTriggerDate'], (TARGET - DAY).isoformat())
        put(snap, -1, close='105')
        result = score(record('up', createdAtUtc=created.isoformat(), horizonDays=1,
                       invalidation={'type': 'close_above', 'price': '105'}), snap)
        self.assertEqual(result['invalidation']['status'], 'clear')

    def test_stop_at_score_end_ignores_later_invalidation(self):
        snap = snapshot()
        put(snap, 1, close='91')
        result = score(record('up', horizonDays=1,
                       invalidation={'type': 'close_below', 'price': '95'}), snap)
        self.assertEqual(result['invalidation']['status'], 'clear')
        self.assertFalse(result['invalidation']['invalidated'])
        self.assertEqual(result['invalidation']['scanEndExclusiveAt'], (opened(TARGET) + DAY).isoformat())

    def test_unfinished_daily_close_cannot_invalidate(self):
        snap = snapshot()
        put(snap, 0, close='91', updated=(opened(TARGET) - DAY).isoformat())
        result = score(record('up', horizonDays=1,
                       invalidation={'type': 'close_below', 'price': '95'}), snap,
                       opened(TARGET) + timedelta(hours=12))
        self.assertEqual(result['status'], 'pending')
        self.assertFalse(result['invalidation']['invalidated'])
        self.assertIsNone(result['invalidation']['firstObservedTriggerDate'])

    def test_gap_before_trigger_marks_first_uncertain_without_erasing_trigger(self):
        snap = snapshot()
        gap = int(opened(TARGET - DAY).timestamp() * 1000)
        snap[2][gap] = ['source_conflict']
        put(snap, 0, close='94')
        put(snap, 2, close='105')
        result = score(record('up', invalidation={'type': 'close_below', 'price': '95'}), snap)
        self.assertEqual(result['status'], 'correct')
        self.assertTrue(result['invalidation']['invalidated'])
        self.assertFalse(result['invalidation']['firstTriggerCertain'])
        self.assertFalse(result['invalidation']['coverageComplete'])
        self.assertEqual(len(result['invalidation']['missingDays']), 1)

    def test_no_trigger_with_gap_is_unknown_and_late_updated_trigger_blocked(self):
        snap = snapshot()
        asof = opened(TARGET) + 3 * DAY
        put(snap, -1, close='91', updated=(asof + timedelta(seconds=1)).isoformat())
        result = score(record('up', invalidation={'type': 'close_below', 'price': '95'}), snap, asof)
        self.assertEqual(result['status'], 'tie')
        self.assertEqual(result['invalidation']['status'], 'missing')
        self.assertIsNone(result['invalidation']['invalidated'])

    def test_no_complete_scan_day_is_pending(self):
        snap = snapshot()
        created = opened(TARGET) - timedelta(hours=12)
        result = score(record('up', createdAtUtc=created.isoformat(),
                       invalidation={'type': 'close_below', 'price': '95'}), snap,
                       opened(TARGET) + timedelta(hours=12))
        self.assertEqual(result['invalidation']['status'], 'pending')
        self.assertIsNone(result['invalidation']['invalidated'])

    def test_late_revision_after_window_end_has_no_applicable_invalidation_days(self):
        snap = snapshot()
        put(snap, 0, close='105')
        created = opened(TARGET) + 4 * DAY
        result = score(record('up', horizonDays=1, createdAtUtc=created.isoformat(), lateRevision=True,
                       invalidation={'type': 'close_below', 'price': '95'}), snap)
        self.assertEqual(result['status'], 'correct')
        self.assertFalse(result['prospectiveEligible'])
        self.assertEqual(result['invalidation']['status'], 'not_applicable')
        self.assertIsNone(result['invalidation']['invalidated'])
        self.assertEqual(result['invalidation']['checkedDays'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
