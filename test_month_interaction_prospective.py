"""Future schedule, append-only scoring and integrity checks."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import unittest

import month_interaction_prospective as study


class FakeClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 11, 12, tzinfo=timezone.utc).astimezone(tz or timezone.utc)


class FakeRegisterClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 29, 12, tzinfo=timezone.utc).astimezone(tz or timezone.utc)


class FakeMarket:
    def __init__(self, candles=None):
        self.candles = candles or {}

    def _snapshot(self, symbol):
        return {'symbol': symbol}, self.candles, {}


class MonthInteractionTests(unittest.TestCase):
    def test_fixed_schedule_has_fourteen_combinations_and_no_past_dates(self):
        with TemporaryDirectory() as directory:
            rows = study.MonthInteractionProspective(directory, FakeMarket()).schedule()
        self.assertEqual(len(rows), 365)
        self.assertEqual(rows[0]['date'], '2026-10-09')
        self.assertEqual(rows[-1]['date'], '2027-10-08')
        self.assertEqual(len(study.CONDITIONS) * len(study.RULES), 14)
        self.assertEqual(rows[0]['monthPillar'], '戊戌')
        self.assertFalse(rows[0]['excluded'])

    def test_register_then_score_only_closed_valid_day_and_detect_tamper(self):
        with TemporaryDirectory() as directory:
            first = study.START
            key = int(study.midnight(first).timestamp() * 1000)
            candle = {'symbol': study.SYMBOL, 'open_ms': key, 'close_ms': key + 86_400_000 - 1,
                      'open': '100', 'high': '110', 'low': '90', 'close': '105', 'volume': '1',
                      'is_closed': 1, 'conflicted': 0, 'request_id': 'req-test',
                      'body_sha256': 'a' * 64, 'updated': '2026-10-10T00:00:00Z'}
            desk = study.MonthInteractionProspective(directory, FakeMarket({key: candle}))
            with patch.object(study, 'datetime', FakeRegisterClock):
                registered = desk.register()
                self.assertTrue(registered['registered'])
                self.assertEqual(registered['scoredCount'], 0)
            with patch.object(study, 'datetime', FakeClock):
                scored = desk.evaluate()
                self.assertEqual(scored['scoredCount'], 1)
                self.assertEqual(desk.evaluate()['scoredCount'], 1)
                self.assertEqual(scored['rows'][0]['result']['actual'], 'up')
                self.assertTrue(all(x['matchedN'] == 0 for x in scored['summary']))
            path = Path(directory) / 'month_interaction_future' / 'evaluations.jsonl'
            altered = json.loads(path.read_text(encoding='utf-8').splitlines()[0])
            altered['actual'] = 'down'
            path.write_text(json.dumps(altered) + '\n', encoding='utf-8')
            with self.assertRaises(study.MonthInteractionError):
                desk.status()


if __name__ == '__main__':
    unittest.main()
