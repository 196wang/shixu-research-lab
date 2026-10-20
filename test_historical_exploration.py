"""Synthetic protocol, chronology, denominator and export checks; no real study run."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

import historical_exploration as h
import exploration_generators as g
from test_prediction_scoring import make_row

DAY = timedelta(days=1)
CAPTURE = datetime(2026, 9, 27, tzinfo=timezone.utc)


def index(first, last):
    return {day: {'valid': True, 'reasons': [], 'prices': {
        'open': Decimal('100'), 'high': Decimal('110'), 'low': Decimal('90'), 'close': Decimal('100')}}
        for day in g.days_between(first, last)}


def timing_row(day, error=None, candidates=None, group='none'):
    candidates = candidates or []
    result = {'status': 'miss' if error is None else 'exact' if error == 0 else 'within_tolerance',
              'timeResult': {'absoluteErrorDays': error, 'candidates': candidates,
                             'matchedPivotId': candidates[0]['pivotId'] if candidates and error is not None else None}}
    return {'date': day.isoformat(), 'year': day.year, 'timingScore': result, 'timingGroup': group,
            'timingRuleIds': [], 'ruleKnown': dict.fromkeys(h.TIMING_IDS, True)}


def direction_row(actual='up', prediction='up', group='single', status='correct'):
    return {'directionScores': {'1': {'status': status, 'directionResult': {'actual': actual}}},
            'directionKind': prediction, 'directionGroup': group, 'directionKnown': True,
            'directionRuleIds': ['BZ-DAY-METALWATER-UP'] if prediction == 'up' else ['BZ-DAY-FIRE-DOWN'] if prediction == 'down' else []}


class ProtocolTests(unittest.TestCase):
    def test_fixed_protocol_and_reject_parameter_search(self):
        self.assertEqual(h.load_protocol(), h.FIXED_PROTOCOL)
        for field, value in [('pivotLeft', 3), ('startDate', '2022-01-01'), ('timingToleranceDays', 1)]:
            with self.subTest(field=field):
                protocol = deepcopy(h.FIXED_PROTOCOL)
                protocol[field] = value
                with self.assertRaises(ValueError): h.validate_protocol(protocol)

    def test_isolation_and_annual_label_boundaries(self):
        end = date(2026, 9, 26)
        self.assertEqual(h.block_reason(date(2024, 2, 1), 1, 'time', end), 'isolated')
        self.assertIsNone(h.block_reason(date(2024, 2, 2), 1, 'time', end))
        self.assertIsNone(h.block_reason(date(2024, 12, 26), 1, 'time', end))
        self.assertEqual(h.block_reason(date(2024, 12, 27), 1, 'time', end), 'boundary_excluded')
        self.assertIsNone(h.block_reason(date(2024, 12, 2), 30, 'up', end))
        self.assertEqual(h.block_reason(date(2024, 12, 3), 30, 'up', end), 'boundary_excluded')
        self.assertIsNone(h.block_reason(date(2026, 9, 21), 1, 'time', end))
        self.assertEqual(h.block_reason(date(2026, 9, 22), 1, 'time', end), 'boundary_excluded')

    def test_unknown_is_not_none_and_exclusion_precedence(self):
        self.assertEqual(h.effective_score({'status': 'miss'}, None, False)['status'], 'trigger_unknown')
        self.assertEqual(h.effective_score(None, 'isolated', False)['status'], 'isolated')
        self.assertEqual(h.group_rules([]), 'none')
        self.assertEqual(h.group_rules(['ASTRO-MOON-PHASE', 'ASTRO-NAKSHATRA-INGRESS']), 'same_family')
        self.assertEqual(h.group_rules(['ASTRO-MOON-PHASE', 'GANN-EQUAL-INTERVAL']), 'cross_family')
        self.assertEqual(h.group_rules(['BZ-DAY-METALWATER-UP', 'BZ-DAY-FIRE-DOWN'], True), 'conflict')


class GeneratorTests(unittest.TestCase):
    def test_price_decimal_ties_and_ambiguous(self):
        day = date(2025, 6, 1)
        rows = index(day - 5 * DAY, day + 5 * DAY)
        self.assertIsNone(g.pivot_kind(rows, day))
        rows[day]['prices']['high'] = Decimal('110.0000000000000000000000001')
        self.assertEqual(g.pivot_kind(rows, day), 'high')
        rows[day + DAY]['prices']['high'] = rows[day]['prices']['high']
        self.assertIsNone(g.pivot_kind(rows, day))
        rows[day + DAY]['prices']['high'] = Decimal('110')
        rows[day]['prices']['low'] = Decimal('89')
        self.assertEqual(g.pivot_kind(rows, day), 'ambiguous')

    def test_confirmation_delay_and_strict_deadline(self):
        a = date(2025, 6, 1)
        for distance, expected in [(3, [False, False]), (4, [False, True]), (6, [False, True]), (7, [True, True])]:
            b = a + distance * DAY
            rows = index(a - 2 * DAY, b + 30 * DAY)
            rows[a]['prices']['high'] = Decimal('120')
            rows[b]['prices']['low'] = Decimal('80')
            before = g.midnight(b + 3 * DAY) - timedelta(microseconds=1)
            _, earlier = g.sequential_gann(rows, a, b + 30 * DAY, before)
            self.assertEqual(earlier, [])
            _, projections = g.sequential_gann(rows, a, b + 30 * DAY, g.midnight(b + 3 * DAY))
            self.assertEqual([p['eligible'] for p in projections], expected)
            self.assertTrue(all(p['knownAtUtc'] == g.midnight(b + 3 * DAY).astimezone(timezone.utc).isoformat() for p in projections))

    def test_anchor_90_day_boundary(self):
        a = date(2025, 1, 1)
        for distance, count in [(87, 2), (88, 0)]:
            b = a + distance * DAY
            rows = index(a - 2 * DAY, b + 200 * DAY)
            rows[a]['prices']['high'] = Decimal('120')
            rows[b]['prices']['low'] = Decimal('80')
            _, projections = g.sequential_gann(rows, a, b + 200 * DAY, g.midnight(b + 3 * DAY))
            self.assertEqual(len(projections), count)

    def test_gap_and_ambiguous_break_pairing(self):
        a, b = date(2025, 6, 1), date(2025, 6, 15)
        for breaker in ('gap', 'ambiguous'):
            rows = index(a - 2 * DAY, b + 50 * DAY)
            rows[a]['prices']['high'] = Decimal('120')
            rows[b]['prices']['low'] = Decimal('80')
            middle = a + 7 * DAY
            if breaker == 'gap': rows[middle]['valid'] = False
            else: rows[middle]['prices'].update(high=Decimal('140'), low=Decimal('60'))
            _, projections = g.sequential_gann(rows, a, b + 50 * DAY, g.midnight(b + 3 * DAY))
            self.assertEqual(projections, [])

    def test_same_type_replaces_previous_and_future_does_not_reanchor(self):
        a = date(2025, 6, 1)
        b = a + 14 * DAY
        rows = index(a - 2 * DAY, b + 60 * DAY)
        rows[a]['prices']['high'] = Decimal('120')
        rows[a + 7 * DAY]['prices']['high'] = Decimal('130')
        rows[b]['prices']['low'] = Decimal('80')
        _, original = g.sequential_gann(rows, a, b + 50 * DAY, g.midnight(b + 3 * DAY))
        self.assertEqual({p['anchorADate'] for p in original}, {(a + 7 * DAY).isoformat()})
        rows[b + 7 * DAY]['prices']['high'] = Decimal('500')
        _, changed = g.sequential_gann(rows, a, b + 50 * DAY, g.midnight(b + 3 * DAY))
        self.assertEqual(original, changed)

    def test_conservative_availability_includes_old_gap_not_future_bar(self):
        target = date(2025, 12, 1)
        rows = index(target - 272 * DAY, target - 5 * DAY)
        self.assertTrue(g.gann_availability(rows, target)[0])
        rows[target - 240 * DAY]['valid'] = False
        self.assertFalse(g.gann_availability(rows, target)[0])
        rows[target - 240 * DAY]['valid'] = True
        del rows[target - 5 * DAY]
        self.assertFalse(g.gann_availability(rows, target)[0])

    def test_deduplicate_preserves_sources_and_filters_ineligible(self):
        events = [{'id': name, 'ruleId': 'GANN-EQUAL-INTERVAL', 'targetDate': '2025-01-01',
                   'family': 'GANN', 'kind': 'time', 'eligible': name != 'late'} for name in ('one', 'two', 'one', 'late')]
        grouped = g.deduplicate_events(events)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]['sourceEventIds'], ['one', 'two'])

    def test_inspection_real_updated_future_and_conflict(self):
        day = date(2025, 6, 1)
        raw = make_row(day, updated=CAPTURE.isoformat())
        snap = ({'symbol': 'BTCUSDT'}, {raw['open_ms']: raw}, {})
        original = deepcopy(snap)
        self.assertTrue(g.inspect_days(snap, day, day, CAPTURE)[day]['valid'])
        self.assertEqual(snap, original)
        self.assertFalse(g.inspect_days(snap, day, day, g.midnight(day + DAY))[day]['valid'])
        raw['conflicted'] = 1
        self.assertFalse(g.inspect_days(snap, day, day, CAPTURE)[day]['valid'])


class MetricTests(unittest.TestCase):
    def test_all_candidate_coverage_differs_from_nearest_and_baseline_recall_100(self):
        target = date(2025, 2, 2)
        candidates = [{'date': '2025-01-30', 'kind': 'high', 'pivotId': 'jan30'},
                      {'date': '2025-02-03', 'kind': 'low', 'pivotId': 'feb03'}]
        row = timing_row(target, 1, [candidates[1], candidates[0]])
        summary, details = h.timing_summary([row], h.FIXED_PROTOCOL['controls'])
        baseline = summary['baseline']
        self.assertEqual(baseline['uniqueMatchedPivots'], 1)
        self.assertEqual(baseline['uniqueCoveredPivots'], 2)
        self.assertEqual(baseline['availablePivots'], 2)
        self.assertEqual(baseline['pivotRecallPct'], 100)
        self.assertEqual(details['availablePivotIds'], ['feb03', 'jan30'])

    def test_cumulative_tolerances_and_bins(self):
        day = date(2025, 6, 1)
        rows = [timing_row(day + i * DAY, error, [{'pivotId': 'p' + str(i)}] if error is not None else [])
                for i, error in enumerate((0, 1, 2, 3, None))]
        metric, _, _ = h.timing_metric('fixture', 'fixture', rows, rows, {'p0', 'p1', 'p2', 'p3'})
        self.assertEqual([metric[k] for k in ('exact', 'within1', 'within2', 'within3', 'miss')], [1, 2, 3, 4, 1])
        self.assertEqual(list(metric['errorBins'].values()), [1, 1, 1, 1, 1])

    def test_direction_down_tie_none_and_weighted_baseline(self):
        rows = [direction_row('down', 'down'), direction_row('flat', 'down'), direction_row('up', None, 'none')]
        summary = h.direction_summary(rows, 1)
        rule = summary['rules'][1]
        self.assertEqual((rule['correct'], rule['tie'], rule['hitRatePct']), (1, 1, 50))
        self.assertAlmostEqual(rule['baselineRatePct'], 100 / 3)
        none = next(m for m in summary['groups'] if m['id'] == 'none')
        self.assertIsNone(none['hitRatePct'])
        self.assertEqual(none['actualUp'], 1)

    def test_clusters_do_not_join_years(self):
        clusters = h.overlap_clusters([date(2024, 12, 30), date(2025, 1, 1), date(2025, 1, 7), date(2025, 1, 14)], 'x')
        self.assertEqual([len(c['targetDates']) for c in clusters], [1, 2, 1])
        self.assertEqual(clusters, h.overlap_clusters([date(2025, 1, 7), date(2025, 1, 14), date(2024, 12, 30), date(2025, 1, 1)], 'x'))

    def test_controls_fixed_count_seed_annual_grids_and_coverage_disclosed(self):
        rows = [timing_row(date(year, 2, 2) + offset * DAY) for year in (2024, 2025) for offset in (0, 1, 2, 7, 8, 9, 15, 25)]
        chosen = [date(2024, 2, 2), date(2024, 2, 3), date(2025, 2, 9)]
        one, samples = h.circular_controls(chosen, rows, h.FIXED_PROTOCOL['controls'])
        self.assertEqual((one, samples), h.circular_controls(chosen, rows, h.FIXED_PROTOCOL['controls']))
        self.assertEqual(len(samples), 100)
        self.assertTrue(all(s['count'] == 3 for s in samples))
        self.assertTrue(all(set(s['offsetsByYear']) == {'2024', '2025'} for s in samples))
        self.assertFalse(one['exactCoverageMatched'])
        self.assertIn('not_calendar_day_gaps', one['spacingPreserved'])
        self.assertTrue(any(s['actualMinusControlCoveragePp'] != 0 for s in samples))


class ReportTests(unittest.TestCase):
    def test_small_mocked_report_real_timestamps_down_details_and_unknown_mask(self):
        target_days = [date(2021, 2, 2), date(2021, 2, 3), date(2021, 2, 4)]
        rows = [make_row(day, close='99', updated=CAPTURE.isoformat()) for day in g.days_between(date(2020, 1, 1), date(2026, 9, 27))]
        # Today is not yet a complete UTC+8 candle; validation must exclude it.
        rows[-1]['is_closed'] = 0
        snap = ({'symbol': 'BTCUSDT', 'generation': 3, 'last_success': CAPTURE.isoformat()}, {r['open_ms']: r for r in rows}, {})
        original = deepcopy(snap)
        calendars = {day.isoformat(): {'known': True, 'ruleIds': ['BZ-DAY-FIRE-DOWN'] if i == 0 else [], 'errors': []} for i, day in enumerate(target_days)}
        astronomical = {day.isoformat(): {'known': i != 2, 'errors': [] if i != 2 else ['fixture']} for i, day in enumerate(target_days)}
        with patch.object(h, '_target_days', return_value=target_days), patch.object(h, 'calendar_events', return_value=(calendars, [])), patch.object(h, 'astronomical_events', return_value=(astronomical, [])):
            report = h.build_report(snap, h.load_protocol(), CAPTURE)
        self.assertEqual(snap, original)
        self.assertFalse(report['registeredPrediction'])
        daily = report['details']['daily']
        self.assertEqual(daily[0]['directionScores']['1']['status'], 'correct')
        self.assertEqual(daily[0]['directionScores']['1']['directionResult']['expected'], 'down')
        self.assertFalse(daily[0]['directionScores']['1']['prospectiveEligible'])
        self.assertEqual(daily[0]['reconstructionCreatedAtUtc'], CAPTURE.isoformat())
        self.assertEqual(daily[1]['scoreReferenceMeaning'], 'up_reference_for_actual_distribution_only')
        self.assertIsNone(daily[2]['timingGroup'])
        self.assertEqual(daily[2]['timingScore']['status'], 'trigger_unknown')
        self.assertEqual(report['timing']['baseline']['evaluated'], 2)
        self.assertEqual(report['years'][0]['timingBaseline']['evaluated'], 2)
        self.assertEqual(report['quality']['timingTriggerUnknownDays'], 1)
        self.assertEqual(report['source']['pendingDays'], 1)
        json.dumps(report, ensure_ascii=False, allow_nan=False)

    def test_invalid_clock_and_wrong_asset(self):
        with self.assertRaises(ValueError): h.build_report(({}, {}, {}), h.load_protocol(), datetime(2026, 9, 27))
        with self.assertRaises(ValueError): g.inspect_days(({'symbol': 'ETHUSDT'}, {}, {}), date(2025, 1, 1), date(2025, 1, 1), CAPTURE)


if __name__ == '__main__':
    unittest.main()
