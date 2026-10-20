"""Check that the comparison panel preserves sealed retrospective totals."""
import unittest

from coinbase_rule_compare import read_comparison
from coinbase_study_store import read


class RuleComparisonTest(unittest.TestCase):
    def test_years_add_up_to_sealed_report(self):
        comparison = read_comparison()
        report = read('report')
        self.assertTrue(comparison['retrospective'])
        self.assertFalse(comparison['registeredPrediction'])
        for symbol in comparison['symbols']:
            fixed = next(item for item in report['symbols'] if item['symbol'] == symbol['symbol'])
            years = symbol['years']
            self.assertEqual([year['year'] for year in years], [2023, 2024, 2025, 2026])
            for block in fixed['blocks']:
                selected = [year for year in years if year['year'] in ((2023, 2024, 2025) if block['id'] == '2023-2025' else (2026,))]
                self.assertEqual(sum(year['eligibleDays'] for year in selected), block['direction']['eligibleDays'])
                self.assertEqual(sum(year['timing']['commonEligibleDays'] for year in selected), block['timing']['commonEligibleDays'])
                for metric in block['direction']['conditions'][0]['rules']:
                    parts = [next(rule for rule in year['directionRules'] if rule['id'] == metric['id']) for year in selected]
                    self.assertEqual(sum(rule['n'] for rule in parts), metric['n'])
                    self.assertEqual(sum(rule['correct'] for rule in parts), metric['correct'])
                for name in ('direction_conflict', 'bazi_and_timing', 'unknown_time_inputs'):
                    fixed_group = next(group for group in block['confluence'] if group['id'] == name)
                    self.assertEqual(sum(next(group['n'] for group in year['confluence'] if group['id'] == name)
                                         for year in selected), fixed_group['n'])


if __name__ == '__main__':
    unittest.main()
