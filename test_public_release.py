"""Checks for the published plan and an explicitly synthetic scoring example."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import unittest

from coinbase_feed import local_open, ms
from coinbase_forward import CoinbaseForward, digest, verify


ROOT = Path(__file__).resolve().parent


class PublicReleaseTests(unittest.TestCase):
    def test_published_registration_seal_and_protocol(self):
        raw = (ROOT / 'public_data/coinbase_forward_registration.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '1742c8550a401356f9e500896ec234fc0ba0684b5942655a65cb1d5f37daf978')
        plan = json.loads(raw)
        protocol = json.loads((ROOT / 'coinbase_forward_protocol.json').read_text(encoding='utf-8'))
        verify(plan)
        self.assertEqual(plan['contentSha256'], 'ccf4191d4e4b1c66a318d1300e669bbf01d4bb804d4c935387c5a32c74fec690')
        self.assertEqual(plan['protocol'], protocol)
        self.assertEqual(plan['protocolSha256'], digest(protocol))
        self.assertEqual(plan['scheduleSha256'], digest(plan['schedule']))
        self.assertEqual((plan['startDate'], plan['endDate']), ('2026-10-19', '2026-11-17'))
        self.assertEqual(len(plan['schedule']), 30)
        self.assertEqual([row['date'] for row in plan['schedule']], [
            (date(2026, 10, 19) + timedelta(days=i)).isoformat() for i in range(30)])
        self.assertFalse(plan['marketValidated'])
        self.assertFalse(plan['independentBlindSample'])

    def test_public_plan_is_visible_without_local_registration(self):
        study = CoinbaseForward(ROOT / 'unused_local_registration_for_test', None, base=ROOT,
                                clock=lambda: datetime(2026, 10, 22, tzinfo=timezone.utc))
        status = study.status()
        self.assertIsNone(status['registration'])
        self.assertIsNone(status['latestEvaluation'])
        self.assertEqual(status['evaluationCount'], 0)
        self.assertEqual(status['publicRegistration']['contentSha256'],
                         'ccf4191d4e4b1c66a318d1300e669bbf01d4bb804d4c935387c5a32c74fec690')

    def test_synthetic_scoring_fixture(self):
        case = json.loads((ROOT / 'examples/synthetic_forward_case.json').read_text(encoding='utf-8'))
        target = date.fromisoformat(case['targetDate'])
        candles = {}
        for offset in range(-case['neighborDaysEachSide'], case['neighborDaysEachSide'] + 1):
            day = target + timedelta(days=offset)
            open_ms = ms(local_open(day))
            prices = case['targetCandle'] if offset == 0 else case['ordinaryCandle']
            candles[open_ms] = {'symbol': 'BTCUSD', 'open_ms': open_ms,
                                'close_ms': open_ms + 86400000 - 1, **prices,
                                'is_closed': 1, 'conflicted': 0, 'request_id': 'synthetic',
                                'body_sha256': 'a' * 64, 'updated': '2026-11-01T00:00:00+00:00'}
        registration = {'registeredAtUtc': case['registeredAtUtc'],
                        'startDate': case['targetDate'], 'endDate': case['targetDate'],
                        'schedule': [{'date': case['targetDate'], 'boundaryExcluded': False,
                                      'directionRuleIds': [case['directionRuleId']],
                                      'timeEvents': [{'ruleId': case['timeRuleId'], 'eventId': 'synthetic'}]}]}
        score = CoinbaseForward(ROOT / 'unused_local_registration_for_test', None, base=ROOT)._score(
            registration, ({'symbol': 'BTCUSD'}, candles, {}), datetime(2026, 11, 1, tzinfo=timezone.utc))
        row, expected = score['rows'][0], case['expected']
        self.assertEqual(row['directionStatus'], expected['directionStatus'])
        self.assertEqual(row['directionResults'][case['directionRuleId']], expected['directionResult'])
        self.assertEqual(row['timeStatus'], expected['timeStatus'])
        self.assertEqual(row['timing']['hit'], expected['timingHit'])
        self.assertEqual(row['timing']['errorDays'], expected['errorDays'])


if __name__ == '__main__':
    unittest.main()
