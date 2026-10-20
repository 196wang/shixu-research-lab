"""Calendar-month and daily views over the sealed Coinbase retrospective rows."""
from collections import Counter

from coinbase_study_store import read as read_coinbase_study

RULE_DIRECTIONS = {
    'DAY-STEM-METALWATER-UP': 'up',
    'DAY-STEM-FIRE-DOWN': 'down',
    'DAY-BRANCH-YIN-DOWN': 'down',
}
SYMBOLS = {'BTCUSD', 'ETHUSD'}
YEARS = {2023, 2024, 2025, 2026}


def summarize(rows):
    months = {}
    daily = []
    for row in rows:
        month = row['date'][:7]
        item = months.setdefault(month, {'month': month, 'up': 0, 'down': 0,
            'flat': 0, 'excluded': 0, 'missing': 0, 'upDates': [], 'downDates': [],
            'flatDates': [], 'excludedDates': [], 'missingDates': [], 'ruleCounts': {
                key: {'triggered': 0, 'correct': 0, 'incorrect': 0, 'flat': 0}
                for key in RULE_DIRECTIONS}, 'conflictDates': []})
        status = row['status']
        actual = row['actual'] if status == 'eligible' else None
        if status == 'eligible' and actual in ('up', 'down', 'flat'):
            item[actual] += 1
            item[actual + 'Dates'].append(row['date'])
        elif status == 'boundary_excluded':
            item['excluded'] += 1
            item['excludedDates'].append(row['date'])
        else:
            item['missing'] += 1
            item['missingDates'].append(row['date'])
        rule_ids = [key for key in row['ruleIds'] if key in RULE_DIRECTIONS] if status == 'eligible' else []
        directions = {RULE_DIRECTIONS[key] for key in rule_ids}
        if len(directions) > 1:
            item['conflictDates'].append(row['date'])
        outcomes = {}
        for key in rule_ids:
            outcome = 'flat' if actual == 'flat' else 'correct' if actual == RULE_DIRECTIONS[key] else 'incorrect'
            outcomes[key] = outcome
            item['ruleCounts'][key]['triggered'] += 1
            item['ruleCounts'][key][outcome] += 1
        daily.append({'date': row['date'], 'status': status, 'actual': actual,
            'dayPillar': (row['pillars'] or {}).get('day'),
            'monthPillar': (row['pillars'] or {}).get('month'),
            'open': row['open'] if status == 'eligible' else None,
            'close': row['close'] if status == 'eligible' else None,
            'returnPct': row['returnPct'] if status == 'eligible' else None,
            'ruleIds': rule_ids, 'ruleOutcomes': outcomes,
            'reasons': row['reasons'] if status != 'eligible' else []})
    for item in months.values():
        item['eligible'] = item['up'] + item['down'] + item['flat']
        item['days'] = item['eligible'] + item['excluded'] + item['missing']
    return {'months': list(months.values()), 'daily': daily}


def read_calendar(symbol, year):
    if symbol not in SYMBOLS or type(year) is not int or year not in YEARS:
        raise ValueError('仅支持 BTCUSD／ETHUSD 与 2023–2026 年')
    details = read_coinbase_study('details')
    source = next(item for item in details['symbols'] if item['symbol'] == symbol)
    rows = [row for row in source['daily'] if row['year'] == year]
    if not rows:
        raise ValueError('固定回测没有该年份的逐日记录')
    return {'studyId': 'CB-USD-HX-001', 'symbol': symbol, 'year': year,
            'range': {'start': rows[0]['date'], 'end': rows[-1]['date']},
            'retrospective': True, 'source': 'sealed Coinbase Exchange BTC/USD or ETH/USD Beijing daily bars',
            **summarize(rows)}
