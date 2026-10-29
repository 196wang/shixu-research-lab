from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock

from coinbase_feed import local_open, ms
from coinbase_forward import CoinbaseForward, CoinbaseForwardError, seal, verify, time_event_diagnostics


class CoinbaseForwardTests(unittest.TestCase):
    @staticmethod
    def fixture(missing=None, registered='2026-10-20T00:00:00+00:00'):
        target = date(2026, 10, 25)
        candles = {}
        for i in range(-5, 6):
            day = target + timedelta(days=i)
            if day == missing:
                continue
            open_ms = ms(local_open(day))
            candles[open_ms] = {'symbol': 'BTCUSD', 'open_ms': open_ms,
                'close_ms': open_ms + 86400000 - 1, 'open': '100',
                'high': '120' if i == 0 else '111', 'low': '90',
                'close': '110' if i == 0 else '100', 'is_closed': 1,
                'conflicted': 0, 'request_id': 'fixture', 'body_sha256': 'a' * 64,
                'updated': '2026-10-31T00:00:00+00:00'}
        registration = {'registeredAtUtc': registered, 'startDate': target.isoformat(),
            'endDate': target.isoformat(), 'schedule': [{'date': target.isoformat(),
                'boundaryExcluded': False, 'directionRuleIds': ['DAY-STEM-METALWATER-UP'],
                'timeEvents': [{'ruleId': 'ASTRO-MOON-PHASE', 'eventId': 'fixture'}]}]}
        snapshot = ({'symbol': 'BTCUSD'}, candles, {})
        return registration, snapshot

    def test_direction_and_strict_pivot_are_scored_after_neighborhood_closes(self):
        study = CoinbaseForward(Path('.'), None)
        registration, snapshot = self.fixture()
        asof = datetime(2026, 11, 1, tzinfo=timezone.utc)
        result = study._score(registration, snapshot, asof)
        row = result['rows'][0]
        self.assertEqual(row['directionResults']['DAY-STEM-METALWATER-UP'], 'correct')
        self.assertEqual(row['timeStatus'], 'scored')
        self.assertTrue(row['timing']['hit'])
        self.assertEqual(row['timing']['errorDays'], 0)

    def test_missing_neighbor_never_counts_as_time_failure(self):
        study = CoinbaseForward(Path('.'), None)
        registration, snapshot = self.fixture(missing=date(2026, 10, 22))
        row = study._score(registration, snapshot, datetime(2026, 11, 1, tzinfo=timezone.utc))['rows'][0]
        self.assertEqual(row['directionStatus'], 'scored')
        self.assertEqual(row['timeStatus'], 'missing')

    def test_late_time_window_is_excluded_without_discarding_direction(self):
        study = CoinbaseForward(Path('.'), None)
        registration, snapshot = self.fixture(registered='2026-10-23T00:00:00+00:00')
        row = study._score(registration, snapshot, datetime(2026, 11, 1, tzinfo=timezone.utc))['rows'][0]
        self.assertEqual(row['directionStatus'], 'scored')
        self.assertEqual(row['timeStatus'], 'late_registration')

    def test_seal_detects_changed_record(self):
        item = seal({'studyId': 'CB-USD-FWD-001', 'n': 1})
        verify(item)
        item['n'] = 2
        with self.assertRaises(CoinbaseForwardError):
            verify(item)

    def test_time_diagnostic_requires_enough_unmarked_control_dates(self):
        rows = [{'timeStatus': 'scored', 'timing': {'hit': hit},
                 'timeEvents': [{'ruleId': 'ASTRO-MOON-PHASE'}] if event else []}
                for hit, event in [(True, True), (False, True), (False, False), (False, False)]]
        result = time_event_diagnostics(rows)
        moon = next(item for item in result if item['ruleId'] == 'ASTRO-MOON-PHASE')
        self.assertEqual((moon['eventDays'], moon['controlPoolDays']), (2, 2))
        self.assertEqual((moon['eventHitPct'], moon['equalCountControlExpectedHitPct']), (50.0, 0.0))
        dense = time_event_diagnostics([{**row, 'timeEvents': [{'ruleId': 'ASTRO-NAKSHATRA-INGRESS'}]}
                                        for row in rows])
        nakshatra = next(item for item in dense if item['ruleId'] == 'ASTRO-NAKSHATRA-INGRESS')
        self.assertEqual(nakshatra['eventCoveragePct'], 100.0)
        self.assertFalse(nakshatra['equalCountControlPossible'])
        self.assertIsNone(nakshatra['equalCountControlExpectedHitPct'])

    def test_auto_tick_waits_until_0020_and_then_refreshes_missing_day(self):
        last = date(2026, 10, 16)
        open_ms = ms(local_open(last))
        market = Mock()
        market._snapshot.return_value = ({'symbol': 'BTCUSD'}, {
            open_ms: {'is_closed': 1, 'conflicted': 0}}, {})
        study = CoinbaseForward(Path('.'), market,
            clock=lambda: datetime(2026, 10, 18, 16, 19, tzinfo=timezone.utc))
        study._registration = lambda: {'studyId': 'CB-USD-FWD-001'}
        study.protocol = lambda: {'endDate': '2026-11-17'}
        study.evaluate = Mock()
        study.auto_tick()
        market.refresh.assert_not_called()
        study.evaluate.assert_not_called()
        study.clock = lambda: datetime(2026, 10, 18, 16, 21, tzinfo=timezone.utc)
        study.auto_tick()
        market.refresh.assert_called_once_with('BTCUSD', 3)
        study.evaluate.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
