"""Read-only exploratory check of 庚戌 days in the sealed Coinbase study."""
from collections import defaultdict
from decimal import Decimal
from statistics import mean, median

from coinbase_study_store import read


def describe(rows):
    if not rows:
        return None
    returns = [float(row['returnPct']) for row in rows]
    ranges = [float((Decimal(row['high']) - Decimal(row['low'])) / Decimal(row['open']) * 100)
              for row in rows]
    down_excursion = [float((Decimal(row['open']) - Decimal(row['low'])) / Decimal(row['open']) * 100)
                      for row in rows]
    up_excursion = [float((Decimal(row['high']) - Decimal(row['open'])) / Decimal(row['open']) * 100)
                    for row in rows]
    return {'n': len(rows), 'down': sum(row['actual'] == 'down' for row in rows),
            'up': sum(row['actual'] == 'up' for row in rows),
            'meanReturnPct': round(mean(returns), 3), 'medianReturnPct': round(median(returns), 3),
            'meanRangePct': round(mean(ranges), 3),
            'meanDownExcursionPct': round(mean(down_excursion), 3),
            'meanUpExcursionPct': round(mean(up_excursion), 3),
            'downExcursionLargerDays': sum(down > up for down, up in zip(down_excursion, up_excursion))}


details = read('details')
for source in details['symbols']:
    rows = [row for row in source['daily'] if row['status'] == 'eligible']
    gengxu = [row for row in rows if row['pillars']['day'] == '庚戌']
    print(source['symbol'], 'ALL', describe(rows), 'GENGXU', describe(gengxu))
    for year in (2023, 2024, 2025, 2026):
        print('YEAR', year, describe([row for row in gengxu if row['year'] == year]))
    buckets = defaultdict(list)
    for row in rows:
        buckets[(row['year'], row['pillars']['month'])].append(row)
    for row in gengxu:
        control = [item for item in buckets[(row['year'], row['pillars']['month'])]
                   if item['pillars']['day'] != '庚戌']
        print(row['date'], row['pillars']['month'], row['actual'],
              round(row['returnPct'], 3), 'control_n', len(control),
              'control_down_pct', round(100 * mean(item['actual'] == 'down' for item in control), 1)
              if control else None)
    controls = [item for row in gengxu for item in buckets[(row['year'], row['pillars']['month'])]
                if item['pillars']['day'] != '庚戌']
    print('MATCHED CONTROL', describe(controls))
