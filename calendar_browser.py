"""Read-only year/month/day browser, using the existing fixed calendar policy.

The cards describe calendar elements only. They never infer market direction.
Term times are lunar-python calculations, not newly verified observations.
"""
from __future__ import annotations

import calendar
from copy import deepcopy
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import parse_qs

from calendar_adapter import LOCAL, POLICY, Solar, calculate_calendar, parse_at

VERSION = '0.9.0'
MIN_AT = datetime(1992, 1, 1, tzinfo=LOCAL)
MAX_AT = datetime(2100, 1, 1, tzinfo=LOCAL)
JIE = ('立春', '惊蛰', '清明', '立夏', '芒种', '小暑', '立秋', '白露', '寒露', '立冬', '大雪', '小寒')
ALIASES = {'DA_XUE': '大雪', 'DONG_ZHI': '冬至', 'XIAO_HAN': '小寒', 'DA_HAN': '大寒',
           'LI_CHUN': '立春', 'YU_SHUI': '雨水', 'JING_ZHE': '惊蛰'}


def parse_browser_query(query):
    """Accept exactly one explicit-offset instant, including blank duplicate checks."""
    params = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    if any(len(values) != 1 for values in params.values()):
        raise ValueError('参数不能重复')
    if set(params) != {'at'}:
        raise ValueError('日历浏览只接受一个at参数')
    at = params['at'][0]
    if len(at) > 48:
        raise ValueError('时间格式过长')
    return parse_at(at).isoformat()


@lru_cache(maxsize=128)
def terms_in_year(year):
    """Use adjacent-year ephemeris entries only as range boundaries, not inputs."""
    table = Solar.fromYmdHms(year, 7, 1, 12, 0, 0).getLunar().getJieQiTable()
    found = {}
    for name, solar in table.items():
        if solar.getYear() == year:
            instant = datetime.fromisoformat(solar.toYmdHms()).replace(tzinfo=LOCAL)
            found[ALIASES.get(name, name)] = instant
    if len(found) != 24:
        raise RuntimeError('节气年表不完整')
    return tuple(sorted(found.items(), key=lambda item: item[1]))


def term_at(year, name):
    return dict(terms_in_year(year))[name]


def snapshot(at):
    return calculate_calendar(at.isoformat())


def range_card(start, end, pillar_key):
    visible_start, visible_end = max(start, MIN_AT), min(end, MAX_AT)
    available = visible_start < visible_end
    # Do not navigate exactly onto an independently unverified second boundary.
    click = visible_start + timedelta(minutes=3) if available else None
    if click and click >= visible_end:
        click = visible_start
    value = snapshot(click)['pillars'][pillar_key] if click else None
    return {'startAt': start.isoformat(), 'endAt': end.isoformat(),
            'at': click.isoformat() if click else None, 'clickAt': click.isoformat() if click else None,
            'pillar': value, 'ganzhi': value, 'available': available,
            'clippedStart': start < MIN_AT, 'clippedEnd': end > MAX_AT,
            'displayStartAt': visible_start.isoformat() if available else None,
            'displayEndAt': visible_end.isoformat() if available else None}


def shift_month(year, month, delta):
    offset = year * 12 + month - 1 + delta
    y, m = divmod(offset, 12)
    if not 1992 <= y <= 2099:
        return None
    return datetime(y, m + 1, 1, 12, tzinfo=LOCAL).isoformat()


@lru_cache(maxsize=48)
def _browser(local_iso):
    local = parse_at(local_iso)
    selected = snapshot(local)
    bazi_year = local.year if local >= term_at(local.year, '立春') else local.year - 1
    years = []
    # Early 1992 belongs to the previous Li Chun year; retain its clipped card.
    for year in range(max(1991, bazi_year - 5), min(2099, bazi_year + 5) + 1):
        start, end = term_at(year, '立春'), term_at(year + 1, '立春')
        item = range_card(start, end, 'year')
        if item['available']:
            years.append({**item, 'year': year, 'selected': start <= local < end})
    months = []
    bounds = [(name, term_at(bazi_year + (name == '小寒'), name)) for name in JIE]
    for index, (name, start) in enumerate(bounds):
        end = bounds[index + 1][1] if index + 1 < 12 else term_at(bazi_year + 1, '立春')
        months.append({**range_card(start, end, 'month'), 'monthIndex': index + 1,
                       'term': name, 'termName': name, 'selected': start <= local < end})
    daily_terms = {}
    for name, boundary in terms_in_year(local.year):
        if boundary.month != local.month:
            continue
        before = snapshot(boundary - timedelta(seconds=1))['pillars']
        after = snapshot(boundary)['pillars']
        transition = {'term': name, 'termName': name, 'at': boundary.isoformat(),
                      'changesYear': before['year'] != after['year'],
                      'changesMonth': before['month'] != after['month'],
                      'beforePillar': before['month'], 'afterPillar': after['month'],
                      'beforePillars': before, 'afterPillars': after,
                      'precisionNote': '库计算到秒；外部校核仅到分钟，临界秒未独立核验'}
        daily_terms.setdefault(boundary.date().isoformat(), []).append(transition)
    days = []
    for day in range(1, calendar.monthrange(local.year, local.month)[1] + 1):
        noon = datetime(local.year, local.month, day, 12, tzinfo=LOCAL)
        value = snapshot(noon)
        day_key = noon.date().isoformat()
        transitions = daily_terms.get(day_key, [])
        days.append({'date': day_key, 'dayNumber': day, 'weekday': noon.weekday(),
                     'at': noon.isoformat(), 'clickAt': noon.isoformat(),
                     'pillar': value['pillars']['day'], 'dayGanzhi': value['pillars']['day'],
                     'pillars': value['pillars'], 'selected': day == local.day,
                     'termTransitions': transitions, 'termBoundaries': transitions,
                     'hasTermBoundary': bool(transitions),
                     'hasMonthBoundary': any(t['changesMonth'] for t in transitions),
                     'hasYearBoundary': any(t['changesYear'] for t in transitions),
                     'nearBoundaryWarnings': value['nearBoundaryWarnings']})
    return {'schemaVersion': 1, 'version': VERSION,
            'year': local.year, 'month': local.month, 'baziYear': bazi_year,
            'selected': {'at': local.isoformat(), 'year': local.year, 'month': local.month,
                         'day': local.day, 'hour': local.hour, 'pillars': selected['pillars'],
                         'nearBoundaryWarnings': selected['nearBoundaryWarnings'],
                         'hourStemReference': selected['hourStemReference']},
            'years': years, 'months': months, 'days': days,
            'navigation': {'previousMonthAt': shift_month(local.year, local.month, -1),
                           'nextMonthAt': shift_month(local.year, local.month, 1)},
            'policy': POLICY, 'marketDirection': None, 'marketValidated': False,
            'displayPolicy': {'yearBoundary': 'exact_lichun', 'monthBoundary': 'exact_jie_not_zhongqi',
                              'rangeConvention': '[startAt,endAt)', 'dailySampleTime': '12:00:00+08:00',
                              'cardNavigation': 'three_minutes_after_visible_start',
                              'dailySampleNote': '流日卡为正午快照；交节当天的年/月柱可能在日内切换，请看交节时刻。'},
            'limits': ['颜色表示干支各自五行，不代表上涨下跌。',
                       '这里只展示流年、流月、流日；未推算资产大运或喜用神。',
                       '节气交接时刻沿用固定库；临界秒尚未独立核验。']}


def calendar_browser(at):
    # A caller cannot mutate cached cards for subsequent requests.
    return deepcopy(_browser(parse_at(at).isoformat()))
