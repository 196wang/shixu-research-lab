"""Deterministic preview checks; no external requests or production database writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone, date
import unittest
from unittest.mock import patch

import chart_candidates as c
from prediction_ledger import normalize_body
from test_prediction_scoring import make_row

NOW = datetime(2026, 9, 27, 5, tzinfo=timezone.utc)


class Market:
    def __init__(self, symbol='BTCUSDT'):
        self.calls = []
        self.snapshot = ({'symbol': symbol, 'generation': 4, 'last_success': NOW.isoformat()}, {}, {})
    def _snapshot(self, symbol):
        self.calls.append(symbol)
        return deepcopy(self.snapshot)


def raw_candidate(family='GANN', kind='time', day='2026-10-05', rule='GANN-EQUAL-INTERVAL', evidence=None, symbol='BTCUSDT'):
    return {'id': 'old-id', 'family': family, 'label': 'fixture', 'reason': 'fixture hypothesis',
            'evidence': evidence or {}, 'canRegister': True, 'registrationDeadlineAt': 'old',
            'draft': {'symbol': symbol, 'kind': kind, 'targetDate': day, 'horizonDays': 1,
                      'title': 'fixture', 'note': 'old dynamic note', 'invalidation': None,
                      'rules': [{'id': rule, 'family': family, 'direction': kind, 'version': '0.15.0'}]}}


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.gann = patch('gann_timing.calculate_gann', return_value={'projections': []})
        self.astro = patch('astro_events.calculate_astro_events', return_value={'events': []})
        self.gann.start(); self.astro.start()
        self.addCleanup(self.gann.stop); self.addCleanup(self.astro.stop)

    def call(self, symbol='BTCUSDT', now=NOW, market=None):
        return c.chart_candidates(market or Market(symbol), symbol, clock=lambda: now)

    def test_exact_thirty_future_beijing_dates_cross_year(self):
        result = self.call(now=datetime(2026, 12, 31, 16, tzinfo=timezone.utc))
        self.assertEqual(result['range']['startDate'], '2027-01-02')
        self.assertEqual(result['range']['endDate'], '2027-01-31')
        self.assertEqual(len(result['days']), 30)
        self.assertEqual(len({d['date'] for d in result['days']}), 30)

    def test_query_rejects_duplicate_unknown_missing_blank_and_wrong_asset(self):
        self.assertEqual(c.parse_chart_candidates_query('symbol=ETHUSDT'), 'ETHUSDT')
        for query in ('', 'symbol=', 'symbol=BTCUSDT&symbol=BTCUSDT', 'symbol=BTCUSDT&start=2026-10-01', 'symbol=BTC', 'symbol=BTCUSDT&'):
            with self.subTest(query=query), self.assertRaises(ValueError): c.parse_chart_candidates_query(query)

    def test_btc_yin_conflict_and_same_family_direction_agreement(self):
        result = self.call()
        yin_days = [d for d in result['days'] if d['pillars']['day'][1] == '寅']
        self.assertTrue(yin_days)
        for day in yin_days:
            rules = [x for x in result['candidates'] if x['id'] in day['candidateIds']]
            self.assertIn(c.YIN_RULE_ID, {r['ruleId'] for r in rules})
            if day['dayStemElement'] in '金水': self.assertTrue(day['hasDirectionConflict'])
            if day['dayStemElement'] == '火': self.assertTrue(day['sameFamilyAgreement'])
        # The September snapshot's next month has only Jia-Yin / Bing-Yin.
        # Metal/water Yin appears in a later server window and must then conflict.
        later = self.call(now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        self.assertTrue(any(d['hasDirectionConflict'] for d in later['days']))
        for item in result['candidates']:
            self.assertEqual(item['status'], 'candidate')
            self.assertFalse(item['marketValidated'])
            self.assertIn(item['candidateKey'], item['draft']['note'])
            self.assertEqual(normalize_body(item['draft']), item['draft'])

    def test_eth_never_transfers_btc_direction(self):
        result = self.call('ETHUSDT')
        self.assertEqual(result['candidates'], [])
        self.assertTrue(all(not day['upIds'] and not day['downIds'] for day in result['days']))
        self.assertTrue(any('ETH' in warning for warning in result['warnings']))
        self.assertTrue(all(day['pillars'] for day in result['days']))

    def test_semantic_id_and_draft_stable_despite_dynamic_gann_evidence(self):
        projection = {'id': 'pair-3-x1', 'anchorAId': 'BTC-a-low', 'anchorBId': 'BTC-b-high',
                      'anchorADate': '2026-09-01', 'anchorBDate': '2026-09-18', 'intervalDays': 17, 'multiplier': 1}
        original = raw_candidate(evidence={'projection': projection, 'sources': [{'snapshotReadAtUtc': NOW.isoformat()}]})
        changed = deepcopy(original)
        changed['id'] = 'new-volatile-id'
        changed['evidence']['projection']['id'] = 'pair-999-x1'
        changed['evidence']['sources'][0]['snapshotReadAtUtc'] = (NOW+timedelta(seconds=2)).isoformat()
        first, last = date(2026, 9, 28), date(2026, 10, 27)
        a, b = (c.normalize_candidate(item, 'BTCUSDT', NOW, first, last) for item in (original, changed))
        self.assertEqual(a['id'], b['id'])
        self.assertEqual(a['draft'], b['draft'])
        self.assertNotEqual(a['legacyId'], b['legacyId'])
        self.assertNotEqual(a['evidenceHash'], b['evidenceHash'])
        changed['evidence']['projection']['multiplier'] = 2
        self.assertNotEqual(a['id'], c.normalize_candidate(changed, 'BTCUSDT', NOW, first, last)['id'])

    def test_neutral_deadline_strict_and_direction_one_day(self):
        first, last = date(2026, 9, 28), date(2026, 10, 27)
        at_deadline = c.midnight(date(2026, 9, 28))
        time = raw_candidate(day='2026-10-01')
        before = c.normalize_candidate(time, 'BTCUSDT', at_deadline-timedelta(microseconds=1), first, last)
        at = c.normalize_candidate(time, 'BTCUSDT', at_deadline, first, last)
        self.assertTrue(before['canRegister'])
        self.assertFalse(at['canRegister'])
        self.assertEqual((at['windowStartDate'], at['windowEndDate']), ('2026-09-28', '2026-10-04'))
        up = c.normalize_candidate(raw_candidate('BAZI','up','2026-10-01','BZ-DAY-METALWATER-UP'), 'BTCUSDT', NOW, first, last)
        self.assertEqual((up['windowStartDate'], up['windowEndDate']), ('2026-10-01', '2026-10-01'))

    def test_stable_provenance_retained_but_new_inputs_do_not_change_math_key(self):
        raw = raw_candidate(evidence={'projection': {'anchorAId':'a','anchorBId':'b','knownAtUtc':NOW.isoformat()},
                                      'settings':{'leftBars':2,'rightBars':2},'lookback':['2026-07-01','2026-09-26']})
        provenance = {'calculationHashes':{'gann_timing.py':'a'*64},'theorySourceHashes':{'spec.md':'b'*64},'marketSnapshotSha256':'c'*64}
        a = c.normalize_candidate(raw,'BTCUSDT',NOW,date(2026,9,28),date(2026,10,27),provenance)
        self.assertIn('c'*64,a['draft']['note'])
        self.assertEqual(a['registrationProvenance']['knownAtUtc'],NOW.isoformat())
        self.assertEqual(a['registrationProvenance']['settings'],{'leftBars':2,'rightBars':2})
        provenance['marketSnapshotSha256']='d'*64
        b = c.normalize_candidate(raw,'BTCUSDT',NOW,date(2026,9,28),date(2026,10,27),provenance)
        self.assertEqual(a['id'],b['id'])
        self.assertNotEqual(a['draft'],b['draft'])
        self.assertEqual(normalize_body(b['draft']),b['draft'])

    def test_gann_duplicates_not_independent_same_family_confirmation(self):
        raw = [raw_candidate(evidence={'projection': {'anchorAId': 'a', 'anchorBId': b, 'multiplier': 1}}) for b in ('b','c')]
        raw.append(raw_candidate('ASTROLOGY','time',rule='ASTRO-MOON-PHASE', evidence={'event': {'id': 'phase'}}))
        candidates = [c.normalize_candidate(r,'BTCUSDT',NOW,date(2026,9,28),date(2026,10,27)) for r in raw]
        day = c.group_day({'date':'2026-10-05'}, candidates)
        self.assertFalse(day['sameFamilyAgreement'])
        self.assertTrue(day['crossFamilyTimingOverlap'])
        self.assertFalse(day['hasDirectionConflict'])
        self.assertEqual(day['upIds'], [])
        self.assertEqual(day['downIds'], [])

    def test_exact_month_transition_keeps_midnight_pillar_and_time(self):
        result = self.call(now=datetime(2024, 2, 3, 3, tzinfo=timezone.utc))
        lichun = next(d for d in result['days'] if d['date'] == '2024-02-04')
        self.assertTrue(lichun['hasMonthTransition'])
        transition = lichun['solarMonthTransitions'][0]
        self.assertEqual(transition['term'], '立春')
        self.assertEqual(lichun['pillars']['month'], transition['beforePillar'])
        self.assertEqual(transition['afterPillar'], '丙寅')
        self.assertNotIn('T00:00:00', transition['at'])

    def test_one_snapshot_read_and_no_market_methods_other_than_read(self):
        market = Market()
        before = deepcopy(market.snapshot)
        with patch('urllib.request.urlopen', side_effect=AssertionError('network forbidden')):
            result = self.call(market=market)
        self.assertEqual(market.calls, ['BTCUSDT'])
        self.assertEqual(market.snapshot, before)
        self.assertEqual(result['marketAsOfUtc'], NOW.isoformat())

    def test_wrong_asset_snapshot_rejected_and_calendar_can_degrade(self):
        result = self.call(market=Market('ETHUSDT'))
        self.assertFalse(result['marketAvailable'])
        self.assertIsNone(result['marketAsOfUtc'])
        self.assertTrue(any('snapshot_asset_mismatch' in warning for warning in result['warnings']))
        self.assertTrue(result['candidates'])  # BTC independent calendar is still possible
        with patch('chart_candidates.calculate_calendar', side_effect=RuntimeError('fixture unavailable')):
            degraded = self.call()
        self.assertTrue(all(not day['calendarAvailable'] for day in degraded['days']))
        self.assertFalse(any(item['ruleId'] == c.YIN_RULE_ID for item in degraded['candidates']))

    def test_missing_market_real_gann_degrades_without_cross_asset_or_network(self):
        # Restore the real Gann call for an empty snapshot; its provenance path is local.
        self.gann.stop()
        result = self.call('ETHUSDT')
        self.assertFalse(result['marketAvailable'])
        self.assertTrue(result['warnings'])
        self.assertEqual(result['candidates'], [])

    def test_stale_cache_rows_do_not_imply_gann_lookback_availability(self):
        market = Market('ETHUSDT')
        row = make_row(date(2020,1,1),'ETHUSDT',updated=NOW.isoformat())
        market.snapshot[1][row['open_ms']] = row
        result = self.call('ETHUSDT',market=market)
        self.assertTrue(result['marketAvailable'])
        self.assertFalse(result['gannAvailable'])
        self.assertEqual(result['gannDataQuality']['validDays'],0)
        self.assertEqual(result['gannDataQuality']['excludedDays'],90)
        self.assertTrue(any('没有连续5根' in warning for warning in result['warnings']))

    def test_no_high_low_or_asset_mismatched_candidate(self):
        for item in (raw_candidate(kind='high'), raw_candidate(symbol='ETHUSDT'), raw_candidate('GANN','up')):
            with self.assertRaises(ValueError): c.normalize_candidate(item,'BTCUSDT',NOW,date(2026,9,28),date(2026,10,27))
        with self.assertRaises(c.ChartCandidatesError): self.call(now=datetime(2099,12,15,tzinfo=timezone.utc))


if __name__ == '__main__': unittest.main()
