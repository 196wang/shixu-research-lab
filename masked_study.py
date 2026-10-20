"""Exploratory calendar-grid masked MBB; never a trading strategy or prospective test."""
import hashlib
import json
import math
from pathlib import Path
import platform
import random
from collections import Counter
from datetime import date, timedelta, datetime, timezone

import legacy_scorer as base
from calendar_validation import independent_day_label

HERE=Path(__file__).resolve().parent
VERSION='0.6.0'


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def make_grid(document,start,end):
    index={}
    for row in document['dailyRows']:
        day=row['date']
        base.parse_date(day)
        if day in index: raise ValueError('duplicate input date')
        index[day]=row
    grid=[]
    for d in base.date_range(date.fromisoformat(start),date.fromisoformat(end)):
        label=d.isoformat()
        if label not in index: raise ValueError('calendar grid row absent; do not compress gaps')
        if index[label].get('dayGanzhi') != independent_day_label(label)[1]:
            raise ValueError('day label disagrees with independent calendar formula')
        checked=base.validate_row(index[label],label)
        if checked['group'] is None: raise ValueError('unknown calendar group cannot be masked')
        grid.append({'date':label,'group':checked['group'],'mask':int(checked['valid']),
                     'return':checked['return'],'errors':checked['errors'],
                     'dayGanzhi':index[label]['dayGanzhi'],'sourceRefs':index[label].get('sourceArchives',[])})
    return grid


def validate_grid(grid):
    for i,row in enumerate(grid):
        d=base.parse_date(row['date'])
        if i and d != base.parse_date(grid[i-1]['date'])+timedelta(days=1):
            raise ValueError('nonconsecutive calendar grid')
        if type(row['group']) is not int or row['group'] not in (0,1): raise ValueError('invalid group')
        if type(row['mask']) is not int or row['mask'] not in (0,1): raise ValueError('invalid mask')
        v=row['return']
        if row['mask'] and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)):
            raise ValueError('observed row must have finite return')
        if not row['mask'] and v is not None: raise ValueError('masked row must not have an imputed return')


def masked_bootstrap(grid,replicates=10000,seed=20260927,block=30):
    validate_grid(grid)
    if type(replicates) is not int or replicates<=0: raise ValueError('invalid replicate count')
    if type(block) is not int or block<=0: raise ValueError('invalid block length')
    n=len(grid)
    if n<block: raise ValueError('not enough calendar days')
    prefixes=[[0.0],[0.0],[0],[0]]
    for row in grid:
        g,m,v=row['group'],row['mask'],row['return']
        additions=[v if m and g==0 else 0.0,v if m and g==1 else 0.0,int(m and g==0),int(m and g==1)]
        for p,a in zip(prefixes,additions): p.append(p[-1]+a)
    rng=random.Random(seed)
    blocks=math.ceil(n/block)
    last=n-block*(blocks-1)
    deltas=[]
    observations=[];group_counts=[[],[]]
    for _ in range(replicates):
        total=[0.0,0.0,0,0]
        for b in range(blocks):
            start=rng.randrange(n-block+1)
            stop=start+(last if b==blocks-1 else block)
            for j,p in enumerate(prefixes): total[j]+=p[stop]-p[start]
        if total[2] and total[3]:
            deltas.append(total[1]/total[3]-total[0]/total[2])
            observations.append(total[2]+total[3])
            group_counts[0].append(total[2]);group_counts[1].append(total[3])
    deltas.sort()
    standard=(replicates==10000 and block==30 and seed==20260927)
    reasons=[]
    if not standard:reasons.append('nonstandard_test_configuration')
    if len(deltas)<9900:reasons.append('fewer_than_9900_valid_replicates')
    available=not reasons
    ci=[base.quantile_linear(deltas,p) for p in (.025,.975)] if available else None
    return {'method':'moving_block_calendar_grid_paired_G_mask_return','blockDays':block,'seed':seed,
            'replicatesRequested':replicates,'replicatesValid':len(deltas),'replicatesInvalid':replicates-len(deltas),
            'calendarSlotsPerReplicate':n,'blocksPerReplicate':blocks,'finalBlockWidth':last,
            'randomDrawCount':blocks*replicates,'available':available,
            'ci95':ci,'ci95Bp':[x*10000 for x in ci] if ci else None,
            'validObservedDaysRange':[min(observations),max(observations)] if observations else None,
            'sampledGroupCountRanges':{str(i):[min(c),max(c)] if c else None for i,c in enumerate(group_counts)},
            'testOnly':not standard,'unavailableReasons':reasons,'quantileDefinition':'linear interpolation at (M-1)*p'}


