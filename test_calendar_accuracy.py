"""Focused independent-oracle, classification and stale-evidence regressions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import calendar_accuracy as audit
import calendar_adapter as adapter


class CalendarAccuracyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = json.loads(audit.REFERENCE.read_text(encoding='utf-8'))
        cls.report = audit.run_accuracy_validation()

    def test_official_day_example_and_leap_day_are_calendar_dates(self):
        self.assertEqual(audit.independent_day('2016-10-10'), (2457672, '乙丑'))
        self.assertEqual(audit.independent_day('2024-02-29')[0] + 1,
                         audit.independent_day('2024-03-01')[0])

    def test_ordinary_month_and_complete_pillars_have_manual_expected_anchor(self):
        self.assertEqual(audit.independent_pillars('2024-02-10T12:00:00+08:00', self.ref),
                         {'year': '甲辰', 'month': '丙寅', 'day': '甲辰', 'hour': '庚午'})
        january = audit.independent_pillars('2025-01-15T12:00:00+08:00', self.ref)
        self.assertEqual((january['year'], january['month']), ('甲辰', '丁丑'))

    def test_late_zi_and_midnight_keep_distinct_day_and_hour_references(self):
        before = audit.independent_pillars('2024-02-09T22:59:59+08:00', self.ref)
        late = audit.independent_pillars('2024-02-09T23:00:00+08:00', self.ref)
        next_day = audit.independent_pillars('2024-02-10T00:00:00+08:00', self.ref)
        self.assertEqual((before['day'], before['hour']), ('癸卯', '癸亥'))
        self.assertEqual((late['day'], late['hour']), ('癸卯', '甲子'))
        self.assertEqual((next_day['day'], next_day['hour']), ('甲辰', '甲子'))

    def test_minute_reference_cannot_masquerade_as_second_precision(self):
        for at in ('2024-02-04T16:27:00+08:00', '2024-02-04T16:29:00+08:00'):
            with self.assertRaises(audit.ReferencePrecisionError):
                audit.independent_pillars(at, self.ref)
        before = audit.independent_pillars('2024-02-04T16:24:00+08:00', self.ref)
        after = audit.independent_pillars('2024-02-04T16:30:00+08:00', self.ref)
        self.assertEqual((before['year'], before['month']), ('癸卯', '乙丑'))
        self.assertEqual((after['year'], after['month']), ('甲辰', '丙寅'))

    def test_independent_oracle_does_not_call_system_under_test(self):
        with (patch.object(adapter, 'calculate_calendar', side_effect=AssertionError('must not call')),
              patch.object(adapter, 'at_local_iso', side_effect=AssertionError('must not call'))):
            result = audit.independent_pillars('2024-06-15T12:00:00+08:00', self.ref)
        self.assertEqual(result['month'], '庚午')

    def test_outside_reference_scope_is_not_extrapolated(self):
        for at in ('2002-03-10T18:00:00+08:00', '2025-02-04T00:00:00+08:00'):
            with self.assertRaises(ValueError):
                audit.independent_pillars(at, self.ref)
        for at in ('2024-02-10T00:00:00', '2024-02-10T00:00:00-00:00', '2024-02-10T00:00:00.1+08:00'):
            with self.assertRaises(ValueError):
                audit.parse_local(at)

    def test_evidence_categories_do_not_inflate_external_full_charts(self):
        self.assertEqual(self.report['status'], 'passed')
        summary = self.report['summary']
        self.assertEqual(summary['executedChecks'], 124)
        self.assertEqual([summary[c]['total'] for c in audit.CATEGORIES], [1, 95, 27, 1])
        self.assertEqual(summary['officialFullFourPillarSamples'], 0)
        self.assertEqual(summary['secondPrecisionSolarTermsVerified'], 0)
        self.assertTrue(self.report['oldValidation']['unchanged'])
        self.assertEqual(self.report['oldValidation']['executedChecks'], 107)
        self.assertFalse(self.report['marketPredictionValidated'])
        self.assertFalse(self.report['assetNatalProfilesValidated'])
        internal = [r for r in self.report['checks'] if r.get('subkind') == 'exact_library_boundary']
        self.assertEqual(len(internal), 12)
        self.assertTrue(all(r['boundaryTimeOrigin'] == 'system_under_test' for r in internal))

    def test_user_screenshot_is_one_non_official_example(self):
        sample = self.report['examples'][0]
        self.assertEqual(sample['at'], '2002-03-10T18:00:00+08:00')
        self.assertEqual(sample['actual'], {'year': '壬午', 'month': '癸卯', 'day': '丁丑', 'hour': '己酉'})
        self.assertEqual(sample['sourceIds'], ['V12-USER-WENZHEN-01'])
        self.assertEqual(sample['category'], 'userScreenshotFullCharts')

    def test_wrong_month_oracle_detects_mismatch_even_if_day_passes(self):
        class BadAdapter:
            at_local_iso = staticmethod(adapter.at_local_iso)

            @staticmethod
            def calculate_calendar(at):
                result = copy.deepcopy(adapter.calculate_calendar(at))
                if at == '2024-06-15T12:00:00+08:00':
                    result['pillars']['month'] = '辛未'
                return result

        report = audit.run_accuracy_validation(api=BadAdapter)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['summary']['formulaFourPillarChecks']['failed'], 1)
        self.assertEqual(report['summary']['officialDayExamples']['failed'], 0)

    def test_loading_refuses_stale_inputs_and_inconsistent_counts(self):
        with tempfile.TemporaryDirectory(prefix='calendar-accuracy-') as temp:
            path = Path(temp) / 'report.json'
            path.write_text(json.dumps(self.report, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(audit.load_accuracy_evidence(path)['summary']['passed'], 124)
            state = copy.deepcopy(self.report['artifacts'])
            state['inputSha256']['calendar_adapter.py'] = 'changed'
            with patch.object(audit, '_artifact_state', return_value=state):
                with self.assertRaises(audit.CalendarAccuracyEvidenceError):
                    audit.load_accuracy_evidence(path)
            corrupted = copy.deepcopy(self.report)
            corrupted['summary']['userScreenshotFullCharts']['passed'] = 124
            path.write_text(json.dumps(corrupted), encoding='utf-8')
            with self.assertRaises(audit.CalendarAccuracyEvidenceError):
                audit.load_accuracy_evidence(path)


if __name__ == '__main__':
    unittest.main()
