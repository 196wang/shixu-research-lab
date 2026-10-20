from datetime import date
import unittest

from confluence_study import build, direction_contrast


def row(day, direction, correct):
    return {'date': day, 'year': int(day[:4]), 'directionKind': direction,
            'directionScores': {'1': {'status': 'correct' if correct else 'incorrect'}}}


class ConfluenceTests(unittest.TestCase):
    def test_same_year_same_direction_complement(self):
        selected = [row('2025-01-02', 'up', True), row('2025-01-03', 'up', False)]
        pool = selected + [row('2025-01-04', 'up', False), row('2025-01-05', 'down', True),
                           row('2024-01-04', 'up', True)]
        result = direction_contrast(selected, pool)
        self.assertEqual(result['matchedN'], 2)
        self.assertEqual(result['complementN'], 1)
        self.assertEqual(result['matchedRatePct'], 50)
        self.assertEqual(result['baselineRatePct'], 0)
        self.assertFalse(result['comparisonReady'])

    def test_frozen_inputs_make_nakshatra_density_visible(self):
        result = build()
        groups = {r['id']: r for r in result['sets']}
        self.assertEqual(len(groups), 8)
        self.assertEqual(result['eligibleBaziDays'], 987)
        self.assertEqual(groups['bazi_only']['triggerDays'] + groups['bazi_astro_only']['triggerDays'] +
                         groups['bazi_gann_only']['triggerDays'] + groups['three_families']['triggerDays'], 987)
        self.assertTrue(groups['nakshatra']['densityWarning'])
        self.assertEqual(groups['nakshatra']['coveragePct'], 100)
        self.assertFalse(groups['nakshatra']['directionContrast']['comparisonReady'])
        self.assertLess(groups['gann']['coveragePct'], 100)


if __name__ == '__main__':
    unittest.main()
