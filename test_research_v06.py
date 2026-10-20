import math
import random
import unittest
import json
import tempfile
from pathlib import Path
from datetime import date,timedelta

from masked_study import masked_bootstrap,validate_grid,make_grid
from recover_market import normalize_api
import recover_market
import legacy_scorer


def fixture(n=61):
    return [{'date':str(date(2020,1,1)+timedelta(days=i)),'group':int(i%10>=6),'mask':int(i not in (4,10,31)),
             'return':None if i in (4,10,31) else (i%7-2)/100,'errors':[]} for i in range(n)]


class GridTests(unittest.TestCase):
    def test_mask_rejects_imputation(self):
        rows=fixture();rows[4]['return']=0
        with self.assertRaisesRegex(ValueError,'imputed'): masked_bootstrap(rows,20)

    def test_compressed_grid_rejected(self):
        with self.assertRaisesRegex(ValueError,'nonconsecutive'): masked_bootstrap([r for r in fixture() if r['mask']],20)

    def test_nonfinite_rejected(self):
        rows=fixture();rows[0]['return']=math.nan
        with self.assertRaisesRegex(ValueError,'finite'): masked_bootstrap(rows,20)

    def test_prefix_sampler_matches_direct_calendar_block_oracle(self):
        rows=fixture();n=len(rows);rng=random.Random(20260927);values=[];counts=[]
        for _ in range(10000):
            sampled=[]
            while len(sampled)<n:
                start=rng.randrange(n-30+1);sampled.extend(rows[start:start+30])
            observed=[x for x in sampled[:n] if x['mask']]
            g0=[x['return'] for x in observed if x['group']==0]
            g1=[x['return'] for x in observed if x['group']==1]
            if g0 and g1:
                values.append(sum(g1)/len(g1)-sum(g0)/len(g0));counts.append(len(observed))
        values.sort();actual=masked_bootstrap(rows)
        self.assertEqual(actual['validObservedDaysRange'],[min(counts),max(counts)])
        self.assertEqual(actual['finalBlockWidth'],1)
        for got,q in zip(actual['ci95'],(.025,.975)):
            self.assertAlmostEqual(got,legacy_scorer.quantile_linear(values,q),places=12)

    def test_complete_grid_matches_old_algorithm(self):
        rows=fixture()
        for i,r in enumerate(rows): r.update(mask=1,**{'return':(i%7-2)/100})
        new=masked_bootstrap(rows);old=legacy_scorer.moving_block_bootstrap(rows)
        self.assertEqual(new['randomDrawCount'],old['randomDrawCount'])
        for x,y in zip(new['ci95'],old['ci95']): self.assertAlmostEqual(x,y,places=12)

    def test_small_test_run_not_reported_as_full_ci(self):
        result=masked_bootstrap(fixture(),20)
        self.assertFalse(result['available']);self.assertIsNone(result['ci95'])

    def test_all_masked_no_fake_interval(self):
        rows=fixture(30)
        for r in rows: r.update(mask=0,**{'return':None})
        result=masked_bootstrap(rows)
        self.assertEqual(result['replicatesValid'],0);self.assertIsNone(result['ci95'])

    def test_duplicate_input_dates_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            make_grid({'dailyRows':[{'date':'2020-01-01'},{'date':'2020-01-01'}]},'2020-01-01','2020-01-01')

    def test_valid_but_wrong_calendar_label_rejected(self):
        with self.assertRaisesRegex(ValueError,'independent calendar'):
            make_grid({'dailyRows':[{'date':'2020-01-01','dayGanzhi':'甲子'}]},'2020-01-01','2020-01-01')


class RecoveryTests(unittest.TestCase):
    def test_rejected_api_row_still_exposes_archive_conflict(self):
        oldroot=recover_market.RAW
        try:
            with tempfile.TemporaryDirectory() as folder:
                recover_market.RAW=Path(folder)
                t=1735689600000
                row=[t,'10','12','9','11','2',t+123000,'0',3,'0','0','0']
                (Path(folder)/'test.json').write_text(json.dumps([row]),encoding='utf-8')
                old={'open':'10','high':'11','low':'9','close':'10','rawCloseTimestamp':t+3599999,'openTimeUtc':'2025-01-01T00:00:00Z'}
                hits=recover_market.audit_invalid_candidates({'savedFile':'test.json','invalidRows':[{'row':1,'error':'partial'}]},{t//1000:old})
                self.assertEqual(len(hits),1)
                self.assertIn('official_source_revision_conflict',old['errors'])
                self.assertEqual(old['close'],'10')
        finally:recover_market.RAW=oldroot

    def test_api_milliseconds_after_archive_microsecond_transition(self):
        t=1735689600000;row=[t,'10','12','9','11','2',t+3599999,'0',3,'0','0','0']
        result=normalize_api(row,'test',1,t,t+3600000-1)
        self.assertEqual(result['openTimeUtcSeconds'],1735689600)
        self.assertEqual(result['sourceTimestampUnit'],'milliseconds')

    def test_partial_hour_rejected(self):
        t=1735689600000;row=[t,'10','12','9','11','2',t+123000,'0',3,'0','0','0']
        with self.assertRaisesRegex(ValueError,'close timestamp'):normalize_api(row,'test',1,t,t+3600000-1)

    def test_wrong_window_rejected(self):
        t=1735689600000;row=[t,'10','12','9','11','2',t+3599999,'0',3,'0','0','0']
        with self.assertRaisesRegex(ValueError,'outside'):normalize_api(row,'test',1,t+3600000,t+7200000-1)


if __name__=='__main__':unittest.main()
