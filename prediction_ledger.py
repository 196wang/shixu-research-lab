"""Versioned prediction ledger; explicit writes, cache-only scoring, full history."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from prediction_confluence import describe_confluence

VERSION = '0.15.0'
HERE = Path(__file__).resolve().parent
LOCAL = timezone(timedelta(hours=8))
BODY_FIELDS = {'symbol', 'kind', 'targetDate', 'horizonDays', 'title', 'note', 'rules', 'invalidation'}
KINDS = ('up', 'down', 'high', 'low', 'time')
STATUS_KEYS = ('exact', 'within', 'miss', 'correct', 'incorrect', 'tie', 'pending', 'missing', 'unreviewed', 'invalidated')


class PredictionLedgerError(RuntimeError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def seal(value, field):
    value[field] = digest({k: v for k, v in value.items() if k != field})
    return value


def instant(value):
    try:
        at = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError('missing timezone')
        return at.astimezone(timezone.utc)
    except (AttributeError, ValueError, TypeError) as exc:
        raise PredictionLedgerError('留档时间格式无效', 503) from exc


def text_field(value, label, minimum, maximum):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise PredictionLedgerError(f'{label}长度应为{minimum}–{maximum}字')
    return value.strip()


def normalize_body(body):
    if not isinstance(body, dict) or set(body) != BODY_FIELDS:
        raise PredictionLedgerError('预测只接受symbol/kind/targetDate/horizonDays/title/note/rules/invalidation全部字段')
    if body['symbol'] not in ('BTCUSDT', 'ETHUSDT', 'BTCUSD', 'ETHUSD') or body['kind'] not in KINDS:
        raise PredictionLedgerError('交易对或预测类型无效')
    try:
        target = date.fromisoformat(body['targetDate'])
        if target.isoformat() != body['targetDate'] or not 2020 <= target.year <= 2099:
            raise ValueError('range')
    except (TypeError, ValueError) as exc:
        raise PredictionLedgerError('目标日期必须为2020–2099年的YYYY-MM-DD') from exc
    horizon = body['horizonDays']
    if type(horizon) is not int or horizon not in (1, 3, 7, 30) or (body['kind'] in ('high', 'low', 'time') and horizon != 1):
        raise PredictionLedgerError('方向窗口为1/3/7/30日，时间类窗口字段固定1')
    rules = body['rules']
    if not isinstance(rules, list) or len(rules) > 20:
        raise PredictionLedgerError('关联规则应为0–20条')
    clean_rules, seen = [], set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'id', 'family', 'direction', 'version'}:
            raise PredictionLedgerError('规则字段必须为id/family/direction/version')
        if rule['family'] not in ('BAZI', 'GANN', 'ASTROLOGY', 'MANUAL') or rule['direction'] not in KINDS:
            raise PredictionLedgerError('规则家族或方向无效')
        normalized = {**rule, 'id': text_field(rule['id'], '规则ID', 1, 100),
                      'version': text_field(rule['version'], '规则版本', 1, 80)}
        identity = (normalized['family'], normalized['id'])
        if identity in seen:
            raise PredictionLedgerError('同一规则不能重复关联')
        seen.add(identity)
        clean_rules.append(normalized)
    invalidation = body['invalidation']
    if invalidation is not None:
        if not isinstance(invalidation, dict) or set(invalidation) != {'type', 'price'} or invalidation['type'] not in ('close_below', 'close_above'):
            raise PredictionLedgerError('失效条件应为close_below或close_above及price')
        value = invalidation['price']
        if not isinstance(value, str) or not 1 <= len(value) <= 200 or not re.fullmatch(r'\d+(?:\.\d+)?', value):
            raise PredictionLedgerError('失效价格须为正十进制字符串')
        try:
            price = Decimal(value)
            if not price.is_finite() or price <= 0 or not math.isfinite(float(price)):
                raise ValueError('not positive finite')
            canonical = format(price, 'f')
            if '.' in canonical:
                canonical = canonical.rstrip('0').rstrip('.')
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise PredictionLedgerError('失效价格须为正有限十进制字符串') from exc
        invalidation = {'type': invalidation['type'], 'price': canonical}
    return {**deepcopy(body), 'title': text_field(body['title'], '标题', 1, 120),
            'note': text_field(body['note'], '备注', 0, 8000),
            'rules': sorted(clean_rules, key=lambda r: (r['family'], r['id'], r['version'], r['direction'])),
            'invalidation': invalidation}


def window_start(body):
    day = date.fromisoformat(body['targetDate'])
    if body['kind'] in ('high', 'low', 'time'):
        day -= timedelta(days=3)
    return datetime(day.year, day.month, day.day, tzinfo=LOCAL)


def implementation_hashes(base):
    try:
        return {name: hashlib.sha256((Path(base) / name).read_bytes()).hexdigest()
                for name in ('prediction_ledger.py', 'prediction_scoring.py', 'prediction_confluence.py', 'gann_timing.py', 'market_feed.py', 'coinbase_feed.py')}
    except OSError as exc:
        raise PredictionLedgerError('预测实现文件缺失，无法保存版本证据', 503) from exc


def latest_reports(reports):
    result = {}
    for report in reports:
        result[report['recordId']] = report
    return result


def public_records(records, invalidations, reports):
    latest = latest_reports(reports)
    confirmed = {}
    for report in reports:
        if report['score'].get('invalidation', {}).get('invalidated') is True:
            confirmed.setdefault(report['recordId'], report['id'])
    counts = {}
    for event in invalidations:
        counts[event['recordId']] = counts.get(event['recordId'], 0) + 1
    result = []
    for record in records:
        report = latest.get(record['id'])
        automatic = record['id'] in confirmed
        result.append({**record, 'invalidated': bool(counts.get(record['id'])) or automatic,
                       'automaticInvalidated': automatic,
                       'automaticInvalidationReportId': confirmed.get(record['id']),
                       'invalidationCount': counts.get(record['id'], 0), 'latestReportId': report['id'] if report else None})
    return result


def summarize(records, reports):
    latest = latest_reports(reports)
    groups = {}
    for name, original in (('originals', True), ('revisions', False)):
        selected = [r for r in records if r['isOriginal'] is original]
        counts = dict.fromkeys(STATUS_KEYS, 0)
        time_counts = dict.fromkeys(('exact', 'within_tolerance', 'miss'), 0)
        direction_counts = dict.fromkeys(('correct', 'incorrect', 'tie'), 0)
        changed_count = 0
        for record in selected:
            report = latest.get(record['id'], {})
            score = report.get('score')
            changed = bool(report.get('implementationChangedSinceRegistration'))
            changed_count += int(changed)
            status = score['status'] if score else 'unreviewed'
            counts['within' if status == 'within_tolerance' else status] += 1
            counts['invalidated'] += int(record['invalidated'])
            if score and record['prospectiveEligible'] and score.get('prospectiveEligible') and not changed:
                if status in time_counts:
                    time_counts[status] += 1
                if status in direction_counts:
                    direction_counts[status] += 1
        time_total, direction_total = sum(time_counts.values()), sum(direction_counts.values())
        groups[name] = {
            'total': len(selected), 'prospectiveEligibleCount': sum(r['prospectiveEligible'] for r in selected),
            'algorithmChangedCount': changed_count, 'counts': counts,
            'timeMetrics': {'evaluated': time_total, 'eligibleResultsOnly': True,
                            'exactHitRatePct': time_counts['exact'] / time_total * 100 if time_total else None,
                            'withinToleranceHitRatePct': (time_counts['exact'] + time_counts['within_tolerance']) / time_total * 100 if time_total else None},
            'directionMetrics': {'evaluated': direction_total, 'eligibleResultsOnly': True,
                                 'correctRatePct': direction_counts['correct'] / direction_total * 100 if direction_total else None}}
    return groups


class PredictionLedger:
    def __init__(self, data_dir, market, clock=None, base=None):
        self.base = Path(base or HERE)
        self.path = Path(data_dir) / 'predictions.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.market, self.clock = market, clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS prediction_versions(
                id TEXT PRIMARY KEY,root_id TEXT NOT NULL REFERENCES prediction_versions(id),
                parent_id TEXT REFERENCES prediction_versions(id),version INTEGER NOT NULL,
                created_at TEXT NOT NULL,body_sha TEXT NOT NULL,record_json TEXT NOT NULL,record_sha TEXT NOT NULL,
                UNIQUE(root_id,version));
              CREATE UNIQUE INDEX IF NOT EXISTS prediction_root_payload ON prediction_versions(body_sha) WHERE parent_id IS NULL;
              CREATE UNIQUE INDEX IF NOT EXISTS prediction_revision_payload ON prediction_versions(parent_id,body_sha) WHERE parent_id IS NOT NULL;
              CREATE TABLE IF NOT EXISTS prediction_invalidations(
                id TEXT PRIMARY KEY,record_id TEXT NOT NULL REFERENCES prediction_versions(id),created_at TEXT NOT NULL,
                reason TEXT NOT NULL,event_json TEXT NOT NULL,event_sha TEXT NOT NULL,UNIQUE(record_id,reason));
              CREATE TABLE IF NOT EXISTS prediction_evaluations(
                id TEXT PRIMARY KEY,created_at TEXT NOT NULL,evaluation_json TEXT NOT NULL,evaluation_sha TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS prediction_reports(
                id TEXT PRIMARY KEY,evaluation_id TEXT NOT NULL REFERENCES prediction_evaluations(id),
                record_id TEXT NOT NULL REFERENCES prediction_versions(id),created_at TEXT NOT NULL,
                report_json TEXT NOT NULL,report_sha TEXT NOT NULL,previous_sha TEXT,
                UNIQUE(evaluation_id,record_id));
            ''')
            for table in ('prediction_versions', 'prediction_invalidations', 'prediction_evaluations', 'prediction_reports'):
                for verb in ('UPDATE', 'DELETE'):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'prediction ledger is append-only'); END")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _now(self):
        at = self.clock()
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise PredictionLedgerError('系统时钟必须带明确时区', 503)
        return at.astimezone(timezone.utc)

    def _load(self, db):
        result = {}
        mapping = [('records', 'prediction_versions', 'record_json', 'record_sha', 'recordSha256'),
                   ('invalidations', 'prediction_invalidations', 'event_json', 'event_sha', 'eventSha256'),
                   ('evaluations', 'prediction_evaluations', 'evaluation_json', 'evaluation_sha', 'contentSha256'),
                   ('reports', 'prediction_reports', 'report_json', 'report_sha', 'contentSha256')]
        try:
            for key, table, json_col, sha_col, field in mapping:
                result[key] = []
                for row in db.execute(f'SELECT * FROM {table} ORDER BY rowid'):
                    value = json.loads(row[json_col])
                    actual = digest({k: v for k, v in value.items() if k != field})
                    if value.get(field) != actual or row[sha_col] != actual or value['id'] != row['id'] or value['createdAtUtc'] != row['created_at']:
                        raise PredictionLedgerError('预测留档内容哈希或元数据不一致', 503)
                    if key == 'records' and (value['rootId'] != row['root_id'] or value['parentId'] != row['parent_id'] or value['version'] != row['version'] or value['bodySha256'] != row['body_sha'] or digest({f: value[f] for f in BODY_FIELDS}) != value['bodySha256']):
                        raise PredictionLedgerError('预测版本索引或正文哈希不一致', 503)
                    if key == 'reports' and (value['recordId'] != row['record_id'] or value['evaluationId'] != row['evaluation_id'] or value['previousReportSha256'] != row['previous_sha'] or digest(value['marketSnapshot']) != value['marketSnapshotSha256']):
                        raise PredictionLedgerError('预测报告索引或行情快照哈希不一致', 503)
                    if key == 'invalidations' and (value['recordId'] != row['record_id'] or value['reason'] != row['reason']):
                        raise PredictionLedgerError('失效事件索引不一致', 503)
                    result[key].append(value)
            records = {r['id']: r for r in result['records']}
            chains = {}
            for report in result['reports']:
                record = records.get(report['recordId'])
                if not record or report['rootId'] != record['rootId'] or report['recordVersion'] != record['version'] or report['previousReportSha256'] != chains.get(record['id']):
                    raise PredictionLedgerError('预测报告版本或追加链不一致', 503)
                chains[record['id']] = report['contentSha256']
            for batch in result['evaluations']:
                members = [r for r in result['reports'] if r['evaluationId'] == batch['id']]
                if batch['reportHashes'] != {r['recordId']: r['contentSha256'] for r in members}:
                    raise PredictionLedgerError('评价批次成员不一致', 503)
        except (KeyError, TypeError, ValueError) as exc:
            raise PredictionLedgerError('预测留档格式无效', 503) from exc
        return result

    def _read(self):
        try:
            with self.connect() as db:
                db.execute('BEGIN')
                return self._load(db)
        except sqlite3.DatabaseError as exc:
            raise PredictionLedgerError('无法读取预测账本', 503) from exc

    def _guard_now(self, data, minimum=None):
        now = self._now()
        times = [instant(item['createdAtUtc']) for values in data.values() for item in values]
        if minimum is not None:
            times.append(minimum)
        if times and now < max(times):
            raise PredictionLedgerError('系统时钟早于已有记录，拒绝倒退写入', 409)
        return now

    @staticmethod
    def _find(data, identity):
        match = next((r for r in data['records'] if r['id'] == identity), None)
        if not match:
            raise PredictionLedgerError('未找到预测版本', 404)
        return match

    def _list_data(self, data):
        records = public_records(data['records'], data['invalidations'], data['reports'])
        latest = latest_reports(data['reports'])
        return {'schemaVersion': 1, 'version': VERSION, 'nowUtc': self._now().isoformat(),
                'records': records, 'invalidations': data['invalidations'],
                'latestReports': [{k: v for k, v in r.items() if k != 'marketSnapshot'} for r in latest.values()],
                'summary': summarize(records, data['reports']), 'evaluationCount': len(data['evaluations']),
                'reportCount': len(data['reports']), 'marketValidated': False}

    def list(self):
        return self._list_data(self._read())

    def _save_version(self, body, parent_id=None):
        clean = normalize_body(body)
        body_sha = digest(clean)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            data = self._load(db)
            now = self._guard_now(data)
            parent = self._find(data, parent_id) if parent_id is not None else None
            duplicate = next((r for r in data['records'] if r['parentId'] == parent_id and r['bodySha256'] == body_sha), None)
            if duplicate:
                return {'created': False, 'record': duplicate, 'ledger': self._list_data(data)}
            original = self._find(data, parent['rootId']) if parent else None
            deadline = window_start(clean)
            if parent is None and now >= deadline:
                raise PredictionLedgerError('观察窗已经开始，不能补登记为原始预测', 409)
            hashes = implementation_hashes(self.base)
            created = self._guard_now(data, now)
            if parent is None and created >= deadline:
                raise PredictionLedgerError('写入时观察窗已经开始，未登记预测', 409)
            identity = str(uuid.uuid4())
            late = bool(parent) and (created >= deadline or created >= instant(original['windowStartAt']))
            record = {**clean, 'id': identity, 'rootId': original['id'] if original else identity,
                      'parentId': parent_id, 'version': max((r['version'] for r in data['records'] if parent and r['rootId'] == parent['rootId']), default=0) + 1,
                      'isOriginal': parent is None, 'createdAtUtc': created.isoformat(),
                      'windowStartAt': deadline.isoformat(), 'originalWindowStartAt': original['windowStartAt'] if original else deadline.isoformat(),
                      'lateRevision': late, 'prospectiveEligible': not late,
                      'bodySha256': body_sha, 'implementationHashes': hashes}
            records = public_records(data['records'] + [record], data['invalidations'], data['reports'])
            record['confluenceSnapshot'] = describe_confluence(records, created)
            seal(record, 'recordSha256')
            if implementation_hashes(self.base) != hashes:
                raise PredictionLedgerError('登记期间实现文件变化，未保存预测', 409)
            final_at = self._guard_now(data, created)
            if parent is None and final_at >= deadline:
                raise PredictionLedgerError('最终写入时观察窗已经开始，未登记预测', 409)
            record['createdAtUtc'] = final_at.isoformat()
            record['lateRevision'] = bool(parent) and (final_at >= deadline or final_at >= instant(original['windowStartAt']))
            record['prospectiveEligible'] = not record['lateRevision']
            current = public_records(data['records'] + [record], data['invalidations'], data['reports'])
            record['confluenceSnapshot'] = describe_confluence(current, final_at)
            seal(record, 'recordSha256')
            db.execute('INSERT INTO prediction_versions VALUES (?,?,?,?,?,?,?,?)',
                       (record['id'], record['rootId'], parent_id, record['version'], record['createdAtUtc'], body_sha, encode(record), record['recordSha256']))
        return {'created': True, 'record': record, 'ledger': self.list()}

    def create(self, body):
        return self._save_version(body)

    def revise(self, identity, body):
        if not isinstance(identity, str) or not identity:
            raise PredictionLedgerError('修订必须指定已有版本ID')
        return self._save_version(body, identity)

    def invalidate(self, identity, body):
        if not isinstance(body, dict) or set(body) != {'reason'}:
            raise PredictionLedgerError('人工失效只接受reason字段')
        reason = text_field(body['reason'], '失效原因', 1, 2000)
        with self.lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            data = self._load(db)
            at = self._guard_now(data)
            record = self._find(data, identity)
            duplicate = next((e for e in data['invalidations'] if e['recordId'] == identity and e['reason'] == reason), None)
            if duplicate:
                return {'created': False, 'invalidation': duplicate, 'ledger': self._list_data(data)}
            event = {'id': str(uuid.uuid4()), 'recordId': identity, 'rootId': record['rootId'],
                     'createdAtUtc': at.isoformat(), 'reason': reason, 'implementationHashes': implementation_hashes(self.base)}
            current = public_records(data['records'], data['invalidations'] + [event], data['reports'])
            event['confluenceSnapshot'] = describe_confluence(current, at)
            seal(event, 'eventSha256')
            final_at = self._guard_now(data, at)
            event['createdAtUtc'] = final_at.isoformat()
            current = public_records(data['records'], data['invalidations'] + [event], data['reports'])
            event['confluenceSnapshot'] = describe_confluence(current, final_at)
            seal(event, 'eventSha256')
            if event['implementationHashes'] != implementation_hashes(self.base):
                raise PredictionLedgerError('失效记录写入期间实现变化，未保存事件', 409)
            db.execute('INSERT INTO prediction_invalidations VALUES (?,?,?,?,?,?)',
                       (event['id'], identity, event['createdAtUtc'], reason, encode(event), event['eventSha256']))
        return {'created': True, 'invalidation': event, 'ledger': self.list()}

    def evaluate(self):
        from prediction_scoring import score_prediction
        with self.lock:
            data = self._read()
            if not data['records']:
                raise PredictionLedgerError('账本还没有预测，请先登记')
            self._guard_now(data)
            hashes = implementation_hashes(self.base)
            snapshots = {}
            for symbol in sorted({r['symbol'] for r in data['records']}):
                try:
                    snapshots[symbol] = self.market._snapshot(symbol)
                except Exception as exc:
                    raise PredictionLedgerError('无法读取行情缓存：' + type(exc).__name__, 503) from exc
            asof = self._guard_now(data)
            scores = {}
            for record in data['records']:
                try:
                    scores[record['id']] = score_prediction(record, snapshots[record['symbol']], asof)
                except Exception as exc:
                    raise PredictionLedgerError('预测评分失败：' + type(exc).__name__ + ' ' + str(exc), 503) from exc
            created = self._guard_now(data, asof)
            evaluation_id = str(uuid.uuid4())
            prior = latest_reports(data['reports'])
            draft_reports = [{'recordId': r['id'], 'score': scores[r['id']], 'id': str(uuid.uuid4())} for r in data['records']]
            current = public_records(data['records'], data['invalidations'], data['reports'] + draft_reports)
            confluence = describe_confluence(current, asof)
            reports = []
            for record, draft in zip(data['records'], draft_reports):
                score = draft['score']
                state = snapshots[record['symbol']][0]
                snapshot = {'asOfUtc': asof.isoformat(), 'state': deepcopy(state),
                            'selectedDays': deepcopy(score['evidence']['selectedDays'])}
                report = {**draft, 'evaluationId': evaluation_id, 'rootId': record['rootId'], 'recordVersion': record['version'],
                          'createdAtUtc': created.isoformat(), 'asOfUtc': asof.isoformat(),
                          'marketGeneration': state.get('generation', 0), 'marketAsOfUtc': state.get('last_success'),
                          'marketSnapshot': snapshot, 'marketSnapshotSha256': digest(snapshot),
                          'previousReportSha256': prior.get(record['id'], {}).get('contentSha256'),
                          'implementationHashes': hashes, 'implementationChangedSinceRegistration': hashes != record['implementationHashes'],
                          'confluenceSnapshot': confluence, 'marketValidated': False}
                reports.append(seal(report, 'contentSha256'))
            batch = seal({'id': evaluation_id, 'createdAtUtc': created.isoformat(), 'asOfUtc': asof.isoformat(),
                          'reportCount': len(reports), 'reportHashes': {r['recordId']: r['contentSha256'] for r in reports},
                          'confluenceSnapshot': confluence, 'implementationHashes': hashes}, 'contentSha256')
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                latest = self._load(db)
                if digest(latest) != digest(data):
                    raise PredictionLedgerError('复核期间账本有新增记录，请重新复核', 409)
                self._guard_now(latest, created)
                if hashes != implementation_hashes(self.base):
                    raise PredictionLedgerError('复核期间实现变化，未保存报告', 409)
                db.execute('INSERT INTO prediction_evaluations VALUES (?,?,?,?)',
                           (batch['id'], created.isoformat(), encode(batch), batch['contentSha256']))
                for report in reports:
                    db.execute('INSERT INTO prediction_reports VALUES (?,?,?,?,?,?,?)',
                               (report['id'], evaluation_id, report['recordId'], created.isoformat(), encode(report), report['contentSha256'], report['previousReportSha256']))
            return {'created': True, 'evaluation': batch, 'ledger': self.list()}

    def export(self):
        data = self._read()
        records = public_records(data['records'], data['invalidations'], data['reports'])
        return {'schemaVersion': 1, 'version': VERSION, 'exportedAtUtc': self._now().isoformat(),
                **data, 'summary': summarize(records, data['reports']), 'marketValidated': False}
