"""Small, fixed retrospective screen for stable BTC timing/risk patterns.

Uses the locally verified Bitstamp hourly-derived days. Calendar hypotheses are
compared with same solar-month complements; market volatility is measured with
only prior-day information. Results are exploratory, not a tuned strategy.
"""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import json
import statistics

from build_bitstamp_long_history import FIRST_DAY, LAST_DAY
from build_bitstamp_long_study import load_local, synthetic_snapshot
from build_element_pattern_study import day_observation, matched_daily
from coinbase_study import inspect
from exploration_generators import days_between
from month_study import daily_rows, episode_plan


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'research' / 'pattern-screen-2013-2026.json'
SPLITS = {'develop_2013_2020': (2013, 2020),
          'check_2021_2023': (2021, 2023),
          'late_2024_2026': (2024, 2026)}

# Declared before inspecting outcomes; no search over individual stems/days.
CALENDAR_CANDIDATES = {
    'metal_water_stem_in_metal_water_branch_month_up':
        ('金水日干 + 金水月支 → 收涨', lambda r: r['pillars']['day'][0] in '庚辛壬癸'
         and r['pillars']['month'][1] in '申酉亥子', 'up'),
    'metal_water_stem_in_other_branch_month_up':
        ('金水日干 + 非金水月支 → 收涨', lambda r: r['pillars']['day'][0] in '庚辛壬癸'
         and r['pillars']['month'][1] not in '申酉亥子', 'up'),
    'fire_stem_in_fire_earth_branch_month_down':
        ('火日干 + 火土月支 → 收跌', lambda r: r['pillars']['day'][0] in '丙丁'
         and r['pillars']['month'][1] in '巳午辰戌丑未', 'down'),
    'yin_day_in_yin_month_down':
        ('寅日 + 寅月 → 收跌', lambda r: r['pillars']['day'][1] == '寅'
         and r['pillars']['month'][1] == '寅', 'down'),
}


def pct(n, d):
    return 100 * n / d if d else None


def sign_lift(rows, predicate, direction):
    result = matched_daily(rows, predicate, direction)
    trigger = result['trigger']
    control = result['matchedControl']
    rate = pct(trigger.get('correct', 0), trigger.get('n', 0))
    baseline = control.get('correctRatePct')
    return {'n': trigger.get('n', 0), 'correct': trigger.get('correct', 0),
            'ratePct': rate, 'matchedControlPct': baseline,
            'liftPp': rate - baseline if rate is not None and baseline is not None else None,
            'unmatchedTriggerDays': result['unmatchedTriggerDays']}


def fixed_calendar(rows):
    result = {}
    for name, (label, predicate, direction) in CALENDAR_CANDIDATES.items():
        by_split = {}
        for split, (first, last) in SPLITS.items():
            subset = [r for r in rows if first <= r['year'] <= last]
            by_split[split] = sign_lift(subset, predicate, direction)
        yearly = {str(year): sign_lift([r for r in rows if r['year'] == year], predicate, direction)
                  for year in range(FIRST_DAY.year, LAST_DAY.year + 1)}
        result[name] = {'label': label, 'direction': direction,
                        'splits': by_split, 'years': yearly}
    return result


def rolling_volatility(days, jie_dates, multiplier=1.5, first=FIRST_DAY, last=LAST_DAY):
    ordered = list(days_between(first, last))
    daily = {}
    for day in ordered:
        src = days.get(day.isoformat(), {'quality': 'missing'})
        if src['quality'] != 'strict':
            continue
        opened = float(src['open'])
        daily[day] = {'rangePct': 100 * (float(src['high']) - float(src['low'])) / opened,
                      'returnPct': 100 * (float(src['close']) / opened - 1)}
    rows = []
    for index, day in enumerate(ordered):
        if day not in daily or index < 31:
            continue
        prior = ordered[index - 30:index]
        previous_prior = ordered[index - 31:index - 1]
        yesterday = ordered[index - 1]
        if any(d not in daily for d in prior + previous_prior):
            continue
        today_median = statistics.median(daily[d]['rangePct'] for d in prior)
        yesterday_median = statistics.median(daily[d]['rangePct'] for d in previous_prior)
        today_high = daily[day]['rangePct'] >= multiplier * today_median
        prev_high = daily[yesterday]['rangePct'] >= multiplier * yesterday_median
        nearby_jie = any(abs((day - event).days) <= 2 for event in jie_dates)
        rows.append({'date': day.isoformat(), 'year': day.year, 'month': day.month,
                     'rangePct': daily[day]['rangePct'], 'todayHigh': today_high,
                     'todayUp': daily[day]['returnPct'] > 0,
                     'previousHigh': prev_high, 'nearbyJie': nearby_jie})
    return rows


