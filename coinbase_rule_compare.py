"""Read-only rule comparisons derived from the sealed Coinbase study."""

from collections import Counter

from coinbase_study import confluence_summary, summarize_direction, timing_summary
from coinbase_study_store import read as read_coinbase_study

YEARS = (2023, 2024, 2025, 2026)


def _year(rows, year):
    selected = [row for row in rows if row['year'] == year]
    eligible = [row for row in selected if row['status'] == 'eligible']
    direction = summarize_direction(selected)
    all_months = next(item for item in direction['conditions'] if item['id'] == 'ALL')
    timing = timing_summary(selected)
    confluence = confluence_summary(selected)
    return {
        'year': year,
        'range': {'start': selected[0]['date'], 'end': selected[-1]['date']},
        'days': len(selected),
        'eligibleDays': len(eligible),
        'excluded': dict(Counter(row['status'] for row in selected if row['status'] != 'eligible')),
        'directionBaseline': {key: direction['baseline'][key]['hitRatePct'] for key in ('up', 'down')},
        'directionRules': [
            {'id': metric['id'], 'direction': metric['direction'], 'n': metric['n'],
             'correct': metric['correct'], 'incorrect': metric['incorrect'], 'tie': metric['tie'],
             'hitRatePct': metric['hitRatePct'],
             'matchedTriggerPct': metric['matched']['triggerHitRatePct'],
             'matchedBaselinePct': metric['matched']['baselineRatePct'],
             'matchedDeltaPp': metric['matched']['deltaPp'],
             'matchedN': metric['matched']['evaluated'],
             'unmatchedN': metric['matched']['unmatchedN']}
            for metric in all_months['rules']],
        'timing': {'commonEligibleDays': timing['commonEligibleDays'], 'metrics': timing['metrics']},
        'confluence': [
            {'id': group['id'], 'n': group['n'],
             'directionEvaluated': group['directionEvaluated'],
             'directionCorrect': group['directionCorrect']}
            for group in confluence],
    }


def read_comparison():
    report = read_coinbase_study('report')
    details = read_coinbase_study('details')
    symbols = []
    for source in details['symbols']:
        if source['symbol'] not in ('BTCUSD', 'ETHUSD'):
            raise ValueError('固定回测资产不符合协议')
        rows = source['daily']
        years = [_year(rows, year) for year in YEARS]
        symbols.append({'symbol': source['symbol'], 'years': years})
    return {
        'schemaVersion': 1,
        'studyId': report['studyId'],
        'retrospective': True,
        'registeredPrediction': False,
        'sourceEndDate': '2026-10-16',
        'protocolSha256': report['protocolSha256'],
        'symbols': symbols,
        'method': {
            'direction': '同年同节气月未触发日作为匹配对照；平盘计入分母',
            'timing': '严格转点左右各2日确认；候选日前后3日邻近，另列全日基准和覆盖率',
            'confluence': '同日出现，不从时间事件推断方向；冲突日单列',
        },
    }
