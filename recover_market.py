"""Add only verified same-exchange observations; preserve all unresolved gaps."""
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import urllib.parse
import zipfile

import legacy_market as market

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / 'outputs' / '计算接入_v0.4'
RAW = HERE / 'recovery_raw'
LOCAL = timezone(timedelta(hours=8))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def request(url, filename):
    path = RAW / filename
    meta = RAW / (filename+'.request.json')
    if path.exists() and meta.exists():
        item = json.loads(meta.read_text(encoding='utf-8'))
        body = path.read_bytes()
        if sha(body) != item['sha256']:
            raise ValueError('cached response hash changed')
        return item, body
    item = {'url': url, 'savedFile': filename, 'fetchedAtUtc': datetime.now(timezone.utc).isoformat(),
            'method': 'GET', 'requestTimestampUnit': 'milliseconds', 'credentialsUsed': False}
    try:
        req = urllib.request.Request(url, headers={'User-Agent':'Local-research/0.6'})
        with urllib.request.urlopen(req, timeout=25) as response:
            body = response.read()
            item.update(status='downloaded', httpStatus=response.status, bytes=len(body), sha256=sha(body))
        path.write_bytes(body)
        write(meta,item)
        return item, body
    except Exception as exc:
        item.update(status='unavailable', error=f'{type(exc).__name__}: {exc}')
        write(meta,item)
        return item, None


def normalize_api(row, filename, number, start, end):
    if not isinstance(row,list) or len(row) != 12:
        raise ValueError('unexpected API kline columns')
    opened, closed = row[0], row[6]
    if type(opened) is not int or type(closed) is not int or opened % 3600000:
        raise ValueError('API timestamp is not an exact millisecond hourly boundary')
    if not start <= opened <= end:
        raise ValueError('API returned bar outside requested window')
    if closed != opened + 3600000 - 1:
        raise ValueError('invalid hourly close timestamp')
    if opened/1000+3600 > datetime.now(timezone.utc).timestamp():
        raise ValueError('unclosed API hour')
    o,h,l,c = [Decimal(str(p)) for p in row[1:5]]
    if any(not p.is_finite() or p <= 0 for p in (o,h,l,c)) or not l <= min(o,c) <= max(o,c) <= h:
        raise ValueError('invalid API OHLC')
    stamp = opened//1000
    return {'openTimeUtcSeconds':stamp,'openTimeUtc':datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
            'closeTimeUtc':datetime.fromtimestamp(closed/1000,timezone.utc).isoformat(),
            'rawOpenTimestamp':opened,'rawCloseTimestamp':closed,'sourceTimestampUnit':'milliseconds',
            'open':str(o),'high':str(h),'low':str(l),'close':str(c),'source':filename,'sourceRow':number,
            'sourceType':'official_rest_klines'}


def fetch_day(day):
    start = int(datetime.combine(date.fromisoformat(day),time(),LOCAL).timestamp()*1000)
    end = start+86400000-1
    query = urllib.parse.urlencode({'symbol':'BTCUSDT','interval':'1h','startTime':start,'endTime':end,'limit':1000})
    url = 'https://data-api.binance.vision/api/v3/klines?'+query
    item, data = request(url, 'BTCUSDT-1h-API-'+day+'.json')
    item.update(localDate=day, requestedStartMs=start, requestedEndMs=end)
    good, invalid = [], []
    if data is not None:
        try:
            rows = json.loads(data)
            if not isinstance(rows,list): raise ValueError('API response is not a kline array')
            for n,row in enumerate(rows,1):
                try: good.append(normalize_api(row,item['savedFile'],n,start,end))
                except (ValueError,ArithmeticError) as exc:
                    invalid.append({'row':n,'rawOpen':row[0] if isinstance(row,list) and row else None,'error':str(exc)})
            item.update(returnedRows=len(rows), validRows=len(good), invalidRows=invalid)
        except (ValueError,TypeError) as exc:
            item.update(status='invalid_response',error=str(exc))
    return item, good


