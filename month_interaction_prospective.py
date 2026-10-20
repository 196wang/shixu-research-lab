"""Registered future month/day study. Market evaluation is manual and append-only."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import threading

from calendar_adapter import calculate_calendar
from month_study import condition_ids, rule_ids, episode_plan, daily_transitions
from prospective import LOCAL, SYMBOL, validate_candle

START = date(2026, 10, 9)
END = date(2027, 10, 8)
DEADLINE = datetime(2026, 10, 8, tzinfo=LOCAL)
CONDITIONS = ('ALL', 'MONTH-STEM-METALWATER', 'MONTH-BRANCH-METALWATER',
              'MONTH-BOTH-METALWATER', 'MONTH-STEM-FIRE', 'MONTH-BRANCH-FIRE', 'MONTH-BRANCH-寅')
RULES = {'DAY-STEM-METALWATER-UP': 'up', 'DAY-STEM-FIRE-DOWN': 'down'}
PROTOCOL = {
    'id': 'MONTH-DAY-FUTURE-001', 'version': '0.1.0', 'symbol': SYMBOL,
    'startDate': START.isoformat(), 'endDate': END.isoformat(),
    'registrationDeadlineAt': DEADLINE.isoformat(), 'timezone': 'UTC+8',
    'dayBoundary': '00:00 UTC+8', 'monthBoundary': 'exact_jie',
    'boundaryDayPolicy': 'exclude_if_jie_strictly_inside_daily_bar',
    'conditionIds': list(CONDITIONS), 'ruleDirections': RULES,
    'primaryHorizonDays': 1, 'outcome': 'daily_close_vs_open',
    'tiePolicy': 'tie_in_denominator',
    'matchedBaseline': 'same_civil_year_and_same_complete_solar_month_episode_nontrigger_days',
    'episodePolicy': 'report_matched_comparison_only_after_all_eligible_days_scored',
    'historicalSelectionDisclosure': 'all_14_conditions_previously_viewed_in_2021_2026_history',
    'marketValidated': False,
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL)


class MonthInteractionError(RuntimeError):
    pass


class MonthInteractionProspective:
    def __init__(self, data_dir, market):
        self.base = Path(data_dir) / 'month_interaction_future'
        self.market = market
        self.lock = threading.RLock()

    @property
    def registration_path(self):
        return self.base / 'registration.json'

    @property
    def ledger_path(self):
        return self.base / 'evaluations.jsonl'

    def schedule(self):
        episodes, boundaries = episode_plan(START, END)
        rows = []
        day = START
        while day <= END:
            at = midnight(day)
            episode = next((e for e in episodes if datetime.fromisoformat(e['startAt']) <= at < datetime.fromisoformat(e['endAt'])), None)
            if episode is None:
                raise MonthInteractionError('未来节气月不可计算')
            pillars = calculate_calendar(at.isoformat())['pillars']
            if pillars['month'] != episode['pillar']:
                raise MonthInteractionError('日历月柱与节气区间不一致')
            rows.append({'date': day.isoformat(), 'year': day.year, 'monthPillar': pillars['month'],
                         'dayPillar': pillars['day'], 'episodeId': episode['id'],
                         'episodeStartAt': episode['startAt'], 'episodeEndAt': episode['endAt'],
                         'excluded': bool(daily_transitions(day, boundaries)),
                         'conditionIds': [x for x in condition_ids(pillars['month']) if x in CONDITIONS],
                         'ruleIds': [x for x in rule_ids(pillars['day']) if x in RULES]})
            day += timedelta(days=1)
        return rows

    def _read(self):
        if not self.registration_path.exists():
            return None, []
        registration = json.loads(self.registration_path.read_text(encoding='utf-8'))
        if (registration.get('protocol') != PROTOCOL or
                registration.get('scheduleSha256') != sha(registration.get('schedule')) or
                registration.get('registrationSha256') != sha({k:v for k,v in registration.items() if k != 'registrationSha256'})):
            raise MonthInteractionError('登记文件完整性不符')
        rows, previous = [], registration['registrationSha256']
        if self.ledger_path.exists():
            for line in self.ledger_path.read_text(encoding='utf-8').splitlines():
                item = json.loads(line)
                if item.get('previousSha256') != previous or item.get('recordSha256') != sha({k:v for k,v in item.items() if k != 'recordSha256'}):
                    raise MonthInteractionError('逐日记录哈希链不符')
                rows.append(item)
                previous = item['recordSha256']
        if len({x['date'] for x in rows}) != len(rows) or any(x['date'] not in {y['date'] for y in registration['schedule']} for x in rows):
            raise MonthInteractionError('逐日记录日期重复或超出登记范围')
        return registration, rows

    def register(self):
        with self.lock:
            if self.registration_path.exists():
                return self.status()
            now = datetime.now(timezone.utc)
            if now >= DEADLINE.astimezone(timezone.utc):
                raise MonthInteractionError('本期登记已截止，不能追认历史预测')
            schedule = self.schedule()
            registration = {'protocol': PROTOCOL, 'registeredAtUtc': now.isoformat(),
                            'schedule': schedule, 'scheduleSha256': sha(schedule)}
            registration['registrationSha256'] = sha(registration)
            self.base.mkdir(parents=True, exist_ok=True)
            with self.registration_path.open('x', encoding='utf-8') as file:
                file.write(json.dumps(registration, ensure_ascii=False, indent=2) + '\n')
            return self.status()

    def evaluate(self):
        with self.lock:
            registration, recorded = self._read()
            if registration is None:
                raise MonthInteractionError('须先登记固定协议')
            now = datetime.now(timezone.utc)
            _, candles, gaps = self.market._snapshot(SYMBOL)
            seen = {x['date'] for x in recorded}
            previous = recorded[-1]['recordSha256'] if recorded else registration['registrationSha256']
            additions = []
            for scheduled in registration['schedule']:
                day = date.fromisoformat(scheduled['date'])
                if scheduled['excluded'] or scheduled['date'] in seen or now < midnight(day + timedelta(days=1)).astimezone(timezone.utc):
                    continue
                key = int(midnight(day).timestamp() * 1000)
                candle = candles.get(key)
                if validate_candle(candle, key, now) or gaps.get(key):
                    continue
                opened, closed = Decimal(str(candle['open'])), Decimal(str(candle['close']))
                item = {'date': scheduled['date'], 'recordedAtUtc': now.isoformat(),
                        'actual': 'up' if closed > opened else 'down' if closed < opened else 'flat',
                        'open': str(opened), 'close': str(closed),
                        'sourceRef': {'requestId': candle['request_id'], 'bodySha256': candle['body_sha256'],
                                      'updatedAtUtc': candle['updated']}, 'previousSha256': previous}
                item['recordSha256'] = sha(item)
                additions.append(item)
                previous = item['recordSha256']
            if additions:
                with self.ledger_path.open('a', encoding='utf-8') as file:
                    for item in additions:
                        file.write(canonical(item) + '\n')
            return self.status()

    def status(self):
        with self.lock:
            registration, recorded = self._read()
            now = datetime.now(timezone.utc)
            if registration is None:
                return {'protocol': PROTOCOL, 'registered': False, 'canRegister': now < DEADLINE.astimezone(timezone.utc),
                        'scheduleCount': 0, 'scoredCount': 0, 'rows': [], 'summary': [], 'nowUtc': now.isoformat()}
            by_date = {x['date']: x for x in recorded}
            schedule = registration['schedule']
            rows = [{**x, 'result': by_date.get(x['date']), 'status': 'excluded' if x['excluded'] else
                     'scored' if x['date'] in by_date else 'future' if now < midnight(date.fromisoformat(x['date']) + timedelta(days=1)).astimezone(timezone.utc) else 'awaiting_market'} for x in schedule]
            summaries = []
            for condition in CONDITIONS:
                for rule, direction in RULES.items():
                    eligible = [x for x in rows if not x['excluded'] and condition in x['conditionIds']]
                    triggered = [x for x in eligible if rule in x['ruleIds']]
                    scored = [x for x in triggered if x['status'] == 'scored']
                    complete_episodes = []
                    for year, episode_id in {(x['year'], x['episodeId']) for x in eligible}:
                        group = [x for x in eligible if x['year'] == year and x['episodeId'] == episode_id]
                        if group and all(x['status'] == 'scored' for x in group) and now >= datetime.fromisoformat(group[0]['episodeEndAt']).astimezone(timezone.utc):
                            complete_episodes.append(group)
                    matched_n, matched_correct, weighted_baseline, complement_n = 0, 0, 0.0, 0
                    for group in complete_episodes:
                        primary = [x for x in group if rule in x['ruleIds']]
                        complement = [x for x in group if rule not in x['ruleIds']]
                        if not primary or not complement:
                            continue
                        matched_n += len(primary)
                        matched_correct += sum(x['result']['actual'] == direction for x in primary)
                        weighted_baseline += len(primary) * sum(x['result']['actual'] == direction for x in complement) / len(complement)
                        complement_n += len(complement)
                    summaries.append({'conditionId': condition, 'ruleId': rule, 'scheduledTriggerDays': len(triggered),
                                      'scoredTriggerDays': len(scored), 'correct': sum(x['result']['actual'] == direction for x in scored),
                                      'matchedN': matched_n, 'matchedCorrect': matched_correct,
                                      'matchedBaselineRatePct': weighted_baseline / matched_n * 100 if matched_n else None,
                                      'matchedRatePct': matched_correct / matched_n * 100 if matched_n else None,
                                      'complementN': complement_n, 'completeEpisodeCount': len(complete_episodes)})
            return {'protocol': PROTOCOL, 'registered': True, 'registeredAtUtc': registration['registeredAtUtc'],
                    'registrationSha256': registration['registrationSha256'], 'scheduleSha256': registration['scheduleSha256'],
                    'scheduleCount': len(schedule), 'scoredCount': len(recorded), 'rows': rows, 'summary': summaries,
                    'nowUtc': now.isoformat()}
