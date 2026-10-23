from datetime import date
import unittest

from jie_observation import jie_windows


class JieObservationTests(unittest.TestCase):
    def test_hanlu_lidong_and_nearby_dates_are_observations(self):
        result = jie_windows(date(2026, 10, 1), date(2026, 11, 17))
        events = {item['date']: item for item in result['events']}
        self.assertEqual(events['2026-10-08']['atLocal'], '2026-10-08T14:29:17+08:00')
        self.assertEqual(events['2026-11-07']['atLocal'], '2026-11-07T17:52:05+08:00')
        self.assertEqual((events['2026-11-07']['beforeMonth'], events['2026-11-07']['afterMonth']),
                         ('戊戌', '己亥'))
        nearby = {item['date']: item['offsetDays'] for item in result['days']
                  if item['eventDate'] == '2026-11-07'}
        self.assertEqual(nearby, {'2026-11-05': -2, '2026-11-06': -1,
                                  '2026-11-07': 0, '2026-11-08': 1,
                                  '2026-11-09': 2})


if __name__ == '__main__':
    unittest.main()