def summarize(grid,bootstrap=True):
    valid=[r for r in grid if r['mask']]
    stats=base.group_stats(valid)
    missing={}
    for g in (0,1):
        rows=[r for r in grid if r['group']==g]
        absent=sum(not r['mask'] for r in rows)
        missing[str(g)]={'expected':len(rows),'observed':len(rows)-absent,'missing':absent,
                         'missingRate':absent/len(rows) if rows else None}
    runs=[]
    for r in grid:
        if r['mask']: continue
        if runs and base.parse_date(r['date'])==base.parse_date(runs[-1]['end'])+timedelta(days=1):
            runs[-1].update(end=r['date'],days=runs[-1]['days']+1)
        else:runs.append({'start':r['date'],'end':r['date'],'days':1})
    return {**stats,'expectedDays':len(grid),'observedDays':len(valid),'missingDays':len(grid)-len(valid),
            'missingByGroup':missing,'coverage':len(valid)/len(grid) if grid else 0,
            'consecutiveMissingRuns':runs,
            'missingByReason':dict(Counter(reason for r in grid if not r['mask'] for reason in r.get('errors',[]))),
            'missingRateGroupDifference':missing['1']['missingRate']-missing['0']['missingRate'] if all(missing[str(g)]['expected'] for g in (0,1)) else None,
            'bootstrap':masked_bootstrap(grid) if bootstrap else None}


def run(document,protocol):
    meta=document['metadata']
    for k,v in {'timezone':'Asia/Shanghai','symbol':'BTCUSDT','marketType':'spot','exchange':'Binance','interval':'1h','researchDataType':'real_market'}.items():
        if meta.get(k)!=v: raise ValueError('unexpected market metadata '+k)
    if meta.get('calendarValidated') is not True: raise ValueError('calendar not validated')
    for k,v in {'blockDays':30,'replicates':10000,'seed':20260927}.items():
        if protocol['bootstrap'][k]!=v:raise ValueError('unapproved bootstrap configuration '+k)
    periods=[]
    for p in protocol['periods']:
        grid=make_grid(document,p['start'],p['end'])
        s=summarize(grid,False)
        quality=protocol['qualityAndAvailability']
        gate_errors=[]
        if s['coverage']<quality['minimumOverallCoverage']:gate_errors.append('coverage_below_frozen_gate')
        if s['observedDays']<quality['minimumCompleteDaysPerPeriod']:gate_errors.append('insufficient_observed_days')
        if any(s['groups'][str(g)]['n']<quality['minimumCompleteDaysPerGroup'] for g in (0,1)):gate_errors.append('insufficient_group_size')
        s['bootstrap']=masked_bootstrap(grid) if not gate_errors else {'available':False,'ci95':None,'ci95Bp':None,'replicatesRequested':10000,'replicatesValid':0,'unavailableReasons':gate_errors}
        annual=[]
        for year in sorted(set(r['date'][:4] for r in grid)):
            rows=[r for r in grid if r['date'][:4]==year]
            annual.append({'year':year,'partialYear':rows[0]['date']!=year+'-01-01' or rows[-1]['date']!=year+'-12-31',**summarize(rows,False)})
        leave=[]
        for year in sorted(set(r['date'][:4] for r in grid)):
            # Descriptive only; never feed concatenated non-adjacent years to MBB.
            rows=[r for r in grid if r['date'][:4]!=year and r['mask']]
            leave.append({'excludedYear':year,**base.group_stats(rows)})
        no_gaps=not s['missingDays']
        strict=base.moving_block_bootstrap([{'group':r['group'],'return':r['return']} for r in grid]) if no_gaps else None
        extremes=sorted([r for r in grid if r['mask']],key=lambda r:r['return'])
        periods.append({**p,**s,'yearly':annual,'leaveOneYearOutDescriptive':leave,'gateErrors':gate_errors,
                        'extremeDays':{'lowest':extremes[:5],'highest':list(reversed(extremes[-5:])),'removedFromMainEstimate':False},
                        'gapFreeOnly':no_gaps,'originalAlgorithmCrosscheck':strict,
                        'status':'data_validation_failed' if gate_errors else ('exploratory_diagnostic' if s['bootstrap']['available'] else 'uncertainty_unavailable'),'marketValidated':False,
                        'screeningOnly':{'effectExceeds10Bp':s['delta'] is not None and s['delta']>=.001,
                                         'intervalLowerAboveZero':s['bootstrap']['available'] and s['bootstrap']['ci95'][0]>0}})
    return {'version':VERSION,'hypothesisId':'H-BZ-DAY-001','hypothesisVersion':'0.2.0',
            'runAtUtc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
            'prospective':False,'marketValidated':False,'tradingBacktest':False,
            'priorKnowledge':protocol['priorKnowledge'],
            'periods':periods,'limits':protocol['limitations']}


