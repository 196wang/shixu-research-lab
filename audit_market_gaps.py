"""Read-only comparison of frozen v0.4 archives and v0.6 recovery captures.

Writes only market_gap_audit.json/md. No network or price imputation.
Run from the workspace root, or pass that root as the first argument.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from collections import Counter
import csv, hashlib, io, json, sys, zipfile

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
OUT = ROOT / 'work/research_v06'
OLD = ROOT / 'outputs/计算接入_v0.4'
LOCAL = timezone(timedelta(hours=8))
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def iso(t): return datetime.fromtimestamp(t, timezone.utc).isoformat()
def row_info(row):
    unit = 1000000 if int(row[0]) >= 100000000000000 else 1000
    o, c = int(row[0]), int(row[6])
    return {'openUtc': iso(o // unit), 'openUtcSeconds': o // unit,
            'timestampUnit': 'microseconds' if unit == 1000000 else 'milliseconds',
            'closeUtc': datetime.fromtimestamp(c / unit, timezone.utc).isoformat(),
            'fullHourTimestamp': c == o + 3600 * unit - 1,
            'signedDurationSeconds': str(Decimal(c - o + 1) / unit),
            'OHLC': [str(x) for x in row[1:5]], 'volume': str(row[5]),
            'numberOfTrades': int(row[8])}

old = read(OLD / '检查与来源/market_validation.json')
recovery_path = OUT / 'market_recovery.json'
recovery = read(recovery_path)
manifest = read(OLD / '检查与来源/market_downloads.json')
hours = {}
for f in manifest['files']:
    if f['status'] != 'verified': continue
    path = OLD / '研究数据/原始小时档案' / f['filename']
    assert digest(path) == f['sha256'], 'frozen archive hash mismatch'
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        names = [n for n in z.namelist() if n.endswith('.csv')]
        assert len(names) == 1
        for n, row in enumerate(csv.reader(io.StringIO(z.read(names[0]).decode())), 1):
            entry = row_info(row)
            entry.update(archive=f['filename'], archiveRow=n)
            assert entry['openUtcSeconds'] not in hours
            hours[entry['openUtcSeconds']] = entry

sources = [
 {'id':'DATA-FILES', 'url':'https://github.com/binance/binance-public-data/blob/master/README.md', 'verified':'official_body', 'summary':'官方档案分日/月，日档通常次日发布；2025年起现货档案采用微秒。每个ZIP有CHECKSUM；历史档案可更正。'},
 {'id':'DATA-API', 'url':'https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market', 'verified':'official_body', 'summary':'GET /api/v3/klines，open time为唯一键，limit最大1000；startTime/endTime始终解释为UTC；未传timeZone时按UTC。'},
 {'id':'DATA-ONLY', 'url':'https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md', 'verified':'official_body', 'summary':'data-api.binance.vision支持公开klines，不要求API key。'},
 {'id':'M-20200219', 'url':'https://www.binance.com/en/support/announcement/detail/360039984971', 'verified':'official_body', 'summary':'2020-02-19临时维护，公告称17:30 UTC恢复交易。', 'utcDay':'2020-02-19'},
 {'id':'M-20200304', 'url':'https://www.binance.com/en/support/announcement/detail/360040487771', 'verified':'official_body', 'summary':'2020-03-04临时维护，公告称11:30 UTC恢复交易。', 'utcDay':'2020-03-04'},
 {'id':'M-20200628', 'url':'https://www.binance.com/en/support/announcement/detail/a9d34695cd9345c7a648a882fcd3bcc0', 'verified':'official_body', 'summary':'预告2020-06-28 02:00 UTC起升级约2小时，之后有30分钟交易恢复前窗口；预计时长不等于实际恢复时间。', 'utcDay':'2020-06-28'},
 {'id':'M-20201221', 'url':'https://www.binance.com/en-ZA/support/announcement/detail/b026ae47a3ab4335990d1c2f92aaeb29', 'verified':'official_search_content', 'summary':'官方索引可读公告：2020-12-21 18:00 UTC恢复交易。英文通用URL本轮重定向至公告目录。', 'utcDay':'2020-12-21'},
 {'id':'M-20210211', 'url':'https://www.binance.com/en/support/announcement/detail/aad7639a0ed9424bad585b508a61a433', 'verified':'official_search_title_only', 'summary':'官方索引显示当日临时维护完成公告；正文重定向，无法核对准确起止时间。', 'utcDay':'2021-02-11'},
 {'id':'M-20210420', 'url':'https://www.binance.com/en/support/announcement/detail/1ff4855808344ad49265ea14a03b32bb', 'verified':'official_search_content', 'summary':'官方索引可读公告：2021-04-20 04:30 UTC恢复交易；正文重定向，证据级别低于本轮直接读取正文。', 'utcDay':'2021-04-20'},
 {'id':'M-20210813', 'url':'https://www.binance.com/en/support/announcement/detail/92a9a5bc0129427f8e9928c9b7b09836', 'verified':'official_body', 'summary':'预告2021-08-13 02:00 UTC起升级约4小时，暂停现货，之后有30分钟交易恢复前窗口。该公告不解释01:00小时closeTime少999ms的原因。', 'utcDay':'2021-08-13'},
 {'id':'M-20230324', 'url':'https://www.binance.com/en/blog/from-our-ceo/6789340645608890113', 'verified':'official_body', 'summary':'官方事后报告：追踪止损订单问题导致现货交易暂停，2023-03-24 14:00 UTC恢复。', 'utcDay':'2023-03-24'},
]
requests = {x['localDate']: x for x in recovery['requests'] if 'localDate' in x}
added = {x['openTimeUtc'] for x in recovery['addedHours']}
days, mismatches = [], []
counts = Counter()
for day in old['missingDays']:
    d = day['date']
    start = int(datetime.fromisoformat(d + 'T00:00:00+08:00').timestamp())
    req = requests[d]
    raw_path = OUT / 'recovery_raw' / req['savedFile']
    assert digest(raw_path) == req['sha256'], 'API capture hash mismatch'
    api = {int(r[0]) // 1000: row_info(r) for r in read(raw_path)}
    for t, a in api.items():
        b = hours.get(t)
        if b:
            keys = ['closeUtc','OHLC','volume','numberOfTrades']
            diff = [k for k in keys if a[k] != b[k]]
            if diff:
                precision_only = diff == ['closeUtc'] and a['fullHourTimestamp'] and b['fullHourTimestamp']
                mismatches.append({'localDate':d, 'openUtc':a['openUtc'], 'differentFields':diff,
                                   'differenceClass':'timestamp_precision_only' if precision_only else 'substantive_source_difference',
                                   'archive':b, 'api':a, 'apiSource':req['savedFile'],
                                   'disposition':'retain_both_do_not_override'})
    holes = []
    for t in range(start, start + 86400, 3600):
        b = hours.get(t)
        if b and b['fullHourTimestamp']: continue
        state = 'absent_in_archive' if b is None else 'invalid_archive_close_timestamp'
        counts[state] += 1
        h = {'openUtc':iso(t), 'openLocal':datetime.fromtimestamp(t,LOCAL).isoformat(),
             'archiveState':state, 'archiveRow':b, 'apiRow':api.get(t),
             'recovered':iso(t) in added}
        holes.append(h)
    utc_dates = sorted({x['openUtc'][:10] for x in holes})
    candidates = []
    for ud in utc_dates:
        url=f'https://data.binance.vision/data/spot/daily/klines/BTCUSDT/1h/BTCUSDT-1h-{ud}.zip'
        candidates.append({'utcDate':ud, 'url':url, 'checksumUrl':url+'.CHECKSUM',
                           'retrievalStatus':'not_requested_in_this_audit',
                           'purpose':'optional_daily_vs_monthly_revision_check_not_guaranteed_repair'})
    evidence=[s['id'] for s in sources if s.get('utcDay') in utc_dates]
    days.append({'date':d, 'timezone':'Asia/Shanghai', 'unqualifiedHoursBefore':len(holes),
                 'recoveredHours':sum(x['recovered'] for x in holes), 'hours':holes,
                 'apiRequest':req, 'dailyArchiveCandidates':candidates,
                 'maintenanceEvidenceIds':evidence,
                 'causeStatus':'maintenance_related_evidence_does_not_explain_every_row' if evidence else 'not_established',
                 'repairStatus':'recovered_by_official_api' if all(x['recovered'] for x in holes) else 'official_api_did_not_repair'})

result={'version':'0.6.0','auditedAtUtc':datetime.now(timezone.utc).isoformat(),
 'frozenArchiveCountVerified':sum(f['status']=='verified' for f in manifest['files']),
 'sourceRecoveryReport':str(recovery_path.relative_to(ROOT)), 'sourceRecoveryReportSha256':digest(recovery_path),
 'priorIncompleteDays':19,'remainingIncompleteDays':recovery['remainingIncompleteDays'],
 'priorUnqualifiedHours':sum(counts.values()),'priorHourCategories':dict(counts),
 'historicalUnqualifiedHours':sum(len(x['hours']) for x in days if x['date']<'2026'),
 'addedHours':len(added),'archiveApiRowDifferences':mismatches,
 'substantiveSourceDifferenceRows':sum(x['differenceClass']=='substantive_source_difference' for x in mismatches),
 'timestampPrecisionOnlyRows':sum(x['differenceClass']=='timestamp_precision_only' for x in mismatches),
 'keyConclusions':[
  '原19天分为18个历史异常日、1个近期档案未发布/不可取日；本轮官方API恢复后剩18个历史日。',
  '旧档案55个不合格时槽包含47个无行和8个closeTime不合格；47个无行中16个近期小时已恢复。',
  '8个异常closeTime不能全称为短小时：2020-12-21 14:00 UTC旧行结束时间早于开始。',
  'API与月档不是完全相同快照：2020-12-21 13:00 UTC即使旧行通过格式校验，仍与新API OHLC及收盘时间不同。两份都保留，不静默覆盖。',
  '另有2021-12-24 03:00 UTC量/笔数差异，以及04:00 UTC无效小时末价和量/笔数差异。共3条实质来源差异；2026年8条完整小时的结束时间差999微秒仅是记录精度差别。',
  '维护公告只作为发生中断的证据。无公告的缺口不能认定无成交或停市；有公告也不能据此造价格或改时间戳。',
  '旧协议要求分段无缺口才可bootstrap；2020–2024仍不达标，不能删缺口拼接或改协议后称原试验通过。',
 ],'sources':sources,'days':days,
 'limits':{'oldFilesModified':False,'networkRequestsByAuditScript':0,'priceImputation':False,'exchangeChanged':False,
           'maintenanceCausalityForAllGapsVerified':False,'newStatisticalProtocolRegistered':False}}
(OUT/'market_gap_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
lines=['# 行情缺口复核 v0.6','',f"审计时间：{result['auditedAtUtc']}",'',
 '## 本轮结论','']+[f'- {x}' for x in result['keyConclusions']]+['',
 '原106份ZIP已逐份重新比对冻结SHA256及CRC。审计程序只读旧档与已保存API响应，不重复下载。','',
 '## 19个原异常日','',
 '| 北京时间日期 | 原不合格小时数 | 不合格小时起点（UTC） | 本轮结果 | 维护证据 |',
 '|---|---:|---|---|---|']
for x in days:
    hs='、'.join(h['openUtc'][:16].replace('T',' ') + ('*' if h['archiveRow'] else '') for h in x['hours'])
    lines.append(f"| {x['date']} | {x['unqualifiedHoursBefore']} | {hs} | {'补回'+str(x['recoveredHours'])+'小时' if x['recoveredHours'] else 'API仍不合格'} | {', '.join(x['maintenanceEvidenceIds']) or '原因未证实'} |")
lines += ['', '* 表示旧档有行但closeTime不符合完整小时；无星号表示旧档无该小时行。精确毫秒值、来源行号、API参数和日档候选URL见JSON。','',
 '## 不能被忽略的版本分歧','',
 '2020-12-21 13:00 UTC：旧月档末价22646.53，high 22665.35，结束13:59:59.999；新API末价22681.32，high 22774，结束13:47:20.521。旧14:00 UTC行零成交，结束时间又是13:47:20.521，早于开始时间。仅比较“两边均过校验的小时”会漏掉这个差异。保留原始版本及冲突记录，不将维护公告当作时间戳更正依据。','',
 '2021-12-24 03:00 UTC量/笔数存在差异；04:00 UTC本身是不完整小时，API末价50951.41与旧档50951.47不同。共3条实质差异。另有2026-09-25的8条完整小时，档案结束在.999999而API为.999，属于微秒/毫秒精度差别，不归类为行情冲突。','',
 '## 恢复路径与下一步','',
 '1. 2026-09-26的16小时已经使用同交易所公开API补回。日档CHECKSUM在本轮恢复时仍404；可在未来人工复核发布情况，未创建自动重试。',
 '2. 历史异常的官方API已查询，本次未修复。JSON保留按UTC日推导的daily ZIP与CHECKSUM候选URL；它们仅用于月档/日档版本核对，未声称一定有完整数据。',
 '3. 2020-12-21等来源不一致区间若继续核对，可另取官方1m或trades作独立审计；即使分钟可取也必须声明重建算法和数据版本，不能悄悄替换原1h试验。',
 '4. 若缺口最终客观存在，先登记新统计方案与新的假说版本，明确完整日筛选、连续片段、覆盖率、缺失机制与重采样规则，再运行；旧失败结果保留。','',
 '## 官方来源与读取层级','']
for s in sources: lines.append(f"- **{s['id']}** [{s['verified']}] [{s['summary']}]({s['url']})")
lines += ['', '无法访问正文的公告只记索引证据；Binance Square用户文章、第三方新闻和Reddit未作为结论依据。',
          '', '完整结构审计：`market_gap_audit.json`；原始HTTP捕获及恢复统计：`market_recovery.json`、`recovery_raw/`。']
(OUT/'market_gap_audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'days':len(days),'unqualifiedHours':sum(counts.values()),'counts':dict(counts),'archiveApiDifferences':len(mismatches),'addedHours':len(added)},ensure_ascii=False))
