"""Checks the frozen report's core audit boundaries without retuning rules."""
from datetime import date, timedelta
import unittest
from unittest.mock import patch
from pathlib import Path

from coinbase_study import timing_label
from coinbase_study_store import CoinbaseStudyIntegrityError, RUN, read


class CoinbaseStudyTests(unittest.TestCase):
    def test_frozen_report_exposes_gaps_and_no_2026_timing_sample(self):
        report = read('report')
        self.assertEqual(report['studyId'], 'CB-USD-HX-001')
        self.assertEqual([s['symbol'] for s in report['symbols']], ['BTCUSD', 'ETHUSD'])
        for symbol in report['symbols']:
            self.assertEqual(symbol['source']['missingSourceDays'], 3)
            self.assertEqual(symbol['blocks'][1]['timing']['commonEligibleDays'], 0)
        self.assertGreater(report['symbols'][0]['blocks'][0]['timing']['commonEligibleDays'], 0)

    def test_manifest_rejects_modified_report(self):
        original_read = Path.read_bytes
        def changed_report(path):
            body = original_read(path)
            return body + b' ' if path.name == 'report.json' else body
        with patch.object(Path, 'read_bytes', changed_report):
            with self.assertRaises(CoinbaseStudyIntegrityError):
                read('report', RUN)

    def test_pivot_window_requires_complete_neighbors(self):
        start = date(2026, 1, 1)
        end = start + 20 * timedelta(days=1)
        index = {start + i * timedelta(days=1): {'valid': True, 'prices': {'high': 2, 'low': 1}} for i in range(21)}
        label, reason = timing_label(start + 10 * timedelta(days=1), index, start, end)
        self.assertIsNone(reason)
        self.assertFalse(label['hit'])
        index[start + 6 * timedelta(days=1)]['valid'] = False
        label, reason = timing_label(start + 10 * timedelta(days=1), index, start, end)
        self.assertIsNone(label)
        self.assertEqual(reason, 'missing_pivot_neighborhood')


if __name__ == '__main__':
    unittest.main()
