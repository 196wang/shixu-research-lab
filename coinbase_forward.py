"""One pre-registered Coinbase BTC/USD future cohort with append-only reviews."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from threading import RLock
import hashlib
import json

from astro_events import calculate_astro_events
from calendar_adapter import calculate_calendar
from coinbase_study import inspect, timing_label
from exploration_generators import sequential_gann
from gann_timing import snapshot_hash
from month_study import daily_transitions, episode_plan, rule_ids
from coinbase_feed import local_open, ms

HERE = Path(__file__).resolve().parent
LOCAL = timezone(timedelta(hours=8))
DAY = timedelta(days=1)
PROTOCOL = HERE / 'coinbase_forward_protocol.json'
PUBLIC_REGISTRATION = Path('public_data/coinbase_forward_registration.json')
RULES = {'DAY-STEM-METALWATER-UP': 'up', 'DAY-STEM-FIRE-DOWN': 'down',
         'DAY-BRANCH-YIN-DOWN': 'down'}
SOURCES = ('coinbase_forward.py', 'coinbase_study.py', 'exploration_generators.py',
           'month_study.py', 'calendar_adapter.py', 'calendar_browser.py',
           'astro_events.py', 'prediction_scoring.py', 'coinbase_feed.py')


class CoinbaseForwardError(RuntimeError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def seal(value):
    value['contentSha256'] = digest(value)
    return value


def verify(value):
    if value.get('contentSha256') != digest({k: v for k, v in value.items() if k != 'contentSha256'}):
        raise CoinbaseForwardError('前瞻登记或复核文件哈希不符', 503)


def days(first, last):
    return [first + i * DAY for i in range((last - first).days + 1)]


class CoinbaseForward:
    def __init__(self, data_dir, market, clock=None, base=HERE):
        self.base = Path(base)
        self.directory = Path(data_dir) / 'coinbase_forward'
        self.public_audit_directory = Path(data_dir) / 'public_forward_audit'
        self.market = market
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = RLock()
        self.last_auto_error = None
        self.last_public_auto_error = None
        self.last_public_auto_attempt = None

    def now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise CoinbaseForwardError('系统时钟缺少时区', 503)
        return value.astimezone(timezone.utc)

    def protocol(self):
        value = json.loads((self.base / PROTOCOL.name).read_text(encoding='utf-8'))
        if value['id'] != 'CB-USD-FWD-001' or value['symbol'] != 'BTCUSD':
            raise CoinbaseForwardError('前瞻协议编号不符', 503)
        return value

    def _files(self):
        return {name: hashlib.sha256((self.base / name).read_bytes()).hexdigest() for name in SOURCES}

    def _registration(self):
        path = self.directory / 'registration.json'
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        verify(value)
        if value.get('studyId') != 'CB-USD-FWD-001':
            raise CoinbaseForwardError('前瞻登记编号不符', 503)
        return value

    def _public_registration(self):
        """Published, sealed plan for display only; never authorizes local scoring."""
        path = self.base / PUBLIC_REGISTRATION
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        verify(value)
        if (value.get('studyId') != 'CB-USD-FWD-001' or
                value.get('protocolSha256') != digest(value.get('protocol')) or
                value.get('scheduleSha256') != digest(value.get('schedule')) or
                value.get('protocol') != self.protocol()):
            raise CoinbaseForwardError('公开前瞻登记与协议或计划哈希不符', 503)
        return value

    def _evaluations(self):
        previous, result = None, []
        for path in sorted(self.directory.glob('evaluation-*.json')):
            item = json.loads(path.read_text(encoding='utf-8'))
            verify(item)
            if item.get('previousSha256') != previous or item.get('studyId') != 'CB-USD-FWD-001':
                raise CoinbaseForwardError('前瞻评价链不连续', 503)
            previous = item['contentSha256']
            result.append(item)
        return result

    def _public_audits(self, registration):
        previous, result = None, []
        for path in sorted(self.public_audit_directory.glob('public-review-*.json')):
            item = json.loads(path.read_text(encoding='utf-8'))
            verify(item)
            if (item.get('previousSha256') != previous or
                    item.get('registrationSha256') != registration['contentSha256'] or
                    item.get('studyId') != registration['studyId'] or
                    item.get('review', {}).get('mode') != 'public_read_only'):
                raise CoinbaseForwardError('公开计划本机审计链不连续', 503)
            previous = item['contentSha256']
            result.append(item)
        return result

    def register(self):
        with self.lock:
            if self._registration():
                raise CoinbaseForwardError('本期前瞻计划已登记，不能重写')
            protocol = self.protocol()
            captured = self.now()
            deadline = datetime.fromisoformat(protocol['registrationDeadline'])
            if captured >= deadline.astimezone(timezone.utc):
                raise CoinbaseForwardError('首日观察窗已经开始，不能补登记')
            first, last = date.fromisoformat(protocol['startDate']), date.fromisoformat(protocol['endDate'])
            if (last - first).days != 29:
                raise CoinbaseForwardError('固定计划必须为 30 个自然日', 503)
            source_end = captured.astimezone(LOCAL).date() - DAY
            source_start = source_end - 89 * DAY
            snapshot = self.market._snapshot('BTCUSD')
            index = inspect(snapshot, source_start, source_end, captured)
            anchors, projections = sequential_gann(index, first, last, captured, 90, (1, 2), 'BTCUSD')
            eligible_gann = [p for p in projections if p['eligible']]
            astro = calculate_astro_events(first.isoformat(), last.isoformat())
            _, boundaries = episode_plan(first, last)
            by_day = {d.isoformat(): [] for d in days(first, last)}
            for p in eligible_gann:
                by_day[p['targetDate']].append({'ruleId': 'GANN-EQUAL-INTERVAL', 'family': 'GANN',
                                               'eventId': p['id'], 'knownAtUtc': p['knownAtUtc']})
            for event in astro['events']:
                target = event['atLocal'][:10]
                if target in by_day:
                    by_day[target].append({'ruleId': 'ASTRO-MOON-PHASE' if event['kind'] == 'moon_phase'
                                           else 'ASTRO-NAKSHATRA-INGRESS', 'family': 'ASTROLOGY',
                                           'eventId': event['id'], 'knownAtUtc': captured.isoformat()})
            schedule = []
            for day in days(first, last):
                pillars = calculate_calendar(local_open(day).isoformat())['pillars']
                boundary = daily_transitions(day, boundaries)
                direction = [] if boundary else [rid for rid in rule_ids(pillars['day']) if rid in RULES]
                schedule.append({'date': day.isoformat(), 'dayPillar': pillars['day'],
                                 'monthPillar': pillars['month'], 'boundaryExcluded': bool(boundary),
                                 'directionRuleIds': direction, 'timeEvents': sorted(by_day[day.isoformat()],
                                                                                   key=lambda e: (e['ruleId'], e['eventId']))})
            completed = self.now()
            if completed >= deadline.astimezone(timezone.utc):
                raise CoinbaseForwardError('计划生成期间已越过登记截止时间')
            value = seal({'studyId': protocol['id'], 'version': protocol['version'],
                          'registeredAtUtc': completed.isoformat(), 'sourceAsOfUtc': captured.isoformat(),
                          'startDate': first.isoformat(),
                          'endDate': last.isoformat(), 'symbol': 'BTCUSD', 'protocol': protocol,
                          'protocolSha256': digest(protocol), 'schedule': schedule,
                          'scheduleSha256': digest(schedule), 'sourceSnapshotSha256': snapshot_hash(snapshot),
                          'gannInputRange': {'start': source_start.isoformat(), 'end': source_end.isoformat()},
                          'gannInputBarSha256': snapshot_hash([index[d] for d in sorted(index)]),
                          'gannAnchors': anchors, 'gannProjections': eligible_gann,
                          'astronomyConfigurationSha256': astro['configurationSha256'],
                          'astronomySourceHashes': astro['sourceHashes'],
                          'implementationSha256': self._files(), 'registeredPrediction': True,
                          'independentBlindSample': False, 'marketValidated': False})
            if self._files() != value['implementationSha256']:
                raise CoinbaseForwardError('登记期间实现文件变化，拒绝保存')
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / 'registration.json').open('x', encoding='utf-8') as output:
                json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
            return value

    def _score(self, registration, snapshot, asof):
        first = date.fromisoformat(registration['startDate'])
        last = date.fromisoformat(registration['endDate'])
        index = inspect(snapshot, first - 5 * DAY, last + 5 * DAY, asof)
        local_today = asof.astimezone(LOCAL).date()
        rows = []
        for scheduled in registration['schedule']:
            day = date.fromisoformat(scheduled['date'])
            inspected = index[day]
            direction = ('boundary_excluded' if scheduled['boundaryExcluded'] else
                         'future' if day >= local_today else 'missing' if not inspected['valid'] else 'scored')
            prices = inspected['prices'] if direction == 'scored' else None
            actual = None
            if prices:
                actual = 'up' if prices['close'] > prices['open'] else 'down' if prices['close'] < prices['open'] else 'flat'
            registration_at = datetime.fromisoformat(registration['registeredAtUtc'])
            if registration_at >= local_open(day - 3 * DAY):
                timing, time_status = None, 'late_registration'
            elif day + 5 * DAY >= local_today:
                timing, time_status = None, 'future'
            else:
                timing, reason = timing_label(day, index, first - 5 * DAY, last + 5 * DAY)
                time_status = 'missing' if reason else 'scored'
            kinds = {RULES[r] for r in scheduled['directionRuleIds']}
            rows.append({'date': scheduled['date'], 'directionStatus': direction,
                         'actual': actual, 'returnPct': float((prices['close'] / prices['open'] - Decimal(1)) * 100) if prices else None,
                         'directionResults': {rule: 'tie' if actual == 'flat' else 'correct' if actual == RULES[rule]
                                              else 'incorrect' for rule in scheduled['directionRuleIds']} if actual else {},
                         'directionConflict': len(kinds) > 1, 'timeStatus': time_status,
                         'timing': timing if time_status == 'scored' else None,
                         'missingReasons': sorted(set(inspected['reasons'])) if direction == 'missing' else [],
                         'timeEvents': scheduled['timeEvents']})
        scored_direction = [r for r in rows if r['directionStatus'] == 'scored']
        scored_time = [r for r in rows if r['timeStatus'] == 'scored']
        direction_metrics = []
        for rid, expected in RULES.items():
            selected = [r for r in scored_direction if rid in r['directionResults']]
            hits = sum(r['directionResults'][rid] == 'correct' for r in selected)
            direction_metrics.append({'id': rid, 'n': len(selected), 'correct': hits,
                                      'incorrect': sum(r['directionResults'][rid] == 'incorrect' for r in selected),
                                      'tie': sum(r['directionResults'][rid] == 'tie' for r in selected),
                                      'hitRatePct': hits / len(selected) * 100 if selected else None,
                                      'allDayBaselinePct': sum(r['actual'] == expected for r in scored_direction)
                                      / len(scored_direction) * 100 if scored_direction else None})
        time_metrics = []
        for rid in ('GANN-EQUAL-INTERVAL', 'ASTRO-MOON-PHASE', 'ASTRO-NAKSHATRA-INGRESS'):
            selected = [r for r in scored_time if any(e['ruleId'] == rid for e in r['timeEvents'])]
            hits = sum(r['timing']['hit'] for r in selected)
            time_metrics.append({'id': rid, 'n': len(selected), 'within3': hits,
                                 'within3RatePct': hits / len(selected) * 100 if selected else None})
        common_dates = {date.fromisoformat(r['date']) for r in scored_time}
        covered = {date.fromisoformat(r['date']) + i * DAY for r in scored_time if r['timeEvents']
                   for i in range(-3, 4) if date.fromisoformat(r['date']) + i * DAY in common_dates}
        return {'rows': rows, 'summary': {'directionRules': direction_metrics,
                'directionScoredDays': len(scored_direction), 'directionFutureDays': sum(r['directionStatus'] == 'future' for r in rows),
                'directionMissingDays': sum(r['directionStatus'] == 'missing' for r in rows),
                'directionBoundaryDays': sum(r['directionStatus'] == 'boundary_excluded' for r in rows),
                'directionConflictDays': sum(r['directionConflict'] for r in scored_direction),
                'timeRules': time_metrics, 'timeScoredDays': len(scored_time),
                'timeFutureDays': sum(r['timeStatus'] == 'future' for r in rows),
                'timeLateRegistrationDays': sum(r['timeStatus'] == 'late_registration' for r in rows),
                'timeMissingDays': sum(r['timeStatus'] == 'missing' for r in rows),
                'allDateWithin3Pct': sum(r['timing']['hit'] for r in scored_time) / len(scored_time) * 100 if scored_time else None,
                'timeEventCoveragePct': len(covered) / len(scored_time) * 100 if scored_time else None}}

    def evaluate(self):
        with self.lock:
            registration = self._registration()
            if not registration:
                raise CoinbaseForwardError('前瞻计划尚未提前登记')
            history = self._evaluations()
            if any(e['registrationSha256'] != registration['contentSha256'] for e in history):
                raise CoinbaseForwardError('评价引用的原登记哈希不符', 503)
            asof = self.now()
            snapshot = self.market._snapshot('BTCUSD')
            score = self._score(registration, snapshot, asof)
            signature = digest({'localDate': asof.astimezone(LOCAL).date().isoformat(),
                                'rows': score['rows']})
            if history and history[-1]['inputSha256'] == signature:
                return {'created': False, 'evaluation': history[-1]}
            result = seal({'studyId': registration['studyId'], 'registrationSha256': registration['contentSha256'],
                           'evaluatedAtUtc': asof.isoformat(), 'marketAsOfUtc': snapshot[0]['last_success'],
                           'inputSha256': signature, 'previousSha256': history[-1]['contentSha256'] if history else None,
                           'implementationSha256': self._files(), 'marketValidated': False,
                           **score})
            self.directory.mkdir(parents=True, exist_ok=True)
            filename = 'evaluation-' + asof.strftime('%Y%m%dT%H%M%S%fZ') + '-' + result['contentSha256'][:8] + '.json'
            with (self.directory / filename).open('x', encoding='utf-8') as output:
                json.dump(result, output, ensure_ascii=False, sort_keys=True, indent=2)
            return {'created': True, 'evaluation': result}

    def preview_public(self):
        """Score the sealed public plan against this machine's existing cache, without writes."""
        with self.lock:
            registration = self._public_registration()
            if not registration:
                raise CoinbaseForwardError('公开封存计划不存在', 404)
            asof = self.now()
            snapshot = self.market._snapshot('BTCUSD')
            state = snapshot[0]
            if (state.get('symbol') != 'BTCUSD' or state.get('provider') != 'Coinbase Exchange' or
                    state.get('product') != 'BTC-USD' or state.get('marketType') != 'spot'):
                raise CoinbaseForwardError('只读复核必须使用 Coinbase BTC/USD 现货缓存', 503)
            first = date.fromisoformat(registration['startDate']) - 5 * DAY
            last = date.fromisoformat(registration['endDate']) + 5 * DAY
            index = inspect(snapshot, first, last, asof)
            price_inputs = [{'date': day.isoformat(), 'valid': index[day]['valid'],
                             'prices': {key: str(value) for key, value in index[day]['prices'].items()}
                             if index[day]['prices'] else None,
                             'reasons': index[day]['reasons']} for day in days(first, last)]
            score = self._score(registration, snapshot, asof)
            # Show observed OHLC and the cached candle's provenance without
            # changing the sealed scoring output or writing an evaluation.
            today = asof.astimezone(LOCAL).date()
            for row in score['rows']:
                day = date.fromisoformat(row['date'])
                observed = index[day]
                candle = snapshot[1].get(ms(local_open(day)))
                prices = observed['prices'] if day < today and observed['valid'] else None
                row['marketEvidence'] = {
                    'status': 'future' if day >= today else 'complete' if prices else 'missing',
                    'open': str(prices['open']) if prices else None,
                    'high': str(prices['high']) if prices else None,
                    'low': str(prices['low']) if prices else None,
                    'close': str(prices['close']) if prices else None,
                    'missingReasons': observed['reasons'] if day < today and not prices else [],
                    'candleSha256': candle.get('body_sha256') if prices and candle else None,
                    'sourceRequestIds': json.loads(candle['source_request_ids'])
                    if prices and candle and candle.get('source_request_ids') else [],
                }
            return {'mode': 'public_read_only', 'registrationSha256': registration['contentSha256'],
                    'evaluatedAtUtc': asof.isoformat(), 'marketAsOfUtc': state.get('last_success'),
                    'marketProvider': state['provider'], 'product': state['product'],
                    'dayTimezone': state.get('dayTimezone'),
                    'priceInputSha256': digest(price_inputs),
                    'rawCacheSnapshotSha256': snapshot_hash(snapshot),
                    'scoreImplementationSha256': self._files(), **score}

    def refresh_public_market(self):
        """Explicitly fetch closed days needed for public replay; never save a review."""
        registration = self._public_registration()
        if not registration:
            raise CoinbaseForwardError('公开封存计划不存在', 404)
        yesterday = self.now().astimezone(LOCAL).date() - DAY
        first = date.fromisoformat(registration['startDate']) - 5 * DAY
        last = min(date.fromisoformat(registration['endDate']) + 5 * DAY, yesterday)
        if last < first:
            raise CoinbaseForwardError('本期尚无已经收盘的行情日期', 409)
        return self.market.refresh_range('BTCUSD', first, last)

    def auto_public_tick(self):
        """While the server runs, append an audit only for a new observed input."""
        with self.lock:
            registration = self._public_registration()
            if not registration:
                return None
            now = self.now()
            local = now.astimezone(LOCAL)
            first_day = date.fromisoformat(registration['startDate'])
            last_day = date.fromisoformat(registration['endDate'])
            # The final timing label needs the five days after the final target.
            if ((local.hour == 0 and local.minute < 20) or local.date() < first_day or
                    local.date() > last_day + 6 * DAY):
                return None
            self.last_public_auto_attempt = now.isoformat()
            history = self._public_audits(registration)
            first = first_day - 5 * DAY
            last = min(last_day + 5 * DAY, local.date() - DAY)
            snapshot = self.market._snapshot('BTCUSD')
            index = inspect(snapshot, first, last, now)
            missing = [day for day in days(first, last) if not index[day]['valid']]
            if missing:
                self.market.refresh_range('BTCUSD', missing[0], last)
            review = self.preview_public()
            key = digest({'localDate': local.date().isoformat(),
                          'priceInputSha256': review['priceInputSha256'],
                          'rawCacheSnapshotSha256': review['rawCacheSnapshotSha256'],
                          'scoreImplementationSha256': review['scoreImplementationSha256']})
            if history and history[-1]['inputSha256'] == key:
                self.last_public_auto_error = None
                return {'created': False, 'audit': history[-1]}
            audit = seal({'studyId': registration['studyId'],
                          'registrationSha256': registration['contentSha256'],
                          'createdAtUtc': now.isoformat(),
                          'observedLocalDate': local.date().isoformat(),
                          'inputSha256': key,
                          'previousSha256': history[-1]['contentSha256'] if history else None,
                          'review': review})
            self.public_audit_directory.mkdir(parents=True, exist_ok=True)
            filename = ('public-review-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '-' +
                        audit['contentSha256'][:8] + '.json')
            with (self.public_audit_directory / filename).open('x', encoding='utf-8') as output:
                json.dump(audit, output, ensure_ascii=False, sort_keys=True, indent=2)
            self.last_public_auto_error = None
            return {'created': True, 'audit': audit}

    def status(self):
        with self.lock:
            registration = self._registration()
            history = self._evaluations() if registration else []
            public = self._public_registration()
            audits = self._public_audits(public) if public else []
            if registration and any(e['registrationSha256'] != registration['contentSha256'] for e in history):
                raise CoinbaseForwardError('评价引用的原登记哈希不符', 503)
            market_path = getattr(self.market, 'path', None)
            market_path = Path(market_path).resolve() if market_path is not None else None
            registration_path = (self.directory / 'registration.json').resolve()
            return {'studyId': 'CB-USD-FWD-001', 'nowUtc': self.now().isoformat(),
                    'protocol': self.protocol(), 'registration': registration,
                    'publicRegistration': public,
                    'dataSources': {'marketCachePath': str(market_path) if market_path else None,
                                    'localRegistrationPath': str(registration_path),
                                    'publicPlanPath': str((self.base / PUBLIC_REGISTRATION).resolve()),
                                    'publicAuditDirectory': str(self.public_audit_directory.resolve()),
                                    'marketAndRegistrationSameDirectory': bool(market_path and
                                        market_path.parent == registration_path.parent.parent)},
                    'publicAudit': {'count': len(audits),
                                    'lastAttemptUtc': self.last_public_auto_attempt,
                                    'lastError': self.last_public_auto_error,
                                    'recent': [{'createdAtUtc': item['createdAtUtc'],
                                                'observedLocalDate': item['observedLocalDate'],
                                                'contentSha256': item['contentSha256'],
                                                'priceInputSha256': item['review']['priceInputSha256'],
                                                'scoredDays': item['review']['summary']['directionScoredDays'],
                                                'missingDays': item['review']['summary']['directionMissingDays']}
                                               for item in audits[-7:]]},
                    'latestEvaluation': history[-1] if history else None,
                    'evaluationCount': len(history), 'lastAutoError': self.last_auto_error,
                    'implementationChangedSinceRegistration': bool(registration and
                        self._files() != registration['implementationSha256']),
                    'autoRefreshWhileServerRuns': True}

    def auto_tick(self):
        """A server-side hourly check; it does not create or alter predictions."""
        if not self._registration():
            return
        now = self.now().astimezone(LOCAL)
        if now.hour == 0 and now.minute < 20:
            return
        if now.date() > date.fromisoformat(self.protocol()['endDate']) + 30 * DAY:
            return
        yesterday = now.date() - DAY
        snapshot = self.market._snapshot('BTCUSD')
        latest = max((datetime.fromtimestamp(key / 1000, LOCAL).date() for key, row in snapshot[1].items()
                      if row['is_closed'] and not row['conflicted']), default=None)
        if latest is None or latest < yesterday:
            count = min(30, max(2, (yesterday - (latest or yesterday)).days + 1))
            self.market.refresh('BTCUSD', count)
        self.evaluate()
        self.last_auto_error = None