def audit_invalid_candidates(item, hours):
    """Rejected rows can still reveal revisions to previously accepted source data."""
    if not item.get('invalidRows'): return []
    raw_rows=json.loads((RAW/item['savedFile']).read_text(encoding='utf-8'))
    conflicts=[]
    for bad in item['invalidRows']:
        candidate=raw_rows[bad['row']-1]
        if not isinstance(candidate,list) or len(candidate)!=12 or type(candidate[0]) is not int: continue
        old=hours.get(candidate[0]//1000)
        if old is None: continue
        differences=[k for k,v in zip(('open','high','low','close'),candidate[1:5]) if Decimal(old[k])!=Decimal(str(v))]
        if old.get('rawCloseTimestamp') != candidate[6]: differences.append('rawCloseTimestamp')
        if differences:
            old.setdefault('errors',[]).append('official_source_revision_conflict')
            conflicts.append({'type':'rejected_api_candidate_vs_accepted_archive','openTimeUtc':old['openTimeUtc'],
                              'differentFields':differences,'existing':dict(old),'candidateRawRow':candidate,
                              'candidateSource':item['savedFile'],'candidateRejection':bad['error'],
                              'resolution':'preserve original value; reject conflicting day from complete-day study'})
    return conflicts


def main():
    RAW.mkdir(parents=True,exist_ok=True)
    old_audit=json.loads((BASE/'检查与来源/market_validation.json').read_text(encoding='utf-8'))
    old_daily_path=BASE/'研究数据/daily_input.json'
    old_hourly_path=BASE/'研究数据/hourly_normalized.jsonl'
    old_doc=json.loads(old_daily_path.read_text(encoding='utf-8'))
    hours={}
    for line in old_hourly_path.read_text(encoding='utf-8').splitlines():
        row=json.loads(line)
        if row['openTimeUtcSeconds'] in hours: raise ValueError('duplicate old normalized hour')
        hours[row['openTimeUtcSeconds']]=row
    added,conflicts,requests=[],[],[]

    def merge(rows):
        for row in rows:
            t=row['openTimeUtcSeconds']
            if t not in hours:
                hours[t]=row
                added.append({'openTimeUtc':row['openTimeUtc'],'source':row['source']})
            elif any(Decimal(hours[t][k]) != Decimal(row[k]) for k in ('open','high','low','close')):
                conflicts.append({'openTimeUtc':row['openTimeUtc'],'existing':hours[t],'candidate':row})
                hours[t].setdefault('errors',[]).append('official_source_conflict')

    filename='BTCUSDT-1h-2026-09-26.zip'
    url='https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1h/'+filename
    check,checkbody=request(url+'.CHECKSUM',filename+'.CHECKSUM')
    requests.append(check)
    if checkbody is not None:
        rec,body=request(url,filename)
        requests.append(rec)
        if body is not None:
            expected=checkbody.decode().split()[0].lower()
            if len(expected)!=64 or sha(body)!=expected: raise ValueError('archive CHECKSUM mismatch')
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                if z.testzip() is not None: raise ValueError('archive ZIP integrity failure')
                names=[n for n in z.namelist() if n.endswith('.csv')]
                if len(names)!=1: raise ValueError('unexpected archive contents')
                rows=[market.normalize_hour(row,filename,n) for n,row in enumerate(csv.reader(io.StringIO(z.read(names[0]).decode())),1)]
            rec.update(archiveIntegrity='SHA256+ZIP_CRC_passed')
            merge(rows)
    with ThreadPoolExecutor(max_workers=3) as pool:
        for item,rows in pool.map(fetch_day,[x['date'] for x in old_audit['missingDays']]):
            requests.append(item)
            merge(rows)
            conflicts.extend(audit_invalid_candidates(item,hours))
    days=[market.aggregate_day(date.fromisoformat(row['date']),hours) for row in old_doc['dailyRows']]
    missing=[{'date':d['date'],'errors':d['sourceErrors'],'hourCount':d['hourCount']} for d in days if d['sourceErrors']]
    meta=dict(old_doc['metadata'])
    meta.update(calendarValidated=True,registered=False,hourlyAggregationValidated=not missing and not conflicts,
                datasetVersion='0.6.0',sourcePolicy='v0.4 verified archives plus verified same-exchange observations; no price filling',
                recoverySources=[{'url':r['url'],'sha256':r.get('sha256')} for r in requests if r['status']=='downloaded'])
    write(HERE/'daily_input.json',{'metadata':meta,'dailyRows':days})
    report={'version':'0.6.0','checkedAtUtc':datetime.now(timezone.utc).isoformat(),
            'oldDailySha256':sha(old_daily_path.read_bytes()),'oldHourlySha256':sha(old_hourly_path.read_bytes()),
            'expectedDays':len(days),'completeDays':len(days)-len(missing),'missingDays':missing,
            'addedHours':added,'sourceConflicts':conflicts,'requests':requests,
            'priorIncompleteDays':len(old_audit['missingDays']),'remainingIncompleteDays':len(missing),
            'priceImputationUsed':False,'exchangeChanged':False,'originalFilesModified':False}
    write(HERE/'market_recovery.json',report)
    print(json.dumps({k:report[k] for k in ('expectedDays','completeDays','priorIncompleteDays','remainingIncompleteDays')},ensure_ascii=False))
    print(json.dumps({'addedHours':len(added),'conflicts':len(conflicts),'requests':len(requests),'unavailable':sum(r['status']!='downloaded' for r in requests)},ensure_ascii=False))


if __name__=='__main__': main()
