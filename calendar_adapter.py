"""UTC-aware timestamp -> research pillars in Asia/Shanghai, explicit midnight policy."""
from __future__ import annotations
import argparse
from datetime import datetime, date, time, timezone, timedelta
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'vendor'))
from lunar_python import Solar
from bazi_engine import calculate

VERSION = '0.5.0'
LOCAL = timezone(timedelta(hours=8), 'Asia/Shanghai research UTC+08')
POLICY = {
    'id': 'BZ-CALENDAR-CIVIL-00-V01', 'timezone': 'Asia/Shanghai',
    'utcOffset': '+08:00', 'supportedYears': [1992, 2099],
    'yearBoundary': 'exact_lichun', 'monthBoundary': 'exact_jie_not_zhongqi',
    'dayBoundary': '00:00', 'trueSolarTime': False,
    'lateZiHourStemReference': 'next_day_late_zi',
    'calendarEngine': 'lunar-python', 'calendarVersion': '1.4.8',
    'note': '采用该库sect2日柱与晚子时干约定；日柱仍当天，23点后的时干以次日日干起算。',
}


def parse_at(at: str):
    if not isinstance(at, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', at):
        raise ValueError('at必须是带时区的ISO时间')
    if at.endswith('-00:00'):
        raise ValueError('-00:00可代表未知时区；请给Z或明确偏移')
    dt = datetime.fromisoformat(at.replace('Z', '+00:00'))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('必须给Z或±HH:MM，禁止猜测时区')
    local = dt.astimezone(LOCAL)
    if not 1992 <= local.year <= 2099:
        raise ValueError('本版限定1992–2099，避免混入中国历史夏令时等未处理设置')
    if local.microsecond:
        raise ValueError('本版只接受整秒，禁止静默舍入节气边界')
    return local


@lru_cache(maxsize=8192)
def at_local_iso(local_iso):
    local = parse_at(local_iso)
    lunar = Solar.fromYmdHms(local.year, local.month, local.day, local.hour, local.minute, local.second).getLunar()
    return {
        'year': lunar.getYearInGanZhiExact(), 'month': lunar.getMonthInGanZhiExact(),
        'day': lunar.getDayInGanZhiExact2(), 'hour': lunar.getTimeInGanZhi(),
        'lunarCalendarYear': lunar.getYearInGanZhi(),
        'lunarDate': {'year': lunar.getYear(), 'month': lunar.getMonth(), 'day': lunar.getDay()},
        'jieQi': {k: v.toYmdHms() for k, v in lunar.getJieQiTable().items()},
    }


def calculate_calendar(at: str):
    if importlib.metadata.version('lunar_python') != '1.4.8':
        raise RuntimeError('历法版本未固定为1.4.8，需重新核对')
    local = parse_at(at)
    data = at_local_iso(local.isoformat())
    nearby = []
    seen = set()
    for name, value in data['jieQi'].items():
        t = datetime.fromisoformat(value).replace(tzinfo=LOCAL)
        distance = (local - t).total_seconds()
        if abs(distance) <= 120 and value not in seen:
            nearby.append({'name': name, 'calculatedAt': t.isoformat(), 'distanceSeconds': distance,
                           'warning': '接近节气计算时刻；官方外部对照仅到分钟，秒级边界尚未独立核验'})
            seen.add(value)
    pillars = {key: data[key] for key in ('year', 'month', 'day', 'hour')}
    return {'schemaVersion': 1, 'adapterVersion': VERSION,
            'inputAt': at, 'atUtc': local.astimezone(timezone.utc).isoformat(),
            'atResearchTimezone': local.isoformat(), 'policy': POLICY,
            'pillars': pillars, 'lunarDate': data['lunarDate'],
            'lunarCalendarYear': data['lunarCalendarYear'],
            'hourStemReference': 'next_day_late_zi' if local.hour == 23 else 'same_day',
            'nearBoundaryWarnings': nearby,
            'inputPolicySha256': hashlib.sha256(json.dumps(POLICY, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'limits': ['真实本命时地未知的资产不自动套用该时刻作出生盘',
                       '当前输出是指定研究时刻的干支，不含起运/喜用/行情方向',
                       '农历年字段按春节，八字年柱按立春，两者不能互换']}


def calculate_relations(profile_id: str, at: str):
    profiles = json.loads((HERE / 'profiles.json').read_text(encoding='utf-8-sig'))['profiles']
    profile = next((p for p in profiles if p['id'] == profile_id), None)
    if profile is None:
        raise ValueError('未知盘版本')
    calendar = calculate_calendar(at)
    payload = {'schemaVersion': 1, 'profile': profile, 'flow': calendar['pillars'],
               'context': {'kind': 'calculated_research_time', 'atUtc': calendar['atUtc'],
                           'policyId': POLICY['id'], 'flowHourReference': calendar['hourStemReference']}}
    return {'calendar': calendar, 'relationships': calculate(payload), 'marketDirection': None}


def day_label(day: str):
    return calculate_calendar(day + 'T12:00:00+08:00')['pillars']['day']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('at')
    parser.add_argument('--profile')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        value = calculate_relations(args.profile, args.at) if args.profile else calculate_calendar(args.at)
        text = json.dumps(value, ensure_ascii=False, indent=2) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding='utf-8')
        else:
            print(text)
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + '\n')
