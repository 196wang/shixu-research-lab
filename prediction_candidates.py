"""Current-time, read-only candidate drafts; no registration and no future prices."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

LOCAL = timezone(timedelta(hours=8))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def preview_candidates(market, symbol, start, end, clock=None):
    if symbol not in ('BTCUSDT', 'ETHUSDT'):
        raise ValueError('候选仅支持 BTCUSDT / ETHUSDT')
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise ValueError('候选日期应为 YYYY-MM-DD') from None
    if first.isoformat() != start or last.isoformat() != end or not 2020 <= first.year <= last.year <= 2099 or not 0 <= (last-first).days < 31:
        raise ValueError('候选预览每次 1–31 个自然日，范围 2020–2099 年')
    now = clock() if clock else datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('候选计算时刻必须有时区')
    candidates, warnings = [], []

    def add(family, kind, target, label, reason, rule_id, evidence):
        target_day = date.fromisoformat(target)
        deadline = datetime.combine(target_day - timedelta(days=3 if kind == 'time' else 0), datetime.min.time(), LOCAL)
        allowed = now < deadline
        draft = {'symbol': symbol, 'kind': kind, 'targetDate': target, 'horizonDays': 1,
                 'title': label, 'note': reason + '\n候选草稿；未验证。来源证据摘要：' + json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                 'rules': [{'id': rule_id, 'family': family, 'direction': kind, 'version': '0.15.0'}], 'invalidation': None}
        candidates.append({'id': digest({'draft': draft, 'family': family})[:24], 'label': label,
                           'family': family, 'reason': reason, 'canRegister': allowed,
                           'blockedReason': None if allowed else '观察窗口已开始：仅可查看，不能补登记为事前预测。',
                           'registrationDeadlineAt': deadline.isoformat(), 'draft': draft, 'evidence': evidence})

    if symbol == 'BTCUSDT':
        from rule_events import matches_at
        for offset in range((last-first).days+1):
            day = (first + timedelta(days=offset)).isoformat()
            result = matches_at(day + 'T00:00:00+08:00')
            for match in result['matches']:
                if match['ruleId'] not in ('BZ-E01', 'BZ-E02'):
                    continue
                kind = 'up' if match['ruleId'] == 'BZ-E01' else 'down'
                add('BAZI', kind, day, ('金水日上涨假设' if kind == 'up' else '火日下跌假设') + ' · ' + match['dayPillar'],
                    match['reason'] + ' 本次新增方向假设按当日开盘至收盘检验；既有历史研究未证实可稳定预测。',
                    'BZ-DAY-METALWATER-UP' if kind == 'up' else 'BZ-DAY-FIRE-DOWN',
                    {'triggerRuleId': match['ruleId'], 'profile': 'BTC-BZ-V01', 'dayPillar': match['dayPillar'],
                     'calendarPolicy': '北京时间每日00:00；日干口径'})
    else:
        warnings.append('ETH 尚无经明确登记的八字方向映射；此处不套用 BTC 金水/火日假设。')

    from gann_timing import calculate_gann, GannTimingError
    lookback_end = now.astimezone(LOCAL).date() - timedelta(days=1)
    lookback_start = max(date(2020, 1, 1), lookback_end-timedelta(days=89))
    try:
        gann = calculate_gann(market, symbol, lookback_start.isoformat(), lookback_end.isoformat(), clock=lambda: now)
        for p in gann['projections']:
            if start <= p['targetDate'] <= end and p.get('status') == 'future_candidate':
                add('GANN', 'time', p['targetDate'], '江恩等距时间候选',
                    '已确认的相邻异向转点间距外推，仅假设附近出现转点，不预设高点、低点或涨跌方向。',
                    'GANN-EQUAL-INTERVAL', {'projection': p, 'lookback': [lookback_start.isoformat(), lookback_end.isoformat()],
                                            'sources': gann.get('sources'), 'codeSha256': gann.get('codeSha256'), 'settings': gann.get('settings')})
    except GannTimingError as exc:
        warnings.append('江恩候选暂不可用：' + str(exc))
    from astro_events import calculate_astro_events
    try:
        astro = calculate_astro_events(start, end)
        for event in astro['events']:
            target = event['atLocal'][:10]
            add('ASTROLOGY', 'time', target, event['label'] + ' · 时间观察',
                '天文事件时间可计算；是否对应市场转点是待检验假设。本候选不带方向。',
                'ASTRO-MOON-PHASE' if event['kind'] == 'moon_phase' else 'ASTRO-NAKSHATRA-INGRESS',
                {'event': event, 'engines': astro.get('engines'), 'sourceHashes': astro.get('sourceHashes')})
    except (ImportError, RuntimeError) as exc:
        warnings.append('星象候选暂不可用：' + str(exc))
    candidates.sort(key=lambda c: (c['draft']['targetDate'], c['family'], c['id']))
    return {'schemaVersion': 1, 'generatedAtUtc': now.astimezone(timezone.utc).isoformat(),
            'symbol': symbol, 'range': {'startDate': start, 'endDate': end}, 'candidates': candidates,
            'warnings': warnings, 'marketValidated': False,
            'note': '预览不会写入账本。确认登记后才进入事前记录；中性时间事件不能当作看涨、看跌或高低点结论。'}
