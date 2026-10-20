"""Synthetic contract tests; no real market rows or scoring is loaded here."""
from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import structure_scorer as scorer


def grid_fixture(n=400, missing=()):
    start = date(2024, 1, 1)
    return [{'date': (start + timedelta(days=i)).isoformat(), 'group': i % 2,
             'mask': int(i not in missing),
             'absoluteReturn': None if i in missing else (i % 17 + 1) / 1000,
             'errors': ['synthetic_missing'] if i in missing else []}
            for i in range(n)]


def naive_bootstrap(grid, seed=20260927):
    """Independent element-by-element paired MBB; no prefix sums or scorer calls."""
    rng = random.Random(seed)
    count = len(grid)
    block = 30
    draws = math.ceil(count / block)
    samples = []
    observed_counts = []
    group_counts = [[], []]
    starts_seen = set()
    for _ in range(10000):
        sampled = []
        for _ in range(draws):
            start = rng.randrange(count - block + 1)
            starts_seen.add(start)
            sampled.extend(grid[start:start + block])
        sampled = sampled[:count]
        groups = [[r['absoluteReturn'] for r in sampled if r['mask'] and r['group'] == g]
                  for g in (0, 1)]
        if groups[0] and groups[1]:
            samples.append(sum(groups[1]) / len(groups[1]) - sum(groups[0]) / len(groups[0]))
            observed_counts.append(len(groups[0]) + len(groups[1]))
            for g in (0, 1):
                group_counts[g].append(len(groups[g]))
    samples.sort()
    ci = []
    if len(samples) >= 9900:
        for probability in (.025, .975):
            position = (len(samples) - 1) * probability
            lower, upper = math.floor(position), math.ceil(position)
            fraction = position - lower
            ci.append(samples[lower] + (samples[upper] - samples[lower]) * fraction)
    return {'valid': len(samples), 'ci': ci or None,
            'counts': [min(observed_counts), max(observed_counts)] if observed_counts else None,
            'groupRanges': {str(g): [min(values), max(values)] if values else None
                            for g, values in enumerate(group_counts)},
            'starts': starts_seen, 'draws': draws * 10000}


def price_document(start='2024-01-01', n=12):
    rows = []
    for i in range(n):
        day = (date.fromisoformat(start) + timedelta(days=i)).isoformat()
        rows.append({'date': day, 'dayGanzhi': scorer.independent_day_label(day)[1],
                     'hourCount': 24, 'sourceErrors': [], 'open': '100',
                     'high': '120', 'low': '70', 'close': '80' if i % 2 else '110'})
    return {'dailyRows': rows}


