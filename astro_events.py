"""Read-only geocentric phase/27-nakshatra events, with explicit numerical limits.

Date ranges are UTC+8 local dates. No database, market feed, natal assumption,
direction classifier or score is read or written by this module.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
from importlib import metadata
import json
import math
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
VERSION = '0.14.0'
LOCAL = timezone(timedelta(hours=8))
UTC = timezone.utc
STEP_SECONDS = 6 * 3600
TOLERANCE_SECONDS = 0.001
NAKSHATRA_WIDTH = 360.0 / 27
PHASES = ((0, '新月'), (90, '上弦月'), (180, '满月'), (270, '下弦月'))
SOURCES = [
    {'id': 'astronomy-engine-python', 'title': 'Astronomy Engine Python API: MoonPhase / SearchMoonPhase',
     'url': 'https://github.com/cosinekitty/astronomy/blob/master/source/python/README.md',
     'usage': '月相定义与搜索 API；实际执行本地固定 2.1.19 版本'},
    {'id': 'swiss-programmer', 'title': 'Swiss Ephemeris programmer documentation',
     'url': 'https://www.astro.com/swisseph/swephprg.htm',
     'usage': 'UTC 转换、Moshier、SIDEREAL 和 Lahiri 模式配置'},
]


class AstroEventsUnavailable(RuntimeError):
    """Dependencies/configuration/calculation unavailable; HTTP callers use 503."""


def _parse_range(start: str, end: str):
    values = []
    for value in (start, end):
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('日期必须为 YYYY-MM-DD')
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError('日期无效') from exc
        if not 1992 <= parsed.year <= 2099:
            raise ValueError('星象事件支持北京时间 1992–2099 年')
        values.append(parsed)
    first, last = values
    days = (last - first).days + 1
    if not 1 <= days <= 62:
        raise ValueError('起止日期须正序，含首尾最多 62 天')
    lo = datetime.combine(first, time(), LOCAL)
    hi = datetime.combine(last + timedelta(days=1), time(), LOCAL)
    return lo.astimezone(UTC), hi.astimezone(UTC), {
        'startDate': start, 'endDate': end, 'days': days, 'timeZone': 'UTC+8',
        'startAtLocal': lo.isoformat(), 'endExclusiveAtLocal': hi.isoformat(),
        'startAtUtc': lo.astimezone(UTC).isoformat(),
        'endExclusiveAtUtc': hi.astimezone(UTC).isoformat(), 'boundary': '[start,end)',
    }


def _load_engines():
    try:
        if str(HERE / 'vendor') not in sys.path:
            sys.path.insert(0, str(HERE / 'vendor'))
        import astronomy
        import indian_adapter as indian
        if metadata.version('astronomy-engine') != '2.1.19':
            raise RuntimeError('Astronomy Engine 版本不符合固定配置 2.1.19')
        swiss_info = indian.engine_info()
        return astronomy, indian, {
            'astronomy': {'name': 'Astronomy Engine', 'version': '2.1.19', 'license': 'MIT'},
            'swiss': swiss_info,
        }
    except Exception as exc:
        raise AstroEventsUnavailable('星象事件依赖不可用或版本不匹配：' + str(exc)) from exc


def _angle_offset(value: float, target: float) -> float:
    if not math.isfinite(value) or not 0 <= value < 360:
        raise AstroEventsUnavailable('星位角度输出异常')
    return (value - target + 180.0) % 360.0 - 180.0


def _bisect_crossing(longitude, left: datetime, right: datetime, target: float):
    """A rising crossing, represented by its upper numerical bracket endpoint."""
    a = _angle_offset(longitude(left), target)
    b = _angle_offset(longitude(right), target)
    if not (-20 < a < 0 <= b < 20):
        raise AstroEventsUnavailable('星象事件未得到连续的正向夹逼区间')
    while (right - left).total_seconds() > TOLERANCE_SECONDS:
        mid = left + (right - left) / 2
        value = _angle_offset(longitude(mid), target)
        if value >= 0:
            right = mid
        else:
            left = mid
    return right, (right - left).total_seconds()


def _grid_floor(at: datetime) -> datetime:
    return datetime.fromtimestamp(math.floor(at.timestamp() / STEP_SECONDS) * STEP_SECONDS, UTC)


def _nakshatra_crossings(longitude, start: datetime, end: datetime):
    """UTC six-hour global brackets make adjacent queries use identical roots."""
    step = timedelta(seconds=STEP_SECONDS)
    left = _grid_floor(start) - step
    left_lon = longitude(left)
    result = []
    while left < end:
        right = left + step
        right_lon = longitude(right)
        _angle_offset(left_lon, 0)
        _angle_offset(right_lon, 0)
        movement = (right_lon - left_lon) % 360.0
        # The Moon is prograde and moves much less than a nakshatra per 6 h.
        # Fail explicitly if those assumptions are not met by the dependency.
        if not 0 < movement < 10:
            raise AstroEventsUnavailable('月亮六小时位移异常，拒绝跨越不连续角度')
        first = math.floor(left_lon / NAKSHATRA_WIDTH) + 1
        last = math.floor((left_lon + movement) / NAKSHATRA_WIDTH)
        for number in range(first, last + 1):
            index = number % 27
            target = index * NAKSHATRA_WIDTH
            at, width = _bisect_crossing(longitude, left, right, target)
            if start <= at < end:
                result.append((at, index, width))
        left, left_lon = right, right_lon
    return result


def _astro_time(astronomy, at: datetime):
    value = at.astimezone(UTC)
    return astronomy.Time.Make(value.year, value.month, value.day, value.hour, value.minute,
                               value.second + value.microsecond / 1e6)


def _phase_crossings(astronomy, start: datetime, end: datetime):
    result = []
    step = timedelta(seconds=STEP_SECONDS)

    def longitude(at):
        return astronomy.MoonPhase(_astro_time(astronomy, at))

    for angle, label in PHASES:
        cursor = start - timedelta(days=1)
        ceiling = end + timedelta(days=1)
        for _ in range(6):  # At most 3 occurrences of any phase in a 64-day span.
            if cursor >= ceiling:
                break
            found = astronomy.SearchMoonPhase(angle, _astro_time(astronomy, cursor),
                                               (ceiling - cursor).total_seconds() / 86400)
            if found is None:
                break
            candidate = found.Utc().replace(tzinfo=UTC)
            if not cursor <= candidate <= ceiling:
                raise AstroEventsUnavailable('月相搜索返回超出搜索区间的时刻')
            # SearchMoonPhase finds each occurrence. Re-bracket on a global UTC
            # grid so the same event has identical timestamps in adjacent queries.
            grid = _grid_floor(candidate)
            refined = None
            for offset in (-1, 0, 1):
                left = grid + offset * step
                right = left + step
                a = _angle_offset(longitude(left), angle)
                b = _angle_offset(longitude(right), angle)
                if -20 < a < 0 <= b < 20:
                    refined = _bisect_crossing(longitude, left, right, angle)
                    break
            if refined is None:
                raise AstroEventsUnavailable('月相搜索结果无法复核角度过界')
            at, width = refined
            if start <= at < end:
                result.append((at, angle, label, width))
            cursor = candidate + timedelta(days=1)
        else:
            raise AstroEventsUnavailable('月相搜索次数异常')
    return result


def _event(kind, at, token, label, width, engine_key):
    analysis_at = at.replace(microsecond=0)
    if at.microsecond:
        analysis_at += timedelta(seconds=1)
    return {
        'id': f'AE14-{kind}-{token}-{at.astimezone(UTC).date().isoformat()}',
        'kind': kind, 'label': label, 'atUtc': at.astimezone(UTC).isoformat(timespec='microseconds'),
        'atLocal': at.astimezone(LOCAL).isoformat(timespec='microseconds'),
        'analysisAtLocal': analysis_at.astimezone(LOCAL).isoformat(timespec='seconds'),
        'direction': None, 'from': None, 'to': None, 'phaseAngleDeg': None,
        'boundaryLongitudeDeg': None, 'engineKey': engine_key,
        'numerical': {'bracketWidthSeconds': width, 'toleranceSeconds': TOLERANCE_SECONDS,
                      'timeMeaning': 'upper_bracket_endpoint_not_accuracy_guarantee'},
    }


def _deduplicate(events):
    unique = {}
    for event in events:
        prior = unique.get(event['id'])
        if prior is not None and prior != event:
            raise AstroEventsUnavailable('同一星象事件出现相互冲突的重复记录')
        unique[event['id']] = event
    return sorted(unique.values(), key=lambda item: (item['atUtc'], item['kind'], item['id']))


def _hash_file(relative):
    return hashlib.sha256((HERE / relative).read_bytes()).hexdigest()


def calculate_astro_events(start: str, end: str) -> dict:
    lo, hi, range_info = _parse_range(start, end)
    astronomy, indian, engines = _load_engines()
    configuration = {
        'id': 'ASTRO-EVENTS-GEOCENTRIC-V14', 'version': VERSION, 'origin': 'geocentric',
        'rangeTimeZone': 'UTC+8', 'rangeBoundary': '[start_midnight,end_plus_one_midnight)',
        'dateRange': ['1992-01-01', '2099-12-31'], 'maxInclusiveDays': 62,
        'moonPhase': {'engineVersion': '2.1.19', 'anglesDeg': [0, 90, 180, 270],
                      'algorithm': 'SearchMoonPhase_then_global_6h_bracket_bisection_of_MoonPhase',
                      'searchToleranceSeconds': 0.1, 'definition': 'geocentric_Moon_minus_Sun_ecliptic_longitude'},
        'nakshatra': {'engineVersion': '2.10.03', 'wrapperVersion': '2.10.3.6',
                      'zodiac': 'sidereal', 'ayanamsa': 'Lahiri', 'ayanamsaMode': 1,
                      'ayanamsaExtraBits': 0, 'backend': 'Moshier', 'count': 27,
                      'widthDeg': NAKSHATRA_WIDTH, 'names': list(indian.NAMES),
                      'flagsRequested': indian.FLAGS, 'indexBase': 0,
                      'algorithm': 'global_6h_forward_longitude_unwrap_then_bisection',
                      'reset': 'indian_adapter.LOCK; swe.close(); set_sid_mode(LAHIRI,0,0)'},
        'numerical': {'sampleHours': 6, 'refinementToleranceSeconds': TOLERANCE_SECONDS,
                      'eventTime': 'upper_bracket_endpoint', 'inclusionUses': 'unrounded_event_time',
                      'analysisLinkTime': 'ceil_to_integral_second'},
    }
    try:
        events = []
        for at, angle, label, width in _phase_crossings(astronomy, lo, hi):
            event = _event('moon_phase', at, str(angle), label, width, 'astronomy')
            event['phaseAngleDeg'] = angle
            events.append(event)
        returned_flags, messages = set(), set()
        with indian.LOCK:
            swe = indian.swe
            swe.close()
            swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)

            def moon_longitude(at):
                at = at.astimezone(UTC)
                _, jd_ut = swe.utc_to_jd(at.year, at.month, at.day, at.hour, at.minute,
                                         at.second + at.microsecond / 1e6, swe.GREG_CAL)
                coords, flags, message = swe.calc_ut(jd_ut, swe.MOON, indian.FLAGS)
                if flags & 7 != swe.FLG_MOSEPH or not flags & swe.FLG_SIDEREAL or not flags & swe.FLG_SPEED:
                    raise AstroEventsUnavailable('星历返回标志不符合固定 Lahiri/Moshier 配置')
                if not all(math.isfinite(x) for x in coords) or not 0 <= coords[0] < 360 or coords[3] <= 0:
                    raise AstroEventsUnavailable('月亮星位或速度输出异常')
                returned_flags.add(flags)
                if message:
                    messages.add(message)
                return coords[0]

            for at, index, width in _nakshatra_crossings(moon_longitude, lo, hi):
                name = indian.NAMES[index]
                event = _event('nakshatra_ingress', at, str(index), '月亮进入 ' + name, width, 'swiss')
                before = (index - 1) % 27
                event['from'] = {'index': before, 'name': indian.NAMES[before]}
                event['to'] = {'index': index, 'name': name}
                event['boundaryLongitudeDeg'] = index * NAKSHATRA_WIDTH
                events.append(event)
        engines['swiss']['returnedFlags'] = sorted(returned_flags)
        engines['swiss']['messages'] = sorted(messages)
        hashes = {name: _hash_file(name) for name in (
            'astro_events.py', 'astro_adapter.py', 'indian_adapter.py', 'indian_math.py',
            'vendor/astronomy/__init__.py', 'vendor/astronomy/astronomy.py',
            'vendor/astronomy_engine-2.1.19.dist-info/METADATA',
            'vendor/pysweph-2.10.3.6.dist-info/METADATA')}
        hashes['swissBinary'] = engines['swiss']['binarySha256']
        events = _deduplicate(events)
    except AstroEventsUnavailable:
        raise
    except Exception as exc:
        raise AstroEventsUnavailable('星象事件计算不可用：' + str(exc)) from exc
    count_phase = sum(e['kind'] == 'moon_phase' for e in events)
    return {
        'schemaVersion': 1, 'version': VERSION, 'available': True,
        'generatedAtUtc': datetime.now(UTC).isoformat(), 'range': range_info,
        'events': events, 'counts': {'moon_phase': count_phase,
                                    'nakshatra_ingress': len(events) - count_phase, 'total': len(events)},
        'engines': engines, 'configuration': configuration,
        'configurationSha256': hashlib.sha256(json.dumps(configuration, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest(),
        'sourceHashes': hashes, 'sources': SOURCES,
        'precision': {
            'note': '毫秒级夹逼仅为本程序数值停止条件，不是天文时刻的误差保证；独立参考对照另行记录。',
            'externalEphemerisPrecisionValidated': False,
            'timeScaleNote': 'Astronomy Engine 的 UT/TT 与默认 Delta-T；Swiss utc_to_jd 的内置闰秒和 Delta-T；未接入实时 EOP。',
        },
        'limits': [
            '月相与月宿来自两套固定引擎；月宿为 Lahiri 恒星黄道 27 宿，不是中国 28 宿。',
            '所有星位以地心为参考，不计算观察地点、宫位或资产出生星盘。',
            '事件时刻是计算结果；边界附近应查看完整时刻，页面联动向上取整到秒。',
            '这些是天文/历法事件，无涨跌方向、交易信号或行情验证。',
        ],
        'marketDirection': None, 'marketValidated': False,
    }