def main():
    protocol=json.loads((HERE/'study_protocol_frozen.json').read_text(encoding='utf-8'))
    registration=json.loads((HERE/'study_registration.json').read_text(encoding='utf-8'))
    required={'daily_input.json','study_protocol_frozen.json','masked_study.py','legacy_scorer.py','recover_market.py',
              'legacy_market.py','calendar_adapter.py','calendar_validation.py','calendar_reference.json',
              'market_recovery.json','market_gap_audit.json','calendar_validation_evidence.json','unit_tests.json','study_grid.json'}
    if not required.issubset(registration['inputHashes']):raise ValueError('required registration evidence absent')
    for name,expected in registration['inputHashes'].items():
        if digest(HERE/name)!=expected: raise ValueError('registered input changed: '+name)
    document=json.loads((HERE/'daily_input.json').read_text(encoding='utf-8'))
    recovery=json.loads((HERE/'market_recovery.json').read_text(encoding='utf-8'))
    calendar=json.loads((HERE/'calendar_validation_evidence.json').read_text(encoding='utf-8'))
    tests=json.loads((HERE/'unit_tests.json').read_text(encoding='utf-8'))
    if calendar['status']!='passed' or not tests['passed']:raise ValueError('validation evidence not passed')
    grids=[make_grid(document,p['start'],p['end']) for p in protocol['periods']]
    if grids!=json.loads((HERE/'study_grid.json').read_text(encoding='utf-8')):raise ValueError('feature and mask snapshot changed')
    if sum(not r['mask'] for g in grids for r in g)!=recovery['remainingIncompleteDays']:raise ValueError('mask and recovery counts disagree')
    for r in recovery['requests']:
        if r['status']=='downloaded' and digest(HERE/'recovery_raw'/r['savedFile'])!=r['sha256']:raise ValueError('recovery raw response changed')
    result=run(document,protocol)
    result['registrationSha256']=digest(HERE/'study_registration.json')
    (HERE/'study_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps([{'period':[p['start'],p['end']],'n':p['observedDays'],'missing':p['missingDays'],'deltaBp':p['deltaBp'],
                      'ci95Bp':p['bootstrap']['ci95Bp'],'validReplicates':p['bootstrap']['replicatesValid']} for p in result['periods']],ensure_ascii=False))


if __name__=='__main__': main()
