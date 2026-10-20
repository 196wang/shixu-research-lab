from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from prediction_candidates import preview_candidates


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.gann = patch('gann_timing.calculate_gann', return_value={'projections': []})
        self.astro = patch('astro_events.calculate_astro_events', return_value={'events': []})
        self.gann.start(); self.astro.start()
        self.addCleanup(self.gann.stop); self.addCleanup(self.astro.stop)
        self.clock = lambda: datetime(2026, 9, 27, 5, tzinfo=timezone.utc)

    def call(self, symbol='BTCUSDT', start='2026-09-28', end='2026-10-07'):
        return preview_candidates(None, symbol, start, end, clock=self.clock)

    def test_bazi_all_ten_stems_direction_hypotheses_and_no_eth_transfer(self):
        values = self.call()['candidates']
        self.assertEqual(len(values), 6)
        self.assertEqual(sum(v['draft']['kind'] == 'up' for v in values), 4)
        self.assertTrue(all(v['canRegister'] and v['draft']['horizonDays'] == 1 for v in values))
        self.assertEqual(self.call('ETHUSDT')['candidates'], [])
        self.assertTrue(self.call('ETHUSDT')['warnings'])

    def test_astro_gann_neutral_and_window_deadline(self):
        event = {'kind': 'moon_phase', 'label': '满月', 'atLocal': '2026-09-29T11:00:00+08:00'}
        with (patch('astro_events.calculate_astro_events', return_value={'events': [event]}),
              patch('gann_timing.calculate_gann', return_value={'projections': [
                  {'targetDate': '2026-10-02', 'status': 'future_candidate'},
                  {'targetDate': '2026-10-03', 'status': 'retrospective'}]})):
            values = self.call('ETHUSDT')['candidates']
        self.assertEqual(len(values), 2)
        self.assertTrue(all(v['draft']['kind'] == 'time' for v in values))
        self.assertFalse(values[0]['canRegister'])
        self.assertTrue(values[1]['canRegister'])

    def test_range_errors_and_failures_explicit(self):
        for start, end in [('2026-09-1','2026-09-02'),('2026-09-28','2026-11-01'),('2026-09-30','2026-09-28')]:
            with self.assertRaises(ValueError):
                self.call(start=start,end=end)
        with patch('astro_events.calculate_astro_events', side_effect=RuntimeError('missing engine')):
            self.assertIn('missing engine', self.call()['warnings'][-1])


if __name__ == '__main__':
    unittest.main()
