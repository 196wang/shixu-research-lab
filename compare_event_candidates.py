"""Calculate event hypotheses without assigning an asset birth chart."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
from calendar_adapter import calculate_calendar
from indian_adapter import calculate_indian

HERE=Path(__file__).resolve().parent


def main():
    catalog=json.loads((HERE/'birth_candidates.json').read_text(encoding='utf-8'))
    profiles=json.loads((HERE/'profiles.json').read_text(encoding='utf-8'))['profiles']
    rows=[]
    for candidate in catalog['candidates']:
        if not candidate.get('eventAtUtc') or not candidate['geocentricEventChartAllowed']:continue
        instant=candidate['eventAtUtc']
        cal=calculate_calendar(instant)
        sky=calculate_indian(instant,scenario=False)
        matching=[]
        comparisons=[]
        for p in profiles:
            if p.get('asset')!=candidate['asset']:continue
            differences=[k for k in ('year','month','day','hour') if p['pillars'][k]!=cal['pillars'][k]]
            comparisons.append({'profileId':p['id'],'givenPillars':p['pillars'],'differentPillars':differences})
            if not differences:matching.append(p['id'])
        rows.append({'candidateId':candidate['id'],'label':candidate['label'],'asset':candidate['asset'],
                     'eventAtUtc':instant,'atResearchTimezone':cal['atResearchTimezone'],
                     'recordPrecision':candidate['recordPrecision'],'sourceRefs':candidate['sourceRefs'],
                     'calendar':cal,'indian':sky,'existingProfileComparisons':comparisons,
                     'sameFourPillarsUnderResearchConvention':matching,
                     'candidateSelectedAsDefault':False,'assetNatalVerified':False,'housesEnabled':False,
                     'note':'计算相符也不是出生依据核定；不替换原四柱，不把UTC+8当出生地。'})
    result={'generatedAtUtc':datetime.now(timezone.utc).isoformat(),'candidatesSha256':hashlib.sha256((HERE/'birth_candidates.json').read_bytes()).hexdigest(),
            'profilesSha256':hashlib.sha256((HERE/'profiles.json').read_bytes()).hexdigest(),'computations':rows,
            'calculableEvents':len(rows),'dateOnlyEventsSkipped':sum(not x.get('eventAtUtc') for x in catalog['candidates']),
            'defaultCandidate':None,'housesEnabled':False,'natalDasaEnabled':False}
    (HERE/'event_comparisons.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([{'id':r['candidateId'],'pillars':r['calendar']['pillars'],'matches':r['sameFourPillarsUnderResearchConvention']} for r in rows],ensure_ascii=False))


if __name__=='__main__':main()
