"""Future horizon extensions preserve immutable observation time and identities."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

import chart_candidates as c
from test_chart_candidates import Market, NOW, raw_candidate


class HorizonTests(unittest.TestCase):
    def test_strict_horizon_parameter(self):
        self.assertEqual(c.parse_chart_candidate_options('symbol=BTCUSDT'), {'symbol':'BTCUSDT','days':30})
        for count in c.HORIZONS:
            self.assertEqual(c.parse_chart_candidate_options('days='+str(count)+'&symbol=ETHUSDT')['days'],count)
        for query in ('symbol=BTCUSDT&days=0','symbol=BTCUSDT&days=366','symbol=BTCUSDT&days=090',
                      'symbol=BTCUSDT&days=90&days=30','symbol=BTCUSDT&days=',
                      'symbol=BTCUSDT&start=2027-01-01','days=90','symbol=BTCUSDT&days=90&unknown=1'):
            with self.subTest(query=query), self.assertRaises(ValueError): c.parse_chart_candidate_options(query)
        for days in (True,30.0,'30',31,0,366):
            with self.subTest(days=days), self.assertRaises(ValueError): c.chart_candidates(Market(),'BTCUSDT',days=days)

    def test_full_year_inclusive_range_chunks_single_snapshot_no_future_prices(self):
        market = Market('ETHUSDT')
        calls=[]
        def preview(snapshot,symbol,start,end,clock):
            calls.append((snapshot,symbol,start,end,clock()))
            return {'candidates':[],'warnings':[]}
        with patch('chart_candidates.preview_candidates',side_effect=preview):
            result=c.chart_candidates(market,'ETHUSDT',clock=lambda:NOW,days=365)
        self.assertEqual(market.calls,['ETHUSDT'])
        self.assertEqual(len(calls),13)
        self.assertEqual(len({id(x[0]) for x in calls}),1)
        self.assertEqual({x[4] for x in calls},{NOW})
        self.assertEqual(result['range']['startDate'],'2026-09-28')
        self.assertEqual(result['range']['endDate'],'2027-09-27')
        self.assertEqual(len(result['days']),365)
        self.assertEqual(len({d['date'] for d in result['days']}),365)
        self.assertTrue(all('close' not in d and 'open' not in d for d in result['days']))
        for previous, following in zip(calls,calls[1:]):
            self.assertEqual(datetime.fromisoformat(previous[3])+timedelta(days=1),datetime.fromisoformat(following[2]))
        self.assertFalse(result['registeredPrediction'])
        self.assertFalse(result['marketValidated'])

    def test_longer_window_retains_same_candidate_identity_body_and_deadline(self):
        def preview(snapshot,symbol,start,end,clock):
            return {'candidates':[raw_candidate(day='2026-10-05')] if start<='2026-10-05'<=end else [],'warnings':[]}
        with patch('chart_candidates.preview_candidates',side_effect=preview):
            short=c.chart_candidates(Market(),'BTCUSDT',clock=lambda:NOW,days=30)
            long=c.chart_candidates(Market(),'BTCUSDT',clock=lambda:NOW,days=90)
        matching={x['id']:x for x in long['candidates']}
        for item in short['candidates']:
            self.assertEqual(item['draft'],matching[item['id']]['draft'])
            self.assertEqual(item['registrationDeadlineAt'],matching[item['id']]['registrationDeadlineAt'])

    def test_failed_chunk_explicit_preserves_calendar_and_other_chunks(self):
        def preview(snapshot,symbol,start,end,clock):
            if start=='2026-10-28': raise RuntimeError('fixture')
            return {'candidates':[raw_candidate(symbol='ETHUSDT',day=start)],'warnings':[]}
        with patch('chart_candidates.preview_candidates',side_effect=preview):
            result=c.chart_candidates(Market('ETHUSDT'),'ETHUSDT',clock=lambda:NOW,days=90)
        self.assertEqual(len(result['days']),90)
        self.assertEqual([x['status'] for x in result['generationChunks']],['generated','unavailable','generated'])
        self.assertEqual(len(result['candidates']),2)
        self.assertTrue(any('2026-10-28' in warning for warning in result['warnings']))

    def test_leap_day_and_year_boundary(self):
        with patch('chart_candidates.preview_candidates',return_value={'candidates':[],'warnings':[]}):
            result=c.chart_candidates(Market('ETHUSDT'),'ETHUSDT',clock=lambda:datetime(2027,12,31,16,tzinfo=timezone.utc),days=90)
        self.assertEqual(result['range']['startDate'],'2028-01-02')
        self.assertEqual(result['range']['endDate'],'2028-03-31')
        self.assertIn('2028-02-29',{d['date'] for d in result['days']})


if __name__=='__main__': unittest.main()
