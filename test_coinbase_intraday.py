import unittest
from datetime import datetime, timedelta

from coinbase_feed import LOCAL
from coinbase_intraday import HOUR, _session


class IntradaySessionTests(unittest.TestCase):
    def test_23_and_midnight_windows_use_different_opening_hour(self):
        midnight = datetime(2026, 10, 16, tzinfo=LOCAL)
        first = midnight - HOUR
        hours = {}
        for i in range(25):
            opened = 100 + i
            hours[int((first + i * HOUR).timestamp())] = {
                'open': str(opened), 'high': str(opened + 2),
                'low': str(opened - 2), 'close': str(opened + 1),
            }
        zi = _session('23', first, hours)
        civil = _session('00', midnight, hours)
        self.assertEqual((zi['open'], zi['close']), ('100', '124'))
        self.assertEqual((civil['open'], civil['close']), ('101', '125'))
        self.assertEqual(zi['completeHours'], 24)
        self.assertEqual(civil['completeHours'], 24)
        self.assertNotEqual(zi['rangePct'], civil['rangePct'])

    def test_missing_hour_never_produces_full_day_metric(self):
        start = datetime(2026, 10, 15, 23, tzinfo=LOCAL)
        result = _session('23', start, {})
        self.assertFalse(result['complete'])
        self.assertEqual(result['completeHours'], 0)
        self.assertNotIn('rangePct', result)


if __name__ == '__main__':
    unittest.main()
