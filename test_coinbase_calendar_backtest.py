import unittest

from coinbase_calendar_backtest import read_calendar, summarize


class CalendarBacktestTests(unittest.TestCase):
    def test_month_counts_dates_and_rule_outcomes_exclude_missing(self):
        rows = [
            {'date': '2025-10-01', 'status': 'eligible', 'actual': 'up', 'pillars': {'day': '庚子', 'month': '乙酉'},
             'open': '100', 'close': '102', 'returnPct': 2.0,
             'ruleIds': ['DAY-STEM-METALWATER-UP'], 'reasons': []},
            {'date': '2025-10-02', 'status': 'eligible', 'actual': 'down', 'pillars': {'day': '丙寅', 'month': '乙酉'},
             'open': '102', 'close': '99', 'returnPct': -2.9,
             'ruleIds': ['DAY-STEM-FIRE-DOWN', 'DAY-BRANCH-YIN-DOWN'], 'reasons': []},
            {'date': '2025-10-03', 'status': 'boundary_excluded', 'actual': None, 'pillars': None,
             'open': None, 'close': None, 'returnPct': None, 'ruleIds': ['DAY-STEM-FIRE-DOWN'],
             'reasons': ['month_boundary_inside_bar']},
            {'date': '2025-10-04', 'status': 'missing', 'actual': None, 'pillars': None,
             'open': None, 'close': None, 'returnPct': None, 'ruleIds': [], 'reasons': ['missing_hour']},
        ]
        result = summarize(rows)
        month = result['months'][0]
        self.assertEqual((month['up'], month['down'], month['eligible'], month['excluded'], month['missing']),
                         (1, 1, 2, 1, 1))
        self.assertEqual(month['upDates'], ['2025-10-01'])
        self.assertEqual(month['downDates'], ['2025-10-02'])
        self.assertEqual(month['ruleCounts']['DAY-STEM-FIRE-DOWN']['correct'], 1)
        self.assertEqual(month['ruleCounts']['DAY-STEM-FIRE-DOWN']['triggered'], 1)
        self.assertEqual(result['daily'][2]['ruleIds'], [])

    def test_fixed_coinbase_year_is_read_without_recalculation(self):
        value = read_calendar('BTCUSD', 2025)
        self.assertEqual(len(value['daily']), 365)
        self.assertEqual(sum(m['days'] for m in value['months']), 365)
        self.assertEqual(len(value['months']), 12)
        self.assertEqual(value['studyId'], 'CB-USD-HX-001')


if __name__ == '__main__':
    unittest.main()