def risk_summary(rows, trigger):
    selected = [r for r in rows if r[trigger]]
    other = [r for r in rows if not r[trigger]]
    selected_rate = pct(sum(r['todayHigh'] for r in selected), len(selected))
    other_rate = pct(sum(r['todayHigh'] for r in other), len(other))
    # Same year/month controls reduce regime and seasonal confounding.
    strata = defaultdict(list)
    for row in rows:
        strata[(row['year'], row['month'])].append(row)
    matched_selected, weighted = 0, 0
    for members in strata.values():
        yes = [r for r in members if r[trigger]]
        no = [r for r in members if not r[trigger]]
        if not yes or not no:
            continue
        matched_selected += len(yes)
        weighted += len(yes) * pct(sum(r['todayHigh'] for r in no), len(no))
    matched = weighted / matched_selected if matched_selected else None
    return {'n': len(selected), 'highVolN': sum(r['todayHigh'] for r in selected),
            'highVolRatePct': selected_rate, 'otherN': len(other),
            'otherHighVolRatePct': other_rate, 'sameYearMonthControlPct': matched,
            'sameYearMonthComparableN': matched_selected,
            'liftPp': selected_rate - matched if selected_rate is not None and matched is not None else None,
            'upRatePct': pct(sum(r['todayUp'] for r in selected), len(selected)),
            'otherUpRatePct': pct(sum(r['todayUp'] for r in other), len(other)),
            'medianRangePct': statistics.median(r['rangePct'] for r in selected) if selected else None}


def fixed_risk(rows):
    result = {name: {'splits': {split: risk_summary([r for r in rows if first <= r['year'] <= last], name)
                                for split, (first, last) in SPLITS.items()},
                     'years': {str(year): risk_summary([r for r in rows if r['year'] == year], name)
                               for year in range(FIRST_DAY.year, LAST_DAY.year + 1)}}
              for name in ('previousHigh', 'nearbyJie')}
    result['jieAfterPreviousHigh'] = {
        split: risk_summary([r for r in rows if first <= r['year'] <= last and r['previousHigh']], 'nearbyJie')
        for split, (first, last) in SPLITS.items()}
    result['jieAfterPreviousCalm'] = {
        split: risk_summary([r for r in rows if first <= r['year'] <= last and not r['previousHigh']], 'nearbyJie')
        for split, (first, last) in SPLITS.items()}
    return result


def coinbase_crosscheck(jie_dates):
    from build_jie_event_study import read_snapshot

    first = date(2022, 1, 1)
    snapshot, captured, source_hash = read_snapshot()
    index = inspect(snapshot, first, LAST_DAY, captured)
    days = {}
    for day in days_between(first, LAST_DAY):
        row = index[day]
        if row['valid']:
            days[day.isoformat()] = {'quality': 'strict',
                                     **{key: str(row['prices'][key]) for key in ('open', 'high', 'low', 'close')}}
    rows = rolling_volatility(days, jie_dates, first=first)
    return {'provider': 'sealed Coinbase BTC/USD Beijing-day snapshot',
            'snapshotSha256': source_hash, 'eligibleN': len(rows),
            '2022-2023': risk_summary([r for r in rows if 2022 <= r['year'] <= 2023], 'previousHigh'),
            '2024-2026': risk_summary([r for r in rows if 2024 <= r['year'] <= 2026], 'previousHigh')}


def build():
    manifest, days = load_local()
    strict, captured = synthetic_snapshot(manifest, days, allow_thin=False)
    index = inspect(strict, FIRST_DAY, LAST_DAY, captured)
    episodes, boundaries = episode_plan(FIRST_DAY, LAST_DAY)
    raw = daily_rows(strict, days_between(FIRST_DAY, LAST_DAY), index, episodes, boundaries)
    eligible = [day_observation(row) for row in raw if row['status'] == 'eligible']
    jie_dates = {at.date() for _, at in boundaries if FIRST_DAY <= at.date() <= LAST_DAY}
    risk_rows = rolling_volatility(days, jie_dates)
    sensitivity = {}
    for multiplier in (1.25, 2.0):
        alternative = rolling_volatility(days, jie_dates, multiplier)
        sensitivity[str(multiplier)] = {
            split: risk_summary([r for r in alternative if first <= r['year'] <= last], 'previousHigh')
            for split, (first, last) in SPLITS.items()}
    return {'type': 'exploratory_fixed_screen_not_forecast',
            'sourceDailySha256': manifest['dailySha256'],
            'dataThrough': LAST_DAY.isoformat(), 'timezone': 'Asia/Shanghai',
            'splits': SPLITS, 'dailyDirectionEligibleN': len(eligible),
            'volatilityEligibleN': len(risk_rows),
            'definitions': {'highVol': 'today high-low/open >= 1.5 * median of prior 30 strict days',
                            'previousHigh': 'yesterday met its own prior-30-day highVol definition',
                            'nearbyJie': 'calendar date is within 2 days of a Jie transition',
                            'calendarControl': 'same year and exact solar-month episode non-trigger days',
                            'riskControl': 'same Gregorian year/month non-trigger days'},
            'calendar': fixed_calendar(eligible), 'risk': fixed_risk(risk_rows),
            'volatilityThresholdSensitivity': sensitivity,
            'coinbaseCrosscheck': coinbase_crosscheck(jie_dates),
            'limits': ['All rules and cutoffs selected after known market history; the time splits are retrospective checks, not true blind tests.',
                       'Multiple comparisons and serial dependence can make apparent lifts unstable.',
                       'Volatility persistence is a market baseline, not evidence for astrology or a profitable trading signal.',
                       'The Jie risk window is known before the day, but adjacent ±2-day windows overlap.',
                       'No execution costs, drawdown, slippage or risk-adjusted returns are calculated.']}


if __name__ == '__main__':
    result = build()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(OUTPUT)
