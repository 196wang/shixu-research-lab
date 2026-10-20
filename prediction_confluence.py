"""Descriptive agreement among declared rules, without implying independence or accuracy."""
from datetime import date, datetime, timedelta, timezone

LOCAL = timezone(timedelta(hours=8))
KINDS = ('up', 'down', 'high', 'low', 'time')
OPPOSITES = {'up': 'down', 'down': 'up', 'high': 'low', 'low': 'high'}


def window(record):
    day = date.fromisoformat(record['targetDate'])
    return ((day - timedelta(days=3), day + timedelta(days=3))
            if record['kind'] in ('high', 'low', 'time')
            else (day, day + timedelta(days=record['horizonDays'] - 1)))


def overlaps(a, b):
    sa, ea = window(a)
    sb, eb = window(b)
    return max(sa, sb) <= min(ea, eb)


def evidence(record):
    # An observation-only rule cannot supply a directional vote. Duplicate rule
    # references are one item even when repeated across records or revisions.
    return {(r['family'], r['id']) for r in record.get('rules', [])
            if r.get('direction') == record['kind']}


def describe_confluence(records, as_of=None):
    at = as_of or datetime.now(timezone.utc)
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('共振快照时间必须有时区')
    chosen = {}
    for r in records:
        if r.get('kind') not in KINDS or datetime.fromisoformat(r['createdAtUtc'].replace('Z', '+00:00')) > at:
            continue
        root = r.get('rootId', r['id'])
        if root not in chosen or (r['version'], r['createdAtUtc']) > (chosen[root]['version'], chosen[root]['createdAtUtc']):
            chosen[root] = r
    # invalidation events are attached by the ledger at the evaluation as-of.
    active = [r for r in chosen.values() if not r.get('invalidated') and not r.get('lateRevision', False)
              and r.get('prospectiveEligible', True)]
    rows = []
    for r in active:
        peers = [p for p in active if p['id'] != r['id'] and p['symbol'] == r['symbol'] and overlaps(r, p)]
        same = [p for p in peers if p['kind'] == r['kind']]
        conflict = [p for p in peers if p['kind'] == OPPOSITES.get(r['kind'])]
        timing = [p for p in peers if p['kind'] == 'time' or r['kind'] == 'time']
        refs = set().union(*(evidence(p) for p in [r] + same))
        families = sorted({item[0] for item in refs if item[0] != 'MANUAL'})
        raw_count = sum(len(evidence(p)) for p in [r] + same)
        simultaneous = []
        first, last = window(r)
        for offset in range((last-first).days+1):
            day = first + timedelta(days=offset)
            current = [p for p in [r] + same if window(p)[0] <= day <= window(p)[1]]
            keys = set().union(*(evidence(p) for p in current))
            genuine = {key for key in keys if key[0] != 'MANUAL'}
            fs = sorted({key[0] for key in genuine})
            if len(genuine) < 2:
                continue
            ids = sorted(p['id'] for p in current)
            if simultaneous and simultaneous[-1]['families'] == fs and simultaneous[-1]['predictionIds'] == ids and date.fromisoformat(simultaneous[-1]['endDate']) + timedelta(days=1) == day:
                simultaneous[-1]['endDate'] = day.isoformat()
            else:
                simultaneous.append({'startDate': day.isoformat(), 'endDate': day.isoformat(),
                                     'families': fs, 'ruleCount': len(genuine), 'predictionIds': ids})
        cross = any(len(w['families']) >= 2 for w in simultaneous)
        rows.append({'predictionId': r['id'], 'rootId': r.get('rootId', r['id']),
                     'symbol': r['symbol'], 'kind': r['kind'], 'targetDate': r['targetDate'],
                     'sameKindIds': [p['id'] for p in same], 'conflictingIds': [p['id'] for p in conflict],
                     'timingOnlyIds': [p['id'] for p in timing],
                     'distinctRuleCount': len(refs), 'distinctTheoryFamilies': families,
                     'sameFamilyAgreement': any(len(w['families']) == 1 for w in simultaneous),
                     'crossFamilyAgreement': cross and r['kind'] != 'time',
                     'crossFamilyTimingOnly': cross and r['kind'] == 'time',
                     'agreementWindows': simultaneous,
                     'duplicateReferencesIgnored': raw_count - len(refs),
                     'hasConflict': bool(conflict),
                     'evidenceRuleKeys': [list(key) for key in sorted(refs)]})
    return {'schemaVersion': 1, 'version': '0.1.0', 'asOfUtc': at.astimezone(timezone.utc).isoformat(),
            'rows': rows, 'activeRoots': len(active), 'marketValidated': False,
            'policy': {'oneVersionPerRoot': 'latest_noninvalidated_eligible_current_version; no fallback after invalidation or late revision',
                       'agreement': 'same_symbol_same_kind_overlapping_declared_windows',
                       'noTransitiveClustering': True, 'timingToleranceDays': 3,
                       'agreementRequiresCommonDate': True,
                       'familyLabelsAreDeclared': True, 'statisticalIndependenceEstablished': False,
                       'oppositePairs': [['up', 'down'], ['high', 'low']],
                       'neutralEventsVoteForDirection': False},
            'note': '规则同向是描述性重合。重复引用只计一次，理论家族不同也不代表统计独立；无方向事件不投涨跌票。'}