class StructureScorerTests(unittest.TestCase):
    def test_abs_loss_and_gain_outcome_not_high_low_amplitude(self):
        document = price_document(n=2)
        grid = scorer.build_grid(document, '2024-01-01', '2024-01-02')
        self.assertAlmostEqual(grid[0]['absoluteReturn'], .1)
        self.assertAlmostEqual(grid[1]['absoluteReturn'], .2)
        self.assertNotIn('return', grid[1])

    def test_original_duplicate_zi_counts_as_one_day(self):
        document = price_document()
        grid = scorer.build_grid(document, '2024-01-01', '2024-01-12')
        self.assertEqual(len(grid), 12)
        self.assertEqual(sum(r['group'] for r in grid), 3)
        noon_day = next(r for r in grid if r['dayGanzhi'][1] == '午')
        self.assertEqual(noon_day['group'], 1)
        self.assertEqual(len(noon_day['clashEdges']), 2)

    def test_group_is_branch_clash_not_old_metal_water_stem(self):
        document = price_document(n=60)
        grid = scorer.build_grid(document, '2024-01-01', '2024-02-29')
        self.assertEqual(sum(r['group'] for r in grid), 15)
        for row in grid:
            self.assertEqual(row['group'], int(row['dayGanzhi'][1] in '寅卯午'))
            self.assertTrue(row['independentDayAndGroupVerified'])
        self.assertTrue(any(r['group'] != int(r['dayGanzhi'][0] in '庚辛壬癸') for r in grid))

    def test_invalid_hour_keeps_calendar_and_group_but_null_outcome(self):
        document = price_document(n=3)
        document['dailyRows'][1]['hourCount'] = 23
        grid = scorer.build_grid(document, '2024-01-01', '2024-01-03')
        self.assertEqual(len(grid), 3)
        self.assertEqual(grid[1]['mask'], 0)
        self.assertIsNone(grid[1]['absoluteReturn'])
        self.assertEqual(grid[1]['date'], '2024-01-02')
        self.assertIn(grid[1]['group'], (0, 1))

    def test_source_error_masks_even_valid_prices(self):
        document = price_document(n=1)
        document['dailyRows'][0]['sourceErrors'] = ['synthetic_bad_hour']
        row = scorer.build_grid(document, '2024-01-01', '2024-01-01')[0]
        self.assertEqual(row['mask'], 0)
        self.assertIsNone(row['absoluteReturn'])

    def test_duplicate_source_date_rejected_not_weighted(self):
        document = price_document(n=1)
        document['dailyRows'].append(deepcopy(document['dailyRows'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate input date'):
            scorer.build_grid(document, '2024-01-01', '2024-01-01')

    def test_absent_calendar_slot_rejected_not_compressed(self):
        document = price_document(n=3)
        document['dailyRows'].pop(1)
        with self.assertRaisesRegex(ValueError, 'calendar grid row absent'):
            scorer.build_grid(document, '2024-01-01', '2024-01-03')

    def test_calendar_label_mismatch_rejected(self):
        document = price_document(n=1)
        actual = document['dailyRows'][0]['dayGanzhi']
        document['dailyRows'][0]['dayGanzhi'] = '乙丑' if actual != '乙丑' else '甲子'
        with self.assertRaisesRegex(ValueError, 'input day label'):
            scorer.build_grid(document, '2024-01-01', '2024-01-01')

    def test_negative_imputed_and_nonfinite_outcomes_rejected(self):
        for value, mask in ((-.01, 1), (float('nan'), 1), (float('inf'), 1), (True, 1), (.1, 0)):
            grid = grid_fixture(31)
            grid[0].update(absoluteReturn=value, mask=mask)
            with self.subTest(value=value, mask=mask), self.assertRaises(ValueError):
                scorer.validate_grid(grid)

    def test_duplicate_or_nonconsecutive_grid_rejected(self):
        for substitute in ('2024-01-01', '2024-01-03'):
            grid = grid_fixture(31)
            grid[1]['date'] = substitute
            with self.subTest(substitute=substitute), self.assertRaises(ValueError):
                scorer.validate_grid(grid)

    def test_naive_mbb_same_rng_start_boundaries_last_block_and_masks(self):
        grid = grid_fixture(47, missing={0, 16, 30, 46})
        actual = scorer.bootstrap_absolute(grid)
        expected = naive_bootstrap(grid)
        self.assertEqual(actual['calendarSlotsPerReplicate'], 47)
        self.assertEqual(actual['finalBlockWidth'], 17)
        self.assertEqual(actual['replicatesValid'], expected['valid'])
        self.assertEqual(actual['randomDrawCount'], expected['draws'])
        self.assertEqual(actual['validObservedDaysRange'], expected['counts'])
        self.assertEqual(actual['sampledGroupCountRanges'], expected['groupRanges'])
        self.assertEqual(expected['starts'], set(range(18)))
        for observed, reference in zip(actual['ci95'], expected['ci']):
            self.assertAlmostEqual(observed, reference, places=13)

    def test_rng_is_reset_each_invocation(self):
        grid = grid_fixture(47, missing={3, 12, 31})
        self.assertEqual(scorer.bootstrap_absolute(grid), scorer.bootstrap_absolute(grid))

    def test_empty_sampled_group_invalid_no_retry_or_imputed_interval(self):
        grid = grid_fixture(61)
        for row in grid:
            row['group'] = 0
        grid[0]['group'] = 1
        actual = scorer.bootstrap_absolute(grid)
        expected = naive_bootstrap(grid)
        self.assertEqual(actual['replicatesValid'], expected['valid'])
        self.assertEqual(actual['replicatesInvalid'], 10000 - expected['valid'])
        self.assertLess(actual['replicatesValid'], 9900)
        self.assertFalse(actual['available'])
        self.assertIsNone(actual['ci95Bp'])
        self.assertEqual(actual['randomDrawCount'], 30000)

    def test_interval_thresholds_strict_beyond_closed_band(self):
        examples = [([10.000001, 11], 'supported_positive_difference'),
                    ([-11, -10.000001], 'supported_negative_difference'),
                    ([-10, 10], 'no_material_difference_at_chosen_margin'),
                    ([10, 10], 'no_material_difference_at_chosen_margin'),
                    ([10, 11], 'uncertain'), ([-11, -10], 'uncertain'),
                    ([-20, 20], 'uncertain'), ([0, 9], 'no_material_difference_at_chosen_margin')]
        for interval, expected in examples:
            with self.subTest(interval=interval):
                self.assertEqual(scorer.decision_from_interval(interval), expected)

    def test_bad_interval_rejected(self):
        for interval in ([2, 1], [0], [0, float('nan')], [False, 1]):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                scorer.decision_from_interval(interval)

    def test_exact_95_percent_and_group_100_gate_passes(self):
        grid = grid_fixture(400, missing=set(range(20)))
        result = scorer.summarize_period(grid, grid[0]['date'], grid[-1]['date'])
        self.assertEqual(result['coverage'], .95)
        self.assertTrue(result['dataGatePassed'])
        self.assertEqual(result['missingDays'], 20)
        self.assertTrue(result['bootstrap']['available'])
        grid = grid_fixture(200)
        result = scorer.summarize_period(grid, grid[0]['date'], grid[-1]['date'])
        self.assertEqual([g['n'] for g in result['groups'].values()], [100, 100])
        self.assertTrue(result['dataGatePassed'])

    def test_coverage_below_gate_skips_bootstrap(self):
        grid = grid_fixture(400, missing=set(range(21)))
        with patch.object(scorer, 'bootstrap_absolute', side_effect=AssertionError('must not run')):
            result = scorer.summarize_period(grid, grid[0]['date'], grid[-1]['date'])
        self.assertEqual(result['decision'], 'insufficient_data')
        self.assertIn('coverage_below_95_percent', result['gateErrors'])

    def test_group_size_99_skips_bootstrap(self):
        grid = grid_fixture(400)
        for i, row in enumerate(grid):
            row['group'] = int(i < 99)
        with patch.object(scorer, 'bootstrap_absolute', side_effect=AssertionError('must not run')):
            result = scorer.summarize_period(grid, grid[0]['date'], grid[-1]['date'])
        self.assertEqual(result['decision'], 'insufficient_data')
        self.assertIn('fewer_than_100_observed_days_in_a_group', result['gateErrors'])

    def test_summary_has_absolute_means_and_no_up_ratio(self):
        grid = grid_fixture(200)
        for row in grid:
            row['absoluteReturn'] = .02 if row['group'] else .01
        result = scorer.summarize_period(grid, grid[0]['date'], grid[-1]['date'])
        self.assertAlmostEqual(result['deltaBp'], 100)
        self.assertEqual(result['groups']['0']['meanAbsoluteReturnBp'], 100)
        self.assertEqual(result['groups']['1']['meanAbsoluteReturnBp'], 200)
        self.assertNotIn('upRatio', json.dumps(result))
        self.assertFalse(result['marketValidated'])

    def test_missing_masks_change_counts_without_shortening_calendar(self):
        grid = grid_fixture(400, missing={2, 4, 6})
        result = scorer.summarize_period(grid, grid[0]['date'], grid[-1]['date'])
        self.assertEqual(result['expectedDays'], 400)
        self.assertEqual(result['observedDays'], 397)
        self.assertEqual(result['groups']['0']['n'], 197)
        self.assertEqual(result['groups']['1']['n'], 200)
        self.assertEqual(result['bootstrap']['calendarSlotsPerReplicate'], 400)

    def test_across_period_same_side_only_and_no_validation_claim(self):
        for decisions, expected in ((['supported_positive_difference'] * 2, 'consistently_positive_difference'),
                                    (['supported_negative_difference'] * 2, 'consistently_negative_difference'),
                                    (['supported_positive_difference', 'supported_negative_difference'], 'no_consistent_directional_support'),
                                    (['uncertain'] * 2, 'no_consistent_directional_support'),
                                    (['no_material_difference_at_chosen_margin'] * 2, 'both_within_chosen_margin'),
                                    (['insufficient_data', 'uncertain'], 'inconclusive_due_to_data_or_uncertainty')):
            with self.subTest(decisions=decisions):
                self.assertEqual(scorer.overall_decision([{'decision': d} for d in decisions])[0], expected)

    def test_main_refuses_existing_output_before_validation_or_scoring(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / 'structure_results.json').write_text('preserve', encoding='utf-8')
            with patch.object(scorer, 'HERE', base), patch.object(scorer, 'verify_execution_inputs', side_effect=AssertionError('must not run')):
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    scorer.main()
            self.assertEqual((base / 'structure_results.json').read_text(), 'preserve')

    def test_prior_execution_receipt_alone_prevents_rerun(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / 'execution_started.json').write_text('{}')
            with patch.object(scorer, 'HERE', base), self.assertRaisesRegex(ValueError, 'already exists'):
                scorer.main()

    def test_manifest_tampering_and_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / 'input.txt'
            target.write_text('original')
            registry = base / 'register.json'
            registry.write_text(json.dumps({'inputHashes': {'input.txt': scorer.digest(target)}}))
            scorer._verify_hash_manifest(base, registry.name)
            target.write_text('changed')
            with self.assertRaisesRegex(ValueError, 'registered input changed'):
                scorer._verify_hash_manifest(base, registry.name)
            registry.write_text(json.dumps({'inputHashes': {'../outside': '0' * 64}}))
            with self.assertRaisesRegex(ValueError, 'outside package'):
                scorer._verify_hash_manifest(base, registry.name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
