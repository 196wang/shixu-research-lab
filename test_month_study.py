"""Synthetic denominators, exact Jie boundaries and partial-month checks."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

import month_study as m
from test_prediction_scoring import make_row

DAY = timedelta(days=1)
NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def sample(day, *, episode='test', actual='up', triggers=True, month='庚寅', rule=None):
    rule = rule or m.RULES[0]['id']
    return {'date': day.isoformat(), 'year': day.year, 'episodeId': episode, 'actual': actual,
            'returnPct': 1.0 if actual == 'up' else -1.0 if actual == 'down' else 0.0,
            'ruleIds': [rule] if triggers else [], 'conditionIds': m.condition_ids(month),
            'pillars': {'month': month, 'day': '庚寅'}, 'status': 'eligible'}


def snapshot(first, last, **changes):
    rows = [make_row(day, updated=NOW.isoformat(), **changes) for day in m.days_between(first, last)]
    return ({'symbol': 'BTCUSDT', 'generation': 5, 'last_success': NOW.isoformat()}, {r['open_ms']: r for r in rows}, {})


def fake_episode(start, end, partial_start=False, partial_end=False, observed_start=None, observed_end=None):
    first, last = m.ceil_midnight(start).date(), end.date()
    observed_start, observed_end = observed_start or first, observed_end or last
    return {'id': 'fixture', 'year': start.year, 'term': '立春', 'startAt': start.isoformat(), 'endAt': end.isoformat(),
            'pillar': '庚寅', 'conditionIds': m.condition_ids('庚寅'), 'stemElement': '金', 'branchElement': '木',
            'interiorStartDate': first.isoformat(), 'interiorEndExclusiveDate': last.isoformat(), 'interiorDays': (last-first).days,
            'observedStartDate': observed_start.isoformat(), 'observedEndExclusiveDate': observed_end.isoformat(),
            'observedDays': (observed_end-observed_start).days, 'partialStart': partial_start, 'partialEnd': partial_end}


class CalendarTests(unittest.TestCase):
    def test_fixed_protocol_and_condition_order(self):
        self.assertEqual(m.load_protocol(), m.FIXED_PROTOCOL)
        bad = m.load_protocol()
        bad['dailyHorizonDays'] = 3
        with self.assertRaises(ValueError): m.validate_protocol(bad)
        self.assertEqual(len(m.CONDITIONS), 18)
        self.assertEqual([c['id'] for c in m.CONDITIONS[-12:]], ['MONTH-BRANCH-' + b for b in m.BRANCHES])

    def test_stem_branch_elements_and_rule_overlap(self):
        self.assertIn('MONTH-STEM-METALWATER', m.condition_ids('壬寅'))
        self.assertNotIn('MONTH-BRANCH-METALWATER', m.condition_ids('壬寅'))
        self.assertIn('MONTH-BOTH-METALWATER', m.condition_ids('庚申'))
        self.assertIn('MONTH-BRANCH-FIRE', m.condition_ids('乙巳'))
        self.assertEqual(m.rule_ids('丙寅'), [m.RULES[1]['id'], m.RULES[2]['id']])
        self.assertEqual(m.rule_ids('甲子'), [])

    def test_strict_inside_and_exact_midnight_boundary(self):
        day = date(2024, 2, 4)
        lo = m.midnight(day)
        boundaries = [('甲', lo), ('乙', lo + timedelta(hours=12)), ('丙', lo + DAY)]
        self.assertEqual([e['term'] for e in m.daily_transitions(day, boundaries)], ['乙'])
        self.assertEqual(m.ceil_midnight(lo), lo)
        self.assertEqual(m.ceil_midnight(lo + timedelta(seconds=1)), lo + DAY)

    def test_real_lichun_jingzhe_and_non_jie_not_excluded(self):
        episodes, boundaries = m.episode_plan(date(2024, 2, 1), date(2024, 3, 31))
        lichun = next(e for e in episodes if e['term'] == '立春')
        self.assertEqual(lichun['pillar'], '丙寅')
        self.assertEqual(lichun['interiorStartDate'], '2024-02-05')
        self.assertEqual(lichun['interiorEndExclusiveDate'], '2024-03-05')
        self.assertEqual(lichun['interiorDays'], 29)  # leap February
        self.assertEqual([e['term'] for e in m.daily_transitions(date(2024, 2, 4), boundaries)], ['立春'])
        self.assertEqual([e['term'] for e in m.daily_transitions(date(2024, 3, 5), boundaries)], ['惊蛰'])
        self.assertEqual(m.daily_transitions(date(2024, 2, 19), boundaries), [])  # 雨水 is not Jie


class MatchedTests(unittest.TestCase):
    def test_weighted_complement_uses_trigger_n_not_complement_pooling(self):
        rows = [sample(date(2025, 2, d), episode='A') for d in (2, 3)]
        rows += [sample(date(2025, 2, 4), episode='A', triggers=False, actual='down')]
        rows += [sample(date(2025, 3, 2), episode='B', actual='down')]
        rows += [sample(date(2025, 3, d), episode='B', triggers=False) for d in (3, 4, 5)]
        result = m.matched_complement(rows, m.RULES[0])
        self.assertEqual(result['n'], 3)
        self.assertAlmostEqual(result['hitRatePct'], 200/3)
        self.assertAlmostEqual(result['baselineRatePct'], 100/3)
        self.assertAlmostEqual(result['excessPp'], 100/3)
        self.assertEqual(result['complementN'], 4)

    def test_unmatched_excluded_from_both_rates(self):
        rows = [sample(date(2025, 2, 2), episode='unmatched')]
        rows += [sample(date(2025, 3, 2), episode='matched', actual='down'), sample(date(2025, 3, 3), episode='matched', triggers=False)]
        result = m.matched_complement(rows, m.RULES[0])
        self.assertEqual((result['n'], result['unmatchedN'], result['unmatchedEpisodeCount']), (1, 1, 1))
        self.assertEqual((result['hitRatePct'], result['baselineRatePct'], result['excessPp']), (0, 100, -100))

    def test_same_solar_month_does_not_borrow_across_year(self):
        rows = [sample(date(2024, 12, 31), episode='crossyear'), sample(date(2025, 1, 1), episode='crossyear', triggers=False)]
        result = m.matched_complement(rows, m.RULES[0])
        self.assertEqual(result['n'], 0)
        self.assertEqual(result['unmatchedN'], 1)
        self.assertIsNone(result['excessPp'])
        self.assertEqual(result['episodes'][0]['year'], 2024)

    def test_ties_retained_and_down_baseline_direction(self):
        rule = m.RULES[1]
        rows = [sample(date(2025, 2, 2), actual='down', rule=rule['id']),
                sample(date(2025, 2, 3), actual='flat', rule=rule['id']),
                sample(date(2025, 2, 4), actual='flat', triggers=False),
                sample(date(2025, 2, 5), actual='down', triggers=False)]
        result = m.matched_complement(rows, rule)
        self.assertEqual(result['hitRatePct'], 50)
        self.assertEqual(result['baselineRatePct'], 50)
        self.assertEqual(result['tie'], 1)

    def test_all_month_context_and_branch_partition(self):
        rows = [sample(date(2025, 2, 2), month='庚寅'), sample(date(2025, 3, 2), month='辛卯', actual='down')]
        summary = m.condition_summary(rows)
        yin = next(c for c in summary if c['id'] == 'MONTH-BRANCH-寅')['rules'][0]
        self.assertEqual(yin['n'], 1)
        self.assertEqual(yin['allMonthBaseline']['n'], 2)
        self.assertEqual(yin['allMonthBaseline']['hitRatePct'], 50)
        context = m.calendar_context(rows)
        self.assertEqual(sum(r['n'] for r in context['monthBranches']), len(rows))
        self.assertEqual(sum(r['metric']['n'] for r in context['dayBranches']), len(rows))
        self.assertEqual([r['branch'] for r in context['monthBranches']], list(m.BRANCHES))


class MonthlyTests(unittest.TestCase):
    def test_complete_interior_open_to_last_close(self):
        start = m.midnight(date(2025, 2, 2)) + timedelta(hours=15)
        end = m.midnight(date(2025, 3, 3)) + timedelta(hours=3)
        plan = fake_episode(start, end)
        snap = snapshot(date(2025, 2, 3), date(2025, 3, 2), close='105')
        values = m.inspect_days(snap, date(2025, 2, 3), date(2025, 3, 2), NOW)
        result = m.evaluate_episodes([plan], values, NOW)[0]
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['returnPct'], 5.0)
        self.assertEqual(result['observedDays'], 28)
        self.assertEqual(result['open'], '100')

    def test_partial_retains_observation_but_excluded_from_month_aggregate(self):
        plan = fake_episode(m.midnight(date(2026, 9, 7)) + timedelta(hours=1), m.midnight(date(2026, 10, 8)),
                            partial_end=True, observed_end=date(2026, 9, 27))
        snap = snapshot(date(2026, 9, 8), date(2026, 9, 26), close='105')
        values = m.inspect_days(snap, date(2026, 9, 8), date(2026, 9, 26), NOW)
        result = m.evaluate_episodes([plan], values, NOW)[0]
        self.assertEqual(result['status'], 'partial')
        self.assertIsNone(result['returnPct'])
        self.assertEqual(result['observedReturnPct'], 5)
        all_months = m.monthly_summary([result])[0]
        self.assertEqual((all_months['n'], all_months['partialEpisodes']), (0, 1))

    def test_one_gap_or_conflict_invalidates_whole_episode(self):
        first, last = date(2025, 2, 3), date(2025, 2, 7)
        plan = fake_episode(m.midnight(first), m.midnight(last + DAY))
        for problem in ('missing', 'conflicted'):
            snap = snapshot(first, last)
            key = int(m.midnight(first + DAY).timestamp()*1000)
            if problem == 'missing': del snap[1][key]
            else: snap[1][key]['conflicted'] = 1
            result = m.evaluate_episodes([plan], m.inspect_days(snap, first, last, NOW), NOW)[0]
            self.assertEqual(result['status'], 'missing')
            self.assertIsNone(result['returnPct'])
            self.assertIsNone(result['observedReturnPct'])
            self.assertEqual(len(result['missingDates']), 1)

    def test_pending_never_uses_future_completed_price(self):
        first, last = date(2026, 9, 27), date(2026, 9, 28)
        plan = fake_episode(m.midnight(first), m.midnight(last + DAY))
        snap = snapshot(first, last, close='105')
        result = m.evaluate_episodes([plan], m.inspect_days(snap, first, last, NOW), NOW)[0]
        self.assertEqual(result['status'], 'pending')
        self.assertIsNone(result['returnPct'])

    def test_august_september_2026_plans_keep_exact_partial_boundary(self):
        plans, _ = m.episode_plan(date(2021, 1, 1), date(2026, 9, 26))
        august = next(e for e in plans if e['year'] == 2026 and e['term'] == '立秋')
        september = next(e for e in plans if e['year'] == 2026 and e['term'] == '白露')
        self.assertFalse(august['partialEnd'])
        self.assertTrue(september['partialEnd'])
        self.assertEqual(september['observedEndExclusiveDate'], '2026-09-27')
        self.assertGreater(september['interiorEndExclusiveDate'], '2026-09-27')


class IntegrationTests(unittest.TestCase):
    def test_small_synthetic_build_no_timestamp_rewrite_no_isolation(self):
        targets = [date(2021, 1, 1), date(2024, 2, 4), date(2024, 2, 5)]
        snap = snapshot(date(2021, 1, 1), date(2026, 9, 26), close='105')
        original = deepcopy(snap)
        with patch.object(m, '_target_days', return_value=targets):
            report = m.build_report(snap, m.load_protocol(), NOW)
        self.assertEqual(snap, original)
        self.assertFalse(report['marketValidated'])
        self.assertFalse(report['registeredPrediction'])
        daily = report['details']['daily']
        self.assertEqual([r['status'] for r in daily], ['eligible', 'boundary_excluded', 'eligible'])
        self.assertTrue(all(r['sourceRef']['updated'] == NOW.isoformat() for r in daily))
        self.assertEqual(report['quality']['eligibleDays'], 2)
        self.assertEqual(len(report['daily']['conditions']), 18)
        self.assertEqual(len(report['examples']), 2)
        self.assertEqual(report['examples'][1]['status'], 'partial')
        self.assertEqual([r['year'] for r in report['daily']['years']], [2021, 2024])
        json.dumps(report, ensure_ascii=False, allow_nan=False)

    def test_wrong_asset_and_naive_capture_rejected(self):
        with self.assertRaises(ValueError): m.build_report(({}, {}, {}), m.load_protocol(), datetime(2026, 9, 27))
        with self.assertRaises(ValueError): m.build_report(({'symbol': 'ETHUSDT'}, {}, {}), m.load_protocol(), NOW)

    def test_decimal_micro_move_is_not_tie(self):
        opened, closed = Decimal('100'), Decimal('100.0000000000000000000001')
        self.assertEqual(m.actual_direction(opened, closed), 'up')


if __name__ == '__main__': unittest.main()
