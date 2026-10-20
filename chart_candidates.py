"""Read-only future chart candidates; stable identities and no automatic registration."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs

from bazi_engine import BRANCH_ELEMENT, stem_info
from calendar_adapter import calculate_calendar
from calendar_browser import JIE, terms_in_year
from prediction_candidates import preview_candidates

HERE = Path(__file__).resolve().parent
VERSION = '0.18.0'
ENGINE_VERSION = '0.19.0'
HORIZONS = (30, 90, 180, 365)
LOCAL = timezone(timedelta(hours=8))
DAY = timedelta(days=1)
SYMBOLS = ('BTCUSDT', 'ETHUSDT')
YIN_RULE_ID = 'DAY-BRANCH-YIN-DOWN'
YIN_SOURCE = '来源溯源/v0.17/月令候选补充.json'
CALCULATION_FILES = (
    'chart_candidates.py', 'prediction_candidates.py', 'calendar_adapter.py', 'calendar_browser.py',
    'bazi_engine.py', 'rule_events.py', 'bazi_features.py', 'gann_timing.py',
    'astro_events.py', 'astro_adapter.py', 'indian_adapter.py', 'indian_math.py', 'prediction_scoring.py',
)
SOURCE_FILES = ('rule_event_catalog.json', 'month_study_protocol.json', YIN_SOURCE,
                '来源溯源/v0.14/江恩计算规范.md')


class ChartCandidatesError(RuntimeError):
    status_code = 503


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def parse_chart_candidates_query(query):
    return parse_chart_candidate_options(query)['symbol']


def parse_chart_candidate_options(query):
    fields = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    if not {'symbol'} <= set(fields) <= {'symbol', 'days'} or any(len(values) != 1 for values in fields.values()) or fields['symbol'][0] not in SYMBOLS:
        raise ValueError('候选图表接受symbol（BTCUSDT或ETHUSDT）及可选days参数')
    value = fields.get('days', ['30'])[0]
    if value not in tuple(map(str, HORIZONS)):
        raise ValueError('未来候选范围可选30、90、180或365天')
    return {'symbol': fields['symbol'][0], 'days': int(value)}


def candidate_key(body):
    note = body.get('note') if isinstance(body, dict) else None
    if not isinstance(note, str):
        return None
    found = re.findall(r'^candidateKey: (CC-[0-9a-f]{32})[ \t]*$', note, re.MULTILINE)
    if len(found) > 1:
        raise ValueError('候选草稿只能包含一个规范candidateKey标记')
    return found[0] if found else None


def candidate_root_conflict(ledger, body):
    """Caller holds ledger.lock through this read AND subsequent create()."""
    key = candidate_key(body)
    if key is None:
        return None
    from prediction_ledger import BODY_FIELDS, normalize_body
    clean = normalize_body(body)
    records = ledger.list()['records']
    matches = [record for record in records if record['symbol'] == clean['symbol'] and candidate_key(record) == key]
    if not matches:
        return None
    roots = {record['rootId'] for record in matches}
    originals = [record for record in records if record['id'] in roots and record['isOriginal']]
    # Identical original body keeps the frozen ledger's existing idempotency.
    if any(clean == {field: original[field] for field in BODY_FIELDS} for original in originals):
        return None
    original = min(originals, key=lambda record: (record['createdAtUtc'], record['id']))
    latest = max((record for record in records if record['rootId'] == original['id']), key=lambda record: record['version'])
    return {'error': '同一候选已有原始登记；新来源或新正文请修订已有记录，不能再增加一个原始样本。',
            'code': 'candidate_already_registered', 'candidateKey': key,
            'existingRecordId': latest['id'], 'rootId': original['id'],
            'root': {field: original[field] for field in ('id', 'version', 'createdAtUtc')},
            'latestRevision': {field: latest[field] for field in ('id', 'version', 'createdAtUtc', 'lateRevision')},
            'revisionPath': '/api/predictions/' + latest['id'] + '/revise'}


def midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL)


def hashes(base, files, warnings):
    result = {}
    for name in files:
        try:
            result[name] = hashlib.sha256((base / name).read_bytes()).hexdigest()
        except OSError:
            result[name] = None
            warnings.append('无法读取候选溯源文件：' + name)
    return result


class FrozenMarket:
    """One immutable asset snapshot shared by metadata and the legacy generator."""
    def __init__(self, market, symbol):
        self.symbol, self.error, self.snapshot = symbol, None, ({}, {}, {})
        try:
            captured = market._snapshot(symbol)
            if not isinstance(captured, (tuple, list)) or len(captured) != 3 or not all(isinstance(p, dict) for p in captured):
                raise ValueError('invalid_snapshot_shape')
            if captured[0].get('symbol') not in (None, symbol):
                raise ValueError('snapshot_asset_mismatch')
            if any(not isinstance(row, dict) or row.get('symbol') != symbol for row in captured[1].values()):
                raise ValueError('snapshot_row_asset_mismatch')
            # Reject non-JSON / nonfinite input before publishing a misleading hash.
            digest(captured)
            self.snapshot = deepcopy(captured)
        except Exception as exc:
            self.error = type(exc).__name__ + ': ' + str(exc)

    def _snapshot(self, symbol):
        if symbol != self.symbol:
            raise ValueError('快照资产与候选资产不一致')
        if self.error:
            raise RuntimeError('本资产行情快照不可用：' + self.error)
        return deepcopy(self.snapshot)


def gann_data_quality(frozen, generated):
    from gann_timing import validate_row
    from prediction_scoring import _bounded_price
    end = generated.astimezone(LOCAL).date() - DAY
    first = max(date(2020, 1, 1), end - 89 * DAY)
    excluded, valid, active, usable = [], 0, 0, 0
    for offset in range((end - first).days + 1):
        day = first + offset * DAY
        key = int(midnight(day).timestamp() * 1000)
        row = deepcopy(frozen.snapshot[1].get(key))
        if isinstance(row, dict):
            for field in ('open', 'high', 'low', 'close'):
                if not _bounded_price(row.get(field)):
                    row[field] = None
        problems, _ = validate_row(row, midnight(day), frozen.symbol, generated)
        extra = frozen.snapshot[2].get(key, [])
        problems.extend(extra if isinstance(extra, list) and all(isinstance(value, str) for value in extra) else ['invalid_gap_metadata'])
        if frozen.error:
            problems.append('snapshot_unavailable')
        if problems:
            excluded.append({'date': day.isoformat(), 'reasons': sorted(set(problems))})
            active = 0
        else:
            valid += 1
            active += 1
            if active == 5:
                usable += 1
    requested = (end - first).days + 1
    return {'range': {'startDate': first.isoformat(), 'endDate': end.isoformat()},
            'requestedDays': requested, 'validDays': valid, 'excludedDays': len(excluded),
            'usableSegmentCount': usable, 'hasUsableSegment': bool(usable), 'excluded': excluded,
            'status': 'unavailable' if not valid else 'insufficient' if not usable else 'partial' if excluded else 'available'}


def semantic_evidence(candidate):
    """Stable mathematical identity, excluding capture time, row hashes and pair counters."""
    evidence = candidate.get('evidence') or {}
    family = candidate['family']
    if family == 'GANN':
        projection = evidence.get('projection') or {}
        return {'anchorAId': projection.get('anchorAId'), 'anchorBId': projection.get('anchorBId'),
                'anchorADate': projection.get('anchorADate'), 'anchorBDate': projection.get('anchorBDate'),
                'intervalDays': projection.get('intervalDays'), 'multiplier': projection.get('multiplier')}
    if family == 'ASTROLOGY':
        event = evidence.get('event') or {}
        return {key: event.get(key) for key in ('id', 'kind', 'atUtc', 'atLocal', 'phaseAngleDeg', 'boundaryLongitudeDeg', 'from', 'to')}
    return {key: evidence.get(key) for key in ('triggerRuleId', 'profile', 'dayPillar', 'dayBranch', 'calendarPolicy') if key in evidence}


def normalize_candidate(candidate, symbol, generated, first, last, provenance=None):
    item = deepcopy(candidate)
    draft = deepcopy(item['draft'])
    if draft.get('symbol') != symbol:
        raise ValueError('候选草稿资产不匹配')
    kind = draft.get('kind')
    if kind not in ('up', 'down', 'time') or draft.get('horizonDays') != 1:
        raise ValueError('候选仅支持当日上涨/下跌或中性时间窗口')
    target = date.fromisoformat(draft['targetDate'])
    if not first <= target <= last:
        raise ValueError('候选目标日期超出服务器指定的未来窗口')
    rules = draft.get('rules', [])
    if len(rules) != 1 or rules[0].get('family') != item['family'] or rules[0].get('direction') != kind:
        raise ValueError('候选规则语义不一致')
    if item['family'] in ('GANN', 'ASTROLOGY') and kind != 'time':
        raise ValueError('江恩和星象候选只能是不带方向的时间观察')
    if item['family'] == 'BAZI' and symbol != 'BTCUSDT':
        raise ValueError('ETH不得套用BTC八字方向候选')
    semantic = semantic_evidence(item)
    identity = {'symbol': symbol, 'ruleId': rules[0]['id'], 'ruleVersion': rules[0]['version'],
                'targetDate': target.isoformat(), 'kind': kind, 'semanticEvidence': semantic}
    stable_id = 'CC-' + digest(identity)[:32]
    # Keep dynamic provenance outside the stable registration body. The full
    # evidence is still available in this exported, timestamped preview.
    draft['note'] = (item['reason'] + '\n候选草稿；尚未验证，需显式确认登记。'
                     + '\ncandidateKey: ' + stable_id + '\n语义证据：'
                     + json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    source_evidence = item.get('evidence') or {}
    stable_provenance = deepcopy(provenance or {})
    if item['family'] == 'GANN':
        projection = source_evidence.get('projection') or {}
        stable_provenance['knownAtUtc'] = projection.get('knownAtUtc', projection.get('knownAt'))
        stable_provenance['settings'] = source_evidence.get('settings')
        stable_provenance['lookback'] = source_evidence.get('lookback')
        stable_provenance['selectedMarketSources'] = [
            {k: source.get(k) for k in ('id', 'symbol', 'marketGeneration', 'marketAsOfUtc', 'snapshotSha256', 'sha256') if k in source}
            for source in source_evidence.get('sources') or [] if isinstance(source, dict)]
    else:
        stable_provenance.pop('marketSnapshotSha256', None)
    if item['family'] == 'ASTROLOGY':
        stable_provenance['eventEngineSourceHashes'] = source_evidence.get('sourceHashes')
    draft['note'] += '\n稳定来源指纹：' + json.dumps(stable_provenance, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(draft['note']) > 8000:
        raise ValueError('候选草稿注释超过登记长度限制')
    offset = 3 if kind == 'time' else 0
    deadline = midnight(target - offset * DAY)
    allowed = generated < deadline
    item.update({'legacyId': item.get('id'), 'id': stable_id, 'candidateKey': stable_id,
                 'kind': kind, 'targetDate': target.isoformat(),
                 'windowStartDate': (target - offset * DAY).isoformat(),
                 'windowEndDate': (target + offset * DAY).isoformat(),
                 'ruleId': rules[0]['id'], 'ruleVersion': rules[0]['version'], 'status': 'candidate',
                 'canRegister': allowed, 'registrationDeadlineAt': deadline.isoformat(),
                 'blockedReason': None if allowed else '观察窗口已开始：仅可查看，不能补登记为事前预测。',
                 'evidenceHash': digest(item.get('evidence')), 'semanticEvidence': semantic,
                 'registrationProvenance': stable_provenance,
                 'draft': draft, 'marketValidated': False, 'registeredPrediction': False})
    return item


def day_context(day, boundaries):
    at = midnight(day)
    item = {'date': day.isoformat(), 'atLocal': at.isoformat(), 'pillars': None,
            'monthStemElement': None, 'monthBranchElement': None, 'dayStemElement': None, 'dayBranchElement': None,
            'calendarAvailable': False, 'calendarErrors': [], 'solarMonthTransitions': [], 'hasMonthTransition': False}
    try:
        calculated = calculate_calendar(at.isoformat())
        pillars = {key: calculated['pillars'][key] for key in ('year', 'month', 'day')}
        item.update({'pillars': pillars, 'calendarAvailable': True,
                     'monthStemElement': stem_info(pillars['month'][0])['element'],
                     'monthBranchElement': BRANCH_ELEMENT[pillars['month'][1]],
                     'dayStemElement': stem_info(pillars['day'][0])['element'],
                     'dayBranchElement': BRANCH_ELEMENT[pillars['day'][1]]})
    except Exception as exc:
        item['calendarErrors'].append('零点历法不可用：' + type(exc).__name__)
    for term, boundary in boundaries:
        # A midnight transition belongs to the new day and is still shown.
        if at <= boundary < at + DAY:
            try:
                before = calculate_calendar((boundary - timedelta(seconds=1)).isoformat())['pillars']['month']
                after = calculate_calendar(boundary.isoformat())['pillars']['month']
                if before != after:
                    item['solarMonthTransitions'].append({'term': term, 'at': boundary.isoformat(),
                        'beforePillar': before, 'afterPillar': after, 'changesMonth': True,
                        'precisionNote': '固定历法库计算到秒；临界秒未做独立外部核验'})
            except Exception as exc:
                item['calendarErrors'].append('交节历法不可用：' + type(exc).__name__)
    item['hasMonthTransition'] = bool(item['solarMonthTransitions'])
    return item


def yin_candidate(symbol, day):
    pillar = day['pillars']['day']
    reason = '日支为寅；下跌方向是独立待检验假设。月份仅作背景，寅日研究没有证实稳定预测能力。'
    label = '寅日下跌实验假设 · ' + pillar
    evidence = {'triggerRuleId': YIN_RULE_ID, 'profile': 'BTC-BZ-V01', 'dayPillar': pillar,
                'dayBranch': '寅', 'calendarPolicy': '北京时间每日00:00；日支口径',
                'sourceStudyId': 'MONTH-BTC-001', 'sourceCandidateId': 'ML-C03', 'sourcePath': YIN_SOURCE,
                'interpretation': '从来源线索拆分的项目假设；不是经证实的传统规则或交易信号'}
    return {'id': None, 'family': 'BAZI', 'label': label, 'reason': reason, 'evidence': evidence,
            'draft': {'symbol': symbol, 'kind': 'down', 'targetDate': day['date'], 'horizonDays': 1,
                      'title': label, 'note': reason, 'rules': [{'id': YIN_RULE_ID, 'family': 'BAZI',
                      'direction': 'down', 'version': VERSION}], 'invalidation': None}}


def group_day(day, candidates):
    items = [item for item in candidates if item['targetDate'] == day['date']]
    grouped = {}
    for item in items:
        grouped.setdefault((item['family'], item['kind']), []).append(item)
    agreements = []
    for (family, kind), members in sorted(grouped.items()):
        rules = sorted({item['ruleId'] for item in members})
        if len(rules) >= 2:
            agreements.append({'family': family, 'kind': kind, 'ruleIds': rules,
                               'candidateIds': [item['id'] for item in members]})
    up = [item['id'] for item in items if item['kind'] == 'up']
    down = [item['id'] for item in items if item['kind'] == 'down']
    day.update({'candidateIds': [item['id'] for item in items], 'upIds': up, 'downIds': down,
                'timeIds': [item['id'] for item in items if item['kind'] == 'time'],
                'hasDirectionConflict': bool(up and down), 'sameFamilyAgreement': bool(agreements),
                'crossFamilyTimingOverlap': len({item['family'] for item in items if item['kind'] == 'time'}) >= 2,
                'agreementGroups': agreements})
    return day


def chart_candidates(market, symbol, clock=None, base=None, days=30):
    if symbol not in SYMBOLS:
        raise ValueError('候选图表仅支持BTCUSDT或ETHUSDT')
    if type(days) is not int or days not in HORIZONS:
        raise ValueError('未来候选范围可选30、90、180或365天')
    generated = clock() if clock else datetime.now(timezone.utc)
    if not isinstance(generated, datetime) or generated.tzinfo is None or generated.utcoffset() is None:
        raise ValueError('候选计算时间必须有明确时区')
    generated = generated.astimezone(timezone.utc)
    first = generated.astimezone(LOCAL).date() + DAY
    last = first + (days - 1) * DAY
    if first.year < 2020 or last.year > 2099:
        raise ChartCandidatesError('未来候选窗口超出固定历法范围2020–2099')
    base = Path(base or HERE)
    warnings = []
    calculation_hashes = hashes(base, CALCULATION_FILES, warnings)
    source_hashes = hashes(base, SOURCE_FILES, warnings)
    frozen = FrozenMarket(market, symbol)
    gann_quality = gann_data_quality(frozen, generated)
    if frozen.error:
        warnings.append('本资产行情缓存不可用；保留可独立计算的历法/星象候选。' + frozen.error)
    elif not frozen.snapshot[1]:
        warnings.append('本资产尚无行情日线缓存；江恩可能没有候选。历法与星象不依赖行情方向。')
    if not gann_quality['hasUsableSegment']:
        warnings.append('江恩回看数据不可用：最近' + str(gann_quality['requestedDays']) + '天仅'
                        + str(gann_quality['validDays']) + '天有效，没有连续5根完整日线；不能将空列表解释为无时间信号。')
    elif gann_quality['excludedDays']:
        warnings.append('江恩回看数据不完整：最近' + str(gann_quality['requestedDays']) + '天有'
                        + str(gann_quality['excludedDays']) + '天无效，缺失处断开转点配对。')
    # Preserve the existing 31-day generator and its semantics. Every chunk
    # shares one observation time and one immutable market snapshot.
    raw, chunks = [], []
    for offset in range(0, days, 30):
        chunk_first = first + offset * DAY
        chunk_last = min(last, chunk_first + 29 * DAY)
        chunk = {'startDate': chunk_first.isoformat(), 'endDate': chunk_last.isoformat()}
        try:
            preview = preview_candidates(frozen, symbol, chunk['startDate'], chunk['endDate'], clock=lambda: generated)
            raw.extend(preview['candidates'])
            warnings.extend(preview['warnings'])
            chunk['status'] = 'generated'
        except Exception as exc:
            chunk['status'] = 'unavailable'
            warnings.append(chunk['startDate'] + '至' + chunk['endDate'] + '候选生成器暂不可用，本次不补造其结果：' + type(exc).__name__)
        chunks.append(chunk)
    boundaries = []
    for year in range(first.year, last.year + 1):
        try:
            boundaries.extend((name, at) for name, at in terms_in_year(year) if name in JIE)
        except Exception as exc:
            warnings.append(str(year) + '年交节表不可用：' + type(exc).__name__)
    days = [day_context(first + offset * DAY, boundaries) for offset in range(days)]
    if symbol == 'BTCUSDT' and source_hashes[YIN_SOURCE]:
        raw.extend(yin_candidate(symbol, day) for day in days if day['calendarAvailable'] and day['pillars']['day'][1] == '寅')
    elif symbol == 'BTCUSDT':
        warnings.append('寅日假设来源缺失；本次不生成寅日方向候选。')
    candidates = {}
    source_hashes['marketSnapshotSha256'] = digest(frozen.snapshot) if not frozen.error else None
    provenance = {'calculationHashes': calculation_hashes,
                  'theorySourceHashes': {key: value for key, value in source_hashes.items() if key != 'marketSnapshotSha256'},
                  'marketSnapshotSha256': source_hashes['marketSnapshotSha256']}
    for item in raw:
        try:
            normalized = normalize_candidate(item, symbol, generated, first, last, provenance)
            candidates.setdefault(normalized['id'], normalized)
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append('跳过语义不完整或资产不匹配的候选：' + type(exc).__name__)
    candidates = sorted(candidates.values(), key=lambda item: (item['targetDate'], item['family'], item['ruleId'], item['id']))
    for day in days: group_day(day, candidates)
    if any(day['calendarErrors'] for day in days):
        warnings.append('部分日期历法字段不可用；请查看各日calendarErrors，不将缺失当成无条件。')
    state = frozen.snapshot[0]
    return {'schemaVersion': 1, 'engineVersion': ENGINE_VERSION, 'generatedAtUtc': generated.isoformat(), 'symbol': symbol,
            'range': {'startDate': first.isoformat(), 'endDate': last.isoformat(), 'timeZone': 'UTC+8', 'dayCount': len(days)},
            'generationChunks': chunks,
            'horizonPolicy': 'calendar_and_rule_projection_from_one_current_snapshot_not_price_forecast',
            'marketAsOfUtc': state.get('last_success'), 'marketGeneration': state.get('generation'),
            'marketAvailable': bool(frozen.snapshot[1]) and frozen.error is None,
            'gannAvailable': gann_quality['hasUsableSegment'], 'gannDataQuality': gann_quality,
            'calculationHashes': calculation_hashes, 'sourceHashes': source_hashes,
            'warnings': list(dict.fromkeys(warnings)), 'candidates': candidates, 'days': days,
            'marketValidated': False, 'registeredPrediction': False,
            'groupingPolicy': 'same_target_date_same_family_and_kind_unique_rule_ids_no_statistical_independence_claim',
            'monthContextPolicy': 'midnight_pillars_with_exact_intraday_jie_transitions_background_only_no_score_or_filter',
            'registrationPolicy': 'explicit_existing_prediction_ledger_only_recheck_deadline_at_submission',
            'note': '候选尚未登记、未经验证；月份背景和多个条件重叠不增加置信度，不自动给出高低点或交易建议。'}
