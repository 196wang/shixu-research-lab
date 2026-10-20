"""Known-pillar relationship engine. Standard library; no date conversion or trading signals."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

VERSION = '0.4.0'
STEMS = '甲乙丙丁戊己庚辛壬癸'
BRANCHES = '子丑寅卯辰巳午未申酉戌亥'
ELEMENTS = '木火土金水'
SIXTY = tuple(STEMS[i % 10] + BRANCHES[i % 12] for i in range(60))
POSITIONS = ('year', 'month', 'day', 'hour')
FLOW_POSITIONS = ('luck',) + POSITIONS
TEN_GODS = (('比肩', '劫财'), ('食神', '伤官'), ('偏财', '正财'),
            ('七杀', '正官'), ('偏印', '正印'))
RELATIONS = ('同我', '我生', '我克', '克我', '生我')
BRANCH_ELEMENT = dict(zip(BRANCHES, '水土木木土火火土金金土水'))
PAIR_TABLES = {
    '六合': ('子丑', '寅亥', '卯戌', '辰酉', '巳申', '午未'),
    '六冲': ('子午', '丑未', '寅申', '卯酉', '辰戌', '巳亥'),
}
GROUP_TABLES = {
    '三合': (('申子辰', '水'), ('亥卯未', '木'), ('寅午戌', '火'), ('巳酉丑', '金')),
    '三会': (('亥子丑', '水'), ('寅卯辰', '木'), ('巳午未', '火'), ('申酉戌', '金')),
}
UNSUPPORTED = ['公历转干支', '节气与日界计算', '真太阳时', '起运与顺逆排运', '地支藏干及其十神',
               '天干五合', '刑害破与自刑', '半合拱合', '旺衰格局调候喜忌', '合化与解冲', '市场方向与收益']


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def stem_info(stem):
    if not isinstance(stem, str) or len(stem) != 1 or stem not in STEMS:
        raise ValueError(f'非法天干: {stem!r}')
    i = STEMS.index(stem)
    return {'stem': stem, 'element': ELEMENTS[i // 2], 'polarity': '阳' if i % 2 == 0 else '阴'}


def ten_god(day_stem, other_stem):
    d, s = stem_info(day_stem), stem_info(other_stem)
    r = (ELEMENTS.index(s['element']) - ELEMENTS.index(d['element'])) % 5
    same = d['polarity'] == s['polarity']
    return {'name': TEN_GODS[r][0 if same else 1], 'relationToReference': RELATIONS[r],
            'samePolarity': same, 'reference': d, 'target': s,
            'derivation': f"{d['element']}→{s['element']}：{RELATIONS[r]}，阴阳{'相同' if same else '不同'}"}


def validate_pillars(pillars, *, complete, label, hour_reference='same_day'):
    if not isinstance(pillars, dict):
        raise ValueError(f'{label}必须是对象')
    allowed = POSITIONS if complete else FLOW_POSITIONS
    unknown = set(pillars) - set(allowed)
    if unknown:
        raise ValueError(f'{label}未知字段: {sorted(unknown)}')
    if complete and set(pillars) != set(POSITIONS):
        raise ValueError(f'{label}必须包含year/month/day/hour四柱')
    for name, pillar in pillars.items():
        if pillar not in SIXTY:
            raise ValueError(f'{label}.{name}不是有效六十甲子: {pillar!r}')
    checks = []
    if hour_reference not in ('same_day', 'next_day_late_zi'):
        raise ValueError('未知时干参考日约定')
    if 'year' in pillars and 'month' in pillars:
        y, m = pillars['year'], pillars['month']
        month_offset = (BRANCHES.index(m[1]) - BRANCHES.index('寅')) % 12
        expected = STEMS[(STEMS.index(y[0]) % 5 * 2 + 2 + month_offset) % 10]
        if m[0] != expected:
            raise ValueError(f'{label}年干/月干不相容：{y}的{m[1]}月应为{expected}{m[1]}，收到{m}')
        checks.append({'check': 'year_month_stem', 'result': 'pass', 'expected': expected + m[1]})
    if 'day' in pillars and 'hour' in pillars:
        d, h = pillars['day'], pillars['hour']
        reference_stem = d[0]
        if hour_reference == 'next_day_late_zi':
            if h[1] != '子':
                raise ValueError('次日参考只适用于显式标记的晚子时')
            reference_stem = SIXTY[(SIXTY.index(d) + 1) % 60][0]
        expected = STEMS[(STEMS.index(reference_stem) % 5 * 2 + BRANCHES.index(h[1])) % 10]
        if h[0] != expected:
            raise ValueError(f'{label}日干/时干不相容：{d}的{h[1]}时应为{expected}{h[1]}，收到{h}')
        checks.append({'check': 'day_hour_stem', 'result': 'pass', 'expected': expected + h[1],
                       'hourStemReference': hour_reference, 'referenceDayStem': reference_stem})
    return checks


def scope(slots):
    contexts = {slot.split('.')[0] for slot in slots}
    return 'cross_natal_flow' if len(contexts) > 1 else next(iter(contexts))


def branch_patterns(tokens):
    pairs, groups, incomplete = [], [], []
    for a, b in itertools.combinations(tokens, 2):
        chars = {a['branch'], b['branch']}
        for kind, table in PAIR_TABLES.items():
            for pattern in table:
                if chars == set(pattern):
                    slots = [a['slot'], b['slot']]
                    pairs.append({'type': kind, 'pattern': pattern, 'slots': slots,
                                  'scope': scope(slots), 'status': 'structural_match_only',
                                  'transformation': 'undetermined'})
    for kind, table in GROUP_TABLES.items():
        for pattern, element in table:
            members = {char: [x['slot'] for x in tokens if x['branch'] == char] for char in pattern}
            missing = [char for char, slots in members.items() if not slots]
            if not missing:
                instances = [list(x) for x in itertools.product(*members.values())]
                groups.append({'type': kind, 'pattern': pattern, 'traditionalElementLabel': element,
                               'memberSlots': members, 'instances': instances,
                               'instanceScopes': [scope(x) for x in instances],
                               'structuralCombinationCount': len(instances),
                               'status': 'all_branches_present_not_transformed',
                               'transformation': 'undetermined'})
            elif len(missing) == 1:
                incomplete.append({'type': kind, 'pattern': pattern, 'memberSlots': members,
                                   'missing': missing, 'status': 'incomplete_not_called_half_combination'})
    return {'pairMatches': pairs, 'completeGroups': groups, 'incompleteGroups': incomplete,
            'coexistingRelations': '全部并列；不自动按合解冲、不选择唯一组合、不按数量判强弱'}


def calculate(payload):
    if not isinstance(payload, dict) or type(payload.get('schemaVersion')) is not int or payload.get('schemaVersion') != 1:
        raise ValueError('输入必须是schemaVersion=1的对象')
    if set(payload) - {'schemaVersion', 'profile', 'flow', 'context'}:
        raise ValueError('未知顶层输入字段')
    if not isinstance(payload.get('context', {}), dict):
        raise ValueError('context必须是对象')
    profile = payload.get('profile')
    if not isinstance(profile, dict) or not isinstance(profile.get('id'), str) or not profile['id']:
        raise ValueError('profile.id为必填非空字符串')
    if profile.get('entityType') not in ('asset', 'person'):
        raise ValueError('profile.entityType必须为asset或person')
    natal, flow = profile.get('pillars'), payload.get('flow', {})
    ctx = payload.get('context', {})
    checks = {'natal': validate_pillars(natal, complete=True, label='profile.pillars',
                                        hour_reference=ctx.get('natalHourReference', 'same_day')),
              'flow': validate_pillars(flow, complete=False, label='flow',
                                       hour_reference=ctx.get('flowHourReference', 'same_day'))}
    day_stem = natal['day'][0]
    tokens = []
    for layer, pillars, order in [('natal', natal, POSITIONS), ('flow', flow, FLOW_POSITIONS)]:
        for position in order:
            if position not in pillars:
                continue
            pillar = pillars[position]
            tokens.append({'slot': f'{layer}.{position}', 'pillar': pillar, 'stem': pillar[0],
                           'branch': pillar[1], 'branchPrimaryElement': BRANCH_ELEMENT[pillar[1]],
                           'tenGod': ten_god(day_stem, pillar[0]),
                           'displayRole': '日主' if (layer, position) == ('natal', 'day') else '比较天干'})
    stem_pairs = []
    for a, b in itertools.combinations(tokens, 2):
        relation = ten_god(a['stem'], b['stem'])
        stem_pairs.append({'referenceSlot': a['slot'], 'targetSlot': b['slot'],
                           'elementRelationToReference': relation['relationToReference'],
                           'derivation': relation['derivation']})
    return {'schemaVersion': 1, 'engineVersion': VERSION, 'inputSha256': digest(payload),
            'profile': profile, 'suppliedFlow': flow, 'context': payload.get('context', {}),
            'checks': checks, 'natalDayReference': stem_info(day_stem), 'tokens': tokens,
            'stemPairRelations': stem_pairs, 'branchRelations': branch_patterns(tokens),
            'sourceRefs': ['SRC-HKO-GZ', 'SRC-BZ-RELATION-TABLE-V03'],
            'interpretation': {'strength': None, 'favorableElements': None, 'transformation': None,
                               'marketDirection': None, 'marketValidated': False,
                               'state': 'relationships_only_not_an_interpretive_chart'},
            'warnings': ['给定四柱只校验内部关系；未按出生日期时地重排。',
                         '支的主五行标签不是藏干/旺衰权重，重复支不是额外独立市场样本。',
                         '日主自身十神算法返回比肩；界面日主标记不是另一个十神。',
                         '原局与岁运并列检测只是本研究的列举方式；组合成立条件仍须解释。'],
            'unsupported': UNSUPPORTED}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        output = calculate(json.loads(args.input.read_text(encoding='utf-8-sig')))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f'输入错误: {exc}\n')
    encoded = json.dumps(output, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')
    else:
        print(encoded, end='')


if __name__ == '__main__':
    main()
