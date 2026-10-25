"""Replicate calendar, Gann and astro timing studies on Bitstamp 2013–2026.

The Bitstamp series is independent from Coinbase. It is never spliced into an
existing Coinbase result or presented as a forward prediction.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import hashlib
import json
import statistics

from build_bitstamp_long_history import FIRST_DAY, LAST_DAY, OUT, LOCAL
from build_element_pattern_study import DAY_RULES, day_observation, matched_daily, month_groups, mean
from build_jie_event_study import read_snapshot
from coinbase_study import build as build_rule_study, inspect
from exploration_generators import days_between
from month_study import daily_rows, episode_plan, evaluate_episodes


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'research' / 'bitstamp-long-study-2013-2026.json'
SUMMARY_OUTPUT = ROOT / 'research' / 'bitstamp-long-summary-2013-2026.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_local():
    manifest = json.loads((OUT / 'manifest.json').read_text(encoding='utf8'))
    if manifest['dailySha256'] != digest(OUT / 'daily.json'):
        raise ValueError('Bitstamp daily file changed')
    for chunk in manifest['chunks']:
        path = OUT / 'raw_chunks' / f"{chunk['ordinal']:04d}.json"
        if digest(path) != chunk['responseSha256']:
            raise ValueError(f'Bitstamp raw chunk {chunk["ordinal"]} changed')
    return manifest, json.loads((OUT / 'daily.json').read_text(encoding='utf8'))


def synthetic_snapshot(manifest, days, *, allow_thin):
    captured = datetime.fromisoformat(manifest['capturedAtUtc'])
    candles, gaps = {}, {}
    for label, source in days.items():
        if source['quality'] == 'missing_hours':
            continue
        day = date.fromisoformat(label)
        opened_ms = int(datetime.combine(day, time(), LOCAL).timestamp() * 1000)
        candles[opened_ms] = {'symbol': 'BTCUSD', 'open_ms': opened_ms,
                              'close_ms': opened_ms + 86400000 - 1, 'is_closed': 1,
                              'request_id': 'bitstamp-hourly-' + label,
                              'body_sha256': source['sourceHourSha256'],
                              'updated': captured.isoformat(),
                              **{field: source[field] for field in ('open', 'high', 'low', 'close')}}
        if source['quality'] == 'thin' and not allow_thin:
            gaps[opened_ms] = ['zero_volume_hour']
    return ({'symbol': 'BTCUSD', 'provider': 'Bitstamp', 'last_success': captured.isoformat()},
            candles, gaps), captured


def element_summary(snapshot, captured):
    first, last = FIRST_DAY, LAST_DAY
    index = inspect(snapshot, first, last, captured)
    episodes, boundaries = episode_plan(first, last)
    raw_rows = daily_rows(snapshot, days_between(first, last), index, episodes, boundaries)
    eligible = [day_observation(row) for row in raw_rows if row['status'] == 'eligible']
    periods = {'2013-2021': [row for row in eligible if row['year'] <= 2021],
               '2022-2025': [row for row in eligible if 2022 <= row['year'] <= 2025],
               '2026-to-10-16': [row for row in eligible if row['year'] == 2026]}
    periods.update({str(year): [row for row in eligible if row['year'] == year]
                    for year in range(first.year, last.year + 1)})
    day_rules = {name: {identity: {'label': label, 'direction': direction,
                                   **matched_daily(members, predicate, direction)}
                        for identity, (label, predicate, direction) in DAY_RULES.items()}
                 for name, members in periods.items()}
    by_episode = defaultdict(list)
    for row in eligible:
        by_episode[row['episodeId']].append(row)
    complete = []
    for row in evaluate_episodes(episodes, index, captured):
        if row['status'] != 'complete':
            continue
        members = by_episode[row['id']]
        if not members:
            continue
        complete.append({'id': row['id'], 'year': row['year'], 'term': row['term'],
                         'pillar': row['pillar'], 'stemElement': row['stemElement'],
                         'branchElement': row['branchElement'], 'startAt': row['startAt'],
                         'endAt': row['endAt'], 'returnPct': row['returnPct'],
                         'dailyN': len(members),
                         'dailyUpCount': sum(day['actual'] == 'up' for day in members),
                         'dailyDownCount': sum(day['actual'] == 'down' for day in members),
                         'meanDailyRangePct': mean([day['rangePct'] for day in members])})
    month_periods = {'2013-2021': [row for row in complete if row['year'] <= 2021],
                     '2022-2025': [row for row in complete if 2022 <= row['year'] <= 2025],
                     '2026-completed': [row for row in complete if row['year'] == 2026]}
    month_periods.update({str(year): [row for row in complete if row['year'] == year]
                          for year in range(first.year, last.year + 1)})
    return {'quality': {'targetDays': len(raw_rows), 'eligibleDays': len(eligible),
                        'statusCounts': dict(Counter(row['status'] for row in raw_rows)),
                        'completeMonthEpisodes': len(complete)},
            'dailyRules': day_rules, 'monthlyGroups': {name: month_groups(members)
                                                       for name, members in month_periods.items()},
            'monthlyEpisodes': complete}


def overlap_with_coinbase(bitstamp_snapshot, captured):
    coinbase, coinbase_captured, coinbase_hash = read_snapshot()
    first, last = date(2022, 1, 1), LAST_DAY
    bitstamp = inspect(bitstamp_snapshot, first, last, captured)
    reference = inspect(coinbase, first, last, coinbase_captured)
    same, different, differences = 0, 0, []
    by_year = defaultdict(lambda: {'n': 0, 'sameSign': 0})
    for day in days_between(first, last):
        a, b = bitstamp[day], reference[day]
        if not a['valid'] or not b['valid']:
            continue
        ar = float((a['prices']['close'] / a['prices']['open'] - 1) * 100)
        br = float((b['prices']['close'] / b['prices']['open'] - 1) * 100)
        agree = (ar > 0) == (br > 0) if ar and br else ar == br
        same += agree
        different += not agree
        differences.append(abs(ar - br))
        by_year[str(day.year)]['n'] += 1
        by_year[str(day.year)]['sameSign'] += agree
    return {'sourceA': 'Bitstamp BTC/USD strict full-volume-hour days',
            'sourceB': 'sealed Coinbase BTC/USD Beijing days',
            'coinbaseSnapshotSha256': coinbase_hash, 'commonValidDays': same + different,
            'sameDirection': same, 'differentDirection': different,
            'medianAbsoluteDailyReturnDifferencePp': statistics.median(differences) if differences else None,
            'byYear': dict(by_year)}


def timing_summary(snapshot, captured):
    protocol = json.loads((ROOT / 'coinbase_study_protocol.json').read_text(encoding='utf8'))
    protocol.update({'studyId': 'BITSTAMP-BTCUSD-2013-2026-RETRO',
                     'provider': 'Bitstamp official public hourly OHLC',
                     'symbols': ['BTCUSD'], 'sourceStartDate': FIRST_DAY.isoformat(),
                     'startDate': FIRST_DAY.isoformat(), 'endDate': LAST_DAY.isoformat(),
                     'reportBlocks': [
                         {'id': '2013-2021', 'start': '2013-01-01', 'end': '2021-12-31'},
                         {'id': '2022-2025', 'start': '2022-01-01', 'end': '2025-12-31'},
                         {'id': '2026-to-10-16', 'start': '2026-01-01', 'end': LAST_DAY.isoformat()}],
                     'registeredPrediction': False, 'independentBlindSample': False,
                     'status': 'exploratory_bitstamp_replication'})
    report, _ = build_rule_study({'BTCUSD': snapshot}, protocol, captured)
    report['symbols'][0]['source']['provider'] = 'Bitstamp'
    report['symbols'][0]['source']['marketType'] = 'spot'
    report['limitations'] = [item for item in report['limitations'] if not item.startswith('ETH 仅作跨资产对照')]
    report['limitations'].append('Bitstamp 的零成交量小时按主要严格口径排除；与 Coinbase 不拼接。')
    return report


def build():
    manifest, days = load_local()
    strict, captured = synthetic_snapshot(manifest, days, allow_thin=False)
    inclusive, _ = synthetic_snapshot(manifest, days, allow_thin=True)
    print('calculating strict element/calendar study', flush=True)
    primary = element_summary(strict, captured)
    print('calculating thin-hour sensitivity', flush=True)
    sensitivity = element_summary(inclusive, captured)
    print('calculating timing families', flush=True)
    timing = timing_summary(strict, captured)
    print('calculating Coinbase overlap', flush=True)
    overlap = overlap_with_coinbase(strict, captured)
    return {'studyType': 'exploratory_retrospective_not_prediction',
            'source': {'provider': 'Bitstamp official public BTC/USD OHLC',
                       'frequency': 'hourly, combined into complete Beijing days',
                       'firstDay': FIRST_DAY.isoformat(), 'lastDay': LAST_DAY.isoformat(),
                       'capturedAtUtc': manifest['capturedAtUtc'],
                       'localRawManifestSha256': digest(OUT / 'manifest.json'),
                       'localDailySha256': manifest['dailySha256'],
                       'rawResponses': 'local_data only; not redistributed in the public repository',
                       'hourCount': manifest['hourCount'], 'strictDays': manifest['strictDays'],
                       'thinDays': manifest['thinDays'], 'missingDays': manifest['missingDays'],
                       'zeroVolumeHours': manifest['zeroVolumeHours']},
            'primaryPolicy': '24 consecutive 1h candles, every hour positive traded volume',
            'sensitivityPolicy': '24 consecutive 1h candles, zero-volume hours allowed but marked thin',
            'primary': primary, 'thinHourSensitivity': {'quality': sensitivity['quality'],
                                                      'dailyRules': sensitivity['dailyRules'],
                                                      'monthlyGroups': sensitivity['monthlyGroups']},
            'timingFamilies': timing, 'coinbaseOverlap': overlap,
            'limits': ['Historical hypotheses were inspected after market outcomes and are not blind.',
                       'Bitstamp is a different exchange from Coinbase; do not splice price series.',
                       'Early zero-volume hourly candles may represent stale rather than traded prices.',
                       'The strict policy sacrifices coverage to avoid using stale hourly candles.',
                       'Calendar month boundary days are excluded from direction attribution.',
                       'No transaction costs, slippage, position sizing or executable returns.']}


def public_summary(result):
    symbol = result['timingFamilies']['symbols'][0]
    return {'studyType': result['studyType'], 'source': result['source'],
            'primaryPolicy': result['primaryPolicy'],
            'sensitivityPolicy': result['sensitivityPolicy'],
            'primaryQuality': result['primary']['quality'],
            'dailyRules': result['primary']['dailyRules'],
            'monthlyGroups': result['primary']['monthlyGroups'],
            'thinHourSensitivity': result['thinHourSensitivity'],
            'timingBlocks': [{'id': block['id'], 'range': block['range'],
                              'quality': block['quality'], 'timing': block['timing'],
                              'confluence': block['confluence']}
                             for block in symbol['blocks']],
            'coinbaseOverlap': result['coinbaseOverlap'], 'limits': result['limits']}


if __name__ == '__main__':
    result = build()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    SUMMARY_OUTPUT.write_text(json.dumps(public_summary(result), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    print(OUTPUT)
