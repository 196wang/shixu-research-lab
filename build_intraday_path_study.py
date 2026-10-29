"""Fixed, retrospective intraday audit of the verified Bitstamp hourly cache.

No market download or modification of the registered Coinbase forward plan.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import statistics

from build_bitstamp_long_history import OUT, LOCAL, STEP, canonical, checked_rows, sha
from build_bitstamp_long_study import load_local, synthetic_snapshot
from build_element_pattern_study import DAY_RULES
from coinbase_study import inspect
from exploration_generators import days_between
from month_study import daily_rows, episode_plan


ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / 'intraday_path_protocol.json'
OUTPUT = ROOT / 'research' / 'bitstamp-intraday-path-2013-2026.json'
BRANCHES = '寅卯辰巳午未申酉戌亥子丑'
FIRST, LAST = date(2013, 1, 1), date(2026, 10, 16)
PERIODS = {'2013-2021': (2013, 2021), '2022-2025': (2022, 2025),
           '2026-to-10-16': (2026, 2026)}


def verified_hours(manifest):
    """Check each raw response and reject duplicate or malformed hours."""
    hours = {}
    for chunk in manifest['chunks']:
        body = (OUT / 'raw_chunks' / f"{chunk['ordinal']:04d}.json").read_bytes()
        if sha(body) != chunk['responseSha256']:
            raise ValueError(f"Bitstamp raw chunk {chunk['ordinal']} changed")
        first = int(datetime.fromisoformat(chunk['firstUtc']).timestamp())
        last = int(datetime.fromisoformat(chunk['lastUtc']).timestamp())
        rows = checked_rows(body, first, last)
        if len(rows) != chunk['rowCount']:
            raise ValueError('Bitstamp chunk row count changed')
        for row in rows:
            stamp = int(row['timestamp'])
            if stamp in hours:
                raise ValueError('duplicate Bitstamp hour')
            hours[stamp] = row
    if len(hours) != manifest['hourCount']:
        raise ValueError('Bitstamp hour count changed')
    return hours


def day_profile(day, daily, hours):
    """Rebuild one Beijing day and infer only hour-level extreme ordering."""
    first = int(datetime.combine(day, time(), LOCAL).timestamp())
    rows = [hours.get(first + i * STEP) for i in range(24)]
    if any(row is None for row in rows):
        raise ValueError(f'{day}: missing hour')
    if daily['sourceHourSha256'] != sha(canonical(rows)):
        raise ValueError(f'{day}: daily provenance mismatch')
    opened, high, low, closed = (Decimal(daily[key]) for key in ('open', 'high', 'low', 'close'))
    if (opened != Decimal(rows[0]['open']) or closed != Decimal(rows[-1]['close'])
            or high != max(Decimal(row['high']) for row in rows)
            or low != min(Decimal(row['low']) for row in rows)):
        raise ValueError(f'{day}: daily OHLC mismatch')
    high_at = [i for i, row in enumerate(rows) if Decimal(row['high']) == high]
    low_at = [i for i, row in enumerate(rows) if Decimal(row['low']) == low]
    order = ('high_first' if max(high_at) < min(low_at) else
             'low_first' if max(low_at) < min(high_at) else 'ambiguous')
    up = float((high / opened - 1) * 100)
    down = float((1 - low / opened) * 100)
    return {'upExcursionPct': up, 'downExcursionPct': down,
            'rangePct': up + down, 'upBias': up > down, 'downBias': down > up,
            'biasTie': up == down, 'extremeOrder': order,
            'highHourLocal': high_at[0], 'lowHourLocal': low_at[0]}


def mean(values):
    return statistics.mean(values) if values else None


def metrics(rows, direction=None):
    n = len(rows)
    favorable = ('upBias' if direction == 'up' else 'downBias') if direction else None
    order = ('low_first' if direction == 'up' else 'high_first') if direction else None
    return {'n': n,
            'upBiasRatePct': 100 * sum(row['upBias'] for row in rows) / n if n else None,
            'downBiasRatePct': 100 * sum(row['downBias'] for row in rows) / n if n else None,
            'biasTieCount': sum(row['biasTie'] for row in rows),
            'highFirstRatePct': 100 * sum(row['extremeOrder'] == 'high_first' for row in rows) / n if n else None,
            'lowFirstRatePct': 100 * sum(row['extremeOrder'] == 'low_first' for row in rows) / n if n else None,
            'ambiguousOrderCount': sum(row['extremeOrder'] == 'ambiguous' for row in rows),
            'favorableBiasRatePct': 100 * sum(row[favorable] for row in rows) / n if n and favorable else None,
            'favorableOrderRatePct': 100 * sum(row['extremeOrder'] == order for row in rows) / n if n and order else None,
            'medianUpExcursionPct': statistics.median(row['upExcursionPct'] for row in rows) if n else None,
            'medianDownExcursionPct': statistics.median(row['downExcursionPct'] for row in rows) if n else None,
            'medianRangePct': statistics.median(row['rangePct'] for row in rows) if n else None,
            'meanRangePct': mean([row['rangePct'] for row in rows]),
            'medianCloseReturnPct': statistics.median(row['returnPct'] for row in rows) if n else None}


def matched(rows, predicate, direction=None):
    """Same-year and exact-Jie-episode weighted ordinary-date comparison."""
    strata = defaultdict(list)
    for row in rows:
        strata[(row['year'], row['episodeId'])].append(row)
    selected, weighted = [], defaultdict(float)
    distinct_control, unmatched = 0, 0
    control_keys = ('upBiasRatePct', 'downBiasRatePct', 'highFirstRatePct',
                    'lowFirstRatePct', 'favorableBiasRatePct', 'favorableOrderRatePct',
                    'meanRangePct')
    for members in strata.values():
        trigger = [row for row in members if predicate(row)]
        if not trigger:
            continue
        control = [row for row in members if not predicate(row)]
        if not control:
            unmatched += len(trigger)
            continue
        selected.extend(trigger)
        distinct_control += len(control)
        base = metrics(control, direction)
        for key in control_keys:
            if base[key] is not None:
                weighted[key] += base[key] * len(trigger)
    n = len(selected)
    return {'trigger': metrics(selected, direction),
            'matchedControl': {key: weighted[key] / n for key in control_keys} if n else {},
            'matchedControlDistinctDays': distinct_control,
            'unmatchedTriggerDays': unmatched}


def build():
    protocol_bytes = PROTOCOL.read_bytes()
    protocol = json.loads(protocol_bytes)
    if (protocol['id'] != 'BITSTAMP-INTRADAY-PATH-001' or
            protocol['firstDay'] != FIRST.isoformat() or protocol['lastDay'] != LAST.isoformat()):
        raise ValueError('intraday protocol or fixed study period changed')
    manifest, daily = load_local()
    if manifest['firstDay'] != FIRST.isoformat() or manifest['lastDay'] != LAST.isoformat():
        raise ValueError('Bitstamp cache range differs from protocol')
    hourly = verified_hours(manifest)
    snapshot, captured = synthetic_snapshot(manifest, daily, allow_thin=False)
    index = inspect(snapshot, FIRST, LAST, captured)
    episodes, boundaries = episode_plan(FIRST, LAST)
    base_rows = daily_rows(snapshot, days_between(FIRST, LAST), index, episodes, boundaries)
    jie_near = {center + timedelta(days=offset)
                for _, instant in boundaries for center in [instant.astimezone(LOCAL).date()]
                for offset in range(-2, 3) if offset != 0}
    eligible = []
    for row in base_rows:
        if row['status'] != 'eligible':
            continue
        day = date.fromisoformat(row['date'])
        if daily[row['date']]['quality'] != 'strict':
            raise ValueError(f'{day}: eligible day is not strict')
        eligible.append({**row, **day_profile(day, daily[row['date']], hourly),
                         'nearJie': day in jie_near,
                         'monthBranch': row['pillars']['month'][1]})
    period_rows = {name: [row for row in eligible if lo <= row['year'] <= hi]
                   for name, (lo, hi) in PERIODS.items()}
    period_rows.update({str(year): [row for row in eligible if row['year'] == year]
                        for year in range(FIRST.year, LAST.year + 1)})
    results = {}
    for period, members in period_rows.items():
        results[period] = {
            'allDays': metrics(members),
            'rules': {rule: {'label': label, 'favorableDirection': direction,
                             **matched(members, predicate, direction)}
                      for rule, (label, predicate, direction) in DAY_RULES.items()},
            'jieNear': matched(members, lambda row: row['nearJie']),
            'monthBranches': {branch: {'allDays': metrics([row for row in members if row['monthBranch'] == branch]),
                                       'rules': {rule: matched([row for row in members if row['monthBranch'] == branch],
                                                              predicate, direction)
                                                 for rule, (_, predicate, direction) in DAY_RULES.items()}}
                              for branch in BRANCHES}}
    return {'studyType': 'exploratory_retrospective_not_prediction',
            'protocolId': protocol['id'], 'protocolSha256': hashlib.sha256(protocol_bytes).hexdigest(),
            'source': {'provider': 'Bitstamp', 'market': 'BTC/USD spot',
                       'frequency': '1h', 'timezone': 'Asia/Shanghai',
                       'firstDay': FIRST.isoformat(), 'lastDay': LAST.isoformat(),
                       'manifestSha256': sha((OUT / 'manifest.json').read_bytes()),
                       'dailySha256': manifest['dailySha256'], 'rawChunkCount': len(manifest['chunks']),
                       'hourCount': len(hourly)},
            'quality': {'targetDays': len(base_rows), 'eligibleDays': len(eligible),
                        'statusCounts': dict(Counter(row['status'] for row in base_rows)),
                        'ambiguousOrderDays': sum(row['extremeOrder'] == 'ambiguous' for row in eligible)},
            'periods': results,
            'limits': ['Outcomes were already known when the hypotheses were chosen; this is not a blind test.',
                       'Hour-level OHLC cannot order extremes inside the same hour or interleaving ties.',
                       'A larger intraday excursion is not an executable profit or a close-direction prediction.',
                       'Early Bitstamp zero-volume hours are excluded; this can change historical coverage.',
                       'Year, month and rule subgroup comparisons are multiple exploratory comparisons.',
                       'Coinbase forward records and their sealed scoring are unchanged.']}


if __name__ == '__main__':
    result = build()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf8')
    print(OUTPUT)
