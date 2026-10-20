"""Scoped structural features over validated pillars, without market interpretation.

The immutable base relationship engine remains separately versioned. This module
does not infer birth settings, favourable elements, transformations or opening.
"""
from itertools import product
from bazi_engine import ELEMENTS, GROUP_TABLES, PAIR_TABLES, ten_god

VERSION = '0.1.0'
SCOPE_ID = 'BZ-NATAL4-FLOW-YMD-V01'
# Conventional order, no numerical weights or strength allocation.
HIDDEN_STEMS = {
    '子': '癸', '丑': '己癸辛', '寅': '甲丙戊', '卯': '乙',
    '辰': '戊乙癸', '巳': '丙庚戊', '午': '丁己', '未': '己丁乙',
    '申': '庚壬戊', '酉': '辛', '戌': '戊辛丁', '亥': '壬甲',
}
STORAGE_BRANCH = {'木': '未', '火': '戌', '金': '丑', '水': '辰'}
NATAL_SLOTS = tuple('natal.' + k for k in ('year', 'month', 'day', 'hour'))
FLOW_SLOTS = ('flow.year', 'flow.month', 'flow.day')
SOURCE_REFS = ['BZ07-S01', 'BZ07-S02', 'BZ07-S03', 'BZ07-S04']


def pair_edges(natal, day, kind):
    patterns = {frozenset(p): p for p in PAIR_TABLES[kind]}
    return [{'pattern': patterns[frozenset((t['branch'], day['branch']))],
             'slots': [t['slot'], day['slot']]}
            for t in natal if frozenset((t['branch'], day['branch'])) in patterns]


def group_states(tokens, kind):
    result = []
    natal_branches = {t['branch'] for t in tokens if t['slot'].startswith('natal.')}
    without_day = {t['branch'] for t in tokens if t['slot'] != 'flow.day'}
    flow_branches = {t['branch'] for t in tokens if t['slot'].startswith('flow.')}
    present = {t['branch'] for t in tokens}
    for pattern, element in GROUP_TABLES[kind]:
        required = set(pattern)
        complete = required <= present
        members = {b: [t['slot'] for t in tokens if t['branch'] == b] for b in pattern}
        cross = any(any(s.startswith('natal.') for s in instance) and
                    any(s.startswith('flow.') for s in instance)
                    for instance in product(*(members[b] for b in pattern)))
        result.append({
            'pattern': pattern, 'elementLabel': element, 'complete': complete,
            'natalAlreadyComplete': required <= natal_branches,
            'flowOnlyComplete': required <= flow_branches,
            'crossLayerInstanceExists': cross,
            'flowDayNecessary': complete and not required <= without_day,
            'completedWithFlow': complete and not required <= natal_branches,
            'missingBranches': [b for b in pattern if b not in present],
            'members': members,
            'transformation': None,
        })
    return result


def calculate_features(relationships, calendar):
    """Internal adapter: relationships must come from bazi_engine.calculate."""
    tokens = relationships['tokens']
    natal = [t for t in tokens if t['slot'] in NATAL_SLOTS]
    flow = [t for t in tokens if t['slot'] in FLOW_SLOTS]
    if {t['slot'] for t in natal} != set(NATAL_SLOTS) or {t['slot'] for t in flow} != set(FLOW_SLOTS):
        raise ValueError('结构规则需要完整原局四柱及流年、流月、流日')
    scoped = natal + flow
    day = next(t for t in flow if t['slot'] == 'flow.day')
    day_stem = relationships['natalDayReference']['stem']
    day_element = relationships['natalDayReference']['element']
    hidden = []
    for token in natal:
        for stem in HIDDEN_STEMS[token['branch']]:
            hidden.append({
                'slot': token['slot'], 'branch': token['branch'], 'stem': stem,
                'tenGod': ten_god(day_stem, stem)['name'],
                'matchesDayMasterStem': stem == day_stem,
                'natalVisibleAt': [t['slot'] for t in natal if t['stem'] == stem],
                'flowVisibleAt': [t['slot'] for t in flow if t['stem'] == stem],
            })
    wealth = ELEMENTS[(ELEMENTS.index(day_element) + 2) % 5]
    branch = STORAGE_BRANCH.get(wealth)
    natal_storage = [t['slot'] for t in natal if t['branch'] == branch] if branch else []
    flow_storage = [t['slot'] for t in flow if t['branch'] == branch] if branch else []
    clash = pair_edges(natal, day, '六冲')
    storage_edges = [e for e in clash if e['slots'][0] in natal_storage] if branch else []
    treasury = {
        'wealthElement': wealth, 'abstractStorageBranch': branch,
        'mappingStatus': 'defined' if branch else 'earth_storage_disputed_unresolved',
        'natalStoragePositions': natal_storage, 'flowStoragePositions': flow_storage,
        'natalStoragePresent': bool(natal_storage) if branch else None,
        'flowDayOppositionEdges': storage_edges,
        'natalStorageOpposedByFlowDay': bool(storage_edges) if branch else None,
        'openingJudgement': None, 'favourableJudgement': None,
        'scopeNote': '流入的库支单列；不能代替原局有库。遇冲仅登记结构。',
    }
    union = pair_edges(natal, day, '六合')
    sanhe, sanhui = group_states(scoped, '三合'), group_states(scoped, '三会')
    # Catalog IDs correspond to the six versioned definitions, not trade signals.
    def feature(identity, label, value, evidence, **extra):
        return {'id': identity, 'title': label, 'value': value,
                'status': 'unknown' if value is None else ('matched' if value else 'not_matched'),
                'evidence': evidence, 'marketDirection': None, **extra}
    features = [
        feature('BZ-S01', '原局与流日六合', bool(union), union),
        feature('BZ-S02', '原局与流日六冲', bool(clash), clash),
        feature('BZ-S03', '三合支齐全', any(g['complete'] for g in sanhe), sanhe,
                flowDayNecessary=any(g['flowDayNecessary'] for g in sanhe)),
        feature('BZ-S04', '三会支齐全', any(g['complete'] for g in sanhui), sanhui,
                flowDayNecessary=any(g['flowDayNecessary'] for g in sanhui)),
        feature('BZ-S05', '原局含财五行对应墓库支', treasury['natalStoragePresent'], natal_storage),
        feature('BZ-S06', '原局墓库支与流日对冲', treasury['natalStorageOpposedByFlowDay'], storage_edges),
    ]
    return {
        'schemaVersion': 1, 'version': VERSION, 'scopeId': SCOPE_ID,
        'evaluatedAt': calendar['atResearchTimezone'],
        'calendarPolicyId': calendar['policy']['id'],
        'scope': {'natalSlots': list(NATAL_SLOTS), 'flowSlots': list(FLOW_SLOTS),
                  'excluded': ['flow.hour', 'flow.luck'],
                  'instantOnly': True,
                  'note': '按所选时刻检查；交节日可在日内改变，不能视为全天信号。'},
        'hiddenStems': hidden, 'exposurePolicy': '仅原局藏干；完全相同天干；原局可见与流年月日可见分别记录；日干自匹配单列，无强弱权重',
        'features': features, 'treasury': treasury, 'sourceRefs': SOURCE_REFS,
        'marketValidated': False, 'marketDirection': None,
        'unknowns': ['合化', '旺衰', '喜忌', '刑害破', '开库成败', '市场方向'],
    }
