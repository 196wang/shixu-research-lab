"""Synthetic aggregation and isolated cache tests; no live requests/user DB writes."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_feed import DAY_MS, MarketFeedError, day_ms
from market_multiframe import (FOUR_HOURS, MarketMultiframe, build_bars, normalize_four_hour,
                               parse_bars_query, period_for)


def response(payload, status=200, headers=None):
    return {'body': json.dumps(payload).encode(), 'status': status, 'headers': headers or {}}


def raw_bar(opened, duration=FOUR_HOURS, close='101'):
    return [opened, '100', '110', '90', close, '12.5', opened + duration - 1, '0', 3, '0', '0', '0']


class API:
    def __init__(self, server_ms):
        self.server_ms = server_ms
        self.calls = []
        self.skip = set()
        self.override = {}
        self.extra = []
        self.failure_at = None
        self.failure_status = 500
        self.retry_after = '90'

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if len(self.calls) == self.failure_at:
            return response({}, self.failure_status, {'Retry-After': self.retry_after})
        if path == '/api/v3/time':
            return response({'serverTime': self.server_ms})
        rows = [self.override.get((params['symbol'], t), raw_bar(t))
                for t in range(params['startTime'], params['endTime'] + 1, FOUR_HOURS) if t not in self.skip]
        return response(rows + self.extra)


class Daily:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.calls = []

    def _snapshot(self, symbol):
        self.calls.append(symbol)
        return deepcopy(self.snapshot)

    def status(self):
        return {'version': '0.12.0', 'job': None}

    def start_refresh(self, symbol):
        self.calls.append(('start', symbol))
        return {'accepted': True, 'job': {'symbol': symbol, 'status': 'queued'}}


def daily_snapshot(first, last, symbol='BTCUSDT', server_ms=None):
    bars = {}
    for i in range((last - first).days + 1):
        opened = day_ms(first + timedelta(days=i))
        raw = raw_bar(opened, DAY_MS)
        bars[opened] = {'symbol': symbol, 'open_ms': opened, 'close_ms': opened + DAY_MS - 1,
                        **dict(zip(('open', 'high', 'low', 'close', 'volume'), raw[1:6])),
                        'is_closed': 1, 'conflicted': 0, 'request_id': 'R', 'body_sha256': 'a' * 64}
    state = {'symbol': symbol, 'range_start': str(first), 'range_end': str(last), 'server_ms': server_ms or day_ms(last + timedelta(days=1)),
             'generation': 3, 'last_success': '2026-09-27T00:00:00+00:00'}
    return state, bars, {}


LABELS = {'year': '甲辰', 'month': '丙寅', 'day': '甲子'}


class MultiframeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = lambda: datetime(2026, 9, 27, tzinfo=timezone.utc)
        self.api = API(day_ms(date(2026, 9, 27)) + 13 * 3600000)
        self.daily = Daily()
        self.feed = MarketMultiframe(self.temp.name, self.daily, self.api, self.clock)

    def tearDown(self):
        if self.feed.thread:
            self.feed.thread.join(5)
        self.temp.cleanup()

    def test_strict_query_and_normalization_numeric_and_boundary(self):
        self.assertEqual(parse_bars_query('symbol=ETHUSDT&interval=1M')['interval'], '1M')
        for text in ('', 'symbol=BTCUSDT', 'symbol=BTCUSDT&interval=1m', 'symbol=X&interval=4h',
                     'symbol=BTCUSDT&interval=4h&interval=4h', 'symbol=BTCUSDT&interval=4h&start=1'):
            with self.subTest(text=text), self.assertRaises(MarketFeedError):
                parse_bars_query(text)
        opened = day_ms(date(2026, 9, 27))
        for index, value in ((0, opened + 1), (6, opened + FOUR_HOURS), (1, 'NaN'), (1, '1e-99999999'),
                             (1, '1' * 201), (2, '1'), (3, '-1'), (5, '-1'), (8, False)):
            raw = raw_bar(opened)
            raw[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                normalize_four_hour(raw, opened + FOUR_HOURS, opened, opened)
        self.assertEqual(normalize_four_hour(raw_bar(opened), opened + FOUR_HOURS - 1, opened, opened)['is_closed'], 0)
        self.assertEqual(normalize_four_hour(raw_bar(opened), opened + FOUR_HOURS, opened, opened)['is_closed'], 1)

    def test_four_hour_initial_one_year_pagination_and_raw_hash(self):
        self.assertEqual(self.feed.refresh('BTCUSDT')['job']['status'], 'succeeded')
        pages = [p for path, p in self.api.calls if path.endswith('klines')]
        self.assertEqual(len(pages), 3)
        first = day_ms(date(2025, 9, 27))
        self.assertEqual([p['startTime'] for p in pages], [first + i * 1000 * FOUR_HOURS for i in range(3)])
        self.assertTrue(all(p['interval'] == '4h' and p['timeZone'] == '8' and p['limit'] == 1000 for p in pages))
        state, rows, gaps = self.feed._snapshot('BTCUSDT')
        self.assertEqual(len(rows), 365 * 6 + 4)
        self.assertEqual(state['range_start'], '2025-09-27')
        self.assertEqual(len(gaps), 1)
        self.assertEqual(self.feed._snapshot('ETHUSDT')[1], {})
        with self.feed.connect() as db:
            captures = db.execute('SELECT body,sha256 FROM mf_requests').fetchall()
            self.assertEqual(len(captures), 4)
            self.assertTrue(all(hashlib.sha256(r['body']).hexdigest() == r['sha256'] for r in captures))
        self.assertEqual({p.name for p in Path(self.temp.name).glob('*.sqlite3')}, {'market_multiframe.sqlite3'})

    @patch('market_multiframe.date_pillars', return_value=LABELS)
    @patch('market_multiframe.transitions_for', return_value=[])
    def test_pending_hidden_continuous_grid_and_then_closed(self, *_):
        self.feed.refresh('BTCUSDT')
        report = self.feed.bars('BTCUSDT', '4h')
        last = report['rows'][-1]
        self.assertEqual(last['atLocal'], '2026-09-27T12:00:00+08:00')
        self.assertFalse(last['complete'])
        self.assertEqual(last['quality']['status'], 'pending')
        self.assertIsNone(last['close'])
        self.assertTrue(all(b['openTimeMs'] - a['openTimeMs'] == FOUR_HOURS for a, b in zip(report['rows'], report['rows'][1:])))
        self.api.server_ms = day_ms(date(2026, 9, 27)) + 16 * 3600000
        self.feed.refresh('BTCUSDT')
        report = self.feed.bars('BTCUSDT', '4h')
        self.assertTrue(report['rows'][-2]['complete'])
        self.assertFalse(report['rows'][-1]['complete'])

    def test_idempotent_repair_and_closed_conflict_preserves_original(self):
        gap = day_ms(date(2025, 10, 1))
        self.api.skip.add(gap)
        self.feed.refresh('BTCUSDT')
        self.assertIn(gap, self.feed._snapshot('BTCUSDT')[2])
        self.api.skip.clear()
        self.feed.refresh('BTCUSDT')
        self.assertNotIn(gap, self.feed._snapshot('BTCUSDT')[2])
        recent = day_ms(date(2026, 9, 26))
        self.api.override[('BTCUSDT', recent)] = raw_bar(recent, close='105')
        self.feed.refresh('BTCUSDT')
        _, rows, gaps = self.feed._snapshot('BTCUSDT')
        self.assertEqual(rows[recent]['close'], '101')
        self.assertEqual(rows[recent]['conflicted'], 1)
        self.assertIn('source_conflict', gaps[recent])
        self.assertEqual(len(rows), 2194)

    def test_late_page_failure_retains_cache_and_time_regression_rejected(self):
        self.feed.refresh('BTCUSDT')
        state, rows, gaps = self.feed._snapshot('BTCUSDT')
        self.api.failure_at = len(self.api.calls) + 2
        self.assertEqual(self.feed.refresh('BTCUSDT')['job']['status'], 'failed')
        after = self.feed._snapshot('BTCUSDT')
        self.assertEqual(after[1:], (rows, gaps))
        self.assertEqual(after[0]['generation'], state['generation'])
        self.api.failure_at = None
        self.api.server_ms -= DAY_MS
        result = self.feed.refresh('BTCUSDT')
        self.assertEqual(result['job']['status'], 'failed')
        self.assertIn('时间回退', result['job']['lastError'])
        self.assertEqual(self.feed._snapshot('BTCUSDT')[1], rows)

    def test_duplicate_conflict_and_invalid_boundary_remain_gaps(self):
        self.api.server_ms = day_ms(date(2020, 1, 2)) + FOUR_HOURS
        first = day_ms(date(2020, 1, 1))
        self.api.extra = [raw_bar(first, close='105')]
        invalid = raw_bar(first + FOUR_HOURS)
        invalid[6] += 1
        self.api.override[('BTCUSDT', first + FOUR_HOURS)] = invalid
        result = self.feed.refresh('BTCUSDT')
        self.assertEqual(result['job']['status'], 'succeeded')
        _, rows, gaps = self.feed._snapshot('BTCUSDT')
        self.assertEqual(rows[first]['conflicted'], 1)
        self.assertIn('source_conflict', gaps[first])
        self.assertNotIn(first + FOUR_HOURS, rows)
        self.assertIn('wrong_UTC8_four_hour_boundary', gaps[first + FOUR_HOURS])
        with self.feed.connect() as db:
            kinds = {r['kind'] for r in db.execute('SELECT kind FROM mf_issues')}
            self.assertEqual(kinds, {'duplicate_conflict', 'invalid'})

    def test_rate_limit_stops_requests_and_persists_cooldown(self):
        self.api.failure_at = 1
        self.api.failure_status = 429
        self.api.retry_after = '172800'
        result = self.feed.refresh('BTCUSDT')
        self.assertEqual(result['job']['status'], 'rate_limited')
        self.assertEqual(datetime.fromisoformat(result['job']['cooldownUntilUtc']) - self.clock(), timedelta(days=2))
        self.assertEqual(len(self.api.calls), 1)
        with self.assertRaises(MarketFeedError) as exc:
            self.feed.start_refresh('ETHUSDT')
        self.assertEqual(exc.exception.status_code, 429)
        self.assertEqual(len(self.api.calls), 1)

    def test_restart_releases_abandoned_job_and_status_never_networks(self):
        identity = self.feed._begin('BTCUSDT')
        self.feed.lock.release()
        other = MarketMultiframe(self.temp.name, self.daily, self.api, self.clock)
        self.assertEqual(other._job(identity)['phase'], 'interrupted')
        self.assertEqual(other.status()['fourHour']['symbols'][0]['status'], 'failed')
        self.assertEqual(self.api.calls, [])
        other.start_refresh('ETHUSDT', '1w')
        self.assertEqual(self.daily.calls, [('start', 'ETHUSDT')])

    @patch('market_multiframe.date_pillars', return_value=LABELS)
    @patch('market_multiframe.transitions_for', return_value=[])
    def test_week_monday_full_aggregate_decimal_and_volume(self, *_):
        snap = daily_snapshot(date(2024, 1, 1), date(2024, 1, 7))
        rows = snap[1]
        rows[day_ms(date(2024, 1, 1))]['open'] = '100.125'
        rows[day_ms(date(2024, 1, 7))]['close'] = '109.875'
        report = build_bars(snap, 'BTCUSDT', '1w')
        row = report['rows'][0]
        self.assertEqual(len(report['rows']), 1)
        self.assertTrue(row['complete'])
        self.assertEqual(row['atLocal'], '2024-01-01T00:00:00+08:00')
        self.assertEqual(row['closeTimeUtcExclusive'], '2024-01-07T16:00:00+00:00')
        self.assertEqual((row['open'], row['high'], row['low'], row['close'], row['volume']), (100.125, 110, 90, 109.875, 87.5))
        self.assertEqual(row['quality']['expectedDays'], 7)

    @patch('market_multiframe.date_pillars', return_value=LABELS)
    @patch('market_multiframe.transitions_for', return_value=[])
    def test_month_leap_year_cross_year_and_partial_edges(self, *_):
        snap = daily_snapshot(date(2024, 2, 1), date(2024, 2, 29))
        row = build_bars(snap, 'BTCUSDT', '1M')['rows'][0]
        self.assertTrue(row['complete'])
        self.assertEqual(row['quality']['expectedDays'], 29)
        self.assertEqual(period_for(date(2024, 12, 23), '1M'), (date(2024, 12, 1), date(2025, 1, 1)))
        snap = daily_snapshot(date(2020, 1, 1), date(2020, 1, 8), server_ms=day_ms(date(2020, 1, 8)) + DAY_MS // 2)
        rows = build_bars(snap, 'BTCUSDT', '1w')['rows']
        self.assertEqual(rows[0]['date'], '2019-12-30')
        self.assertIn('outside_cache_range', rows[0]['errors'])
        self.assertEqual(rows[-1]['quality']['status'], 'pending')
        self.assertTrue(all(r['close'] is None for r in rows))

    @patch('market_multiframe.date_pillars', return_value=LABELS)
    @patch('market_multiframe.transitions_for', return_value=[])
    def test_any_gap_conflict_wrong_asset_invalid_or_unclosed_day_nulls_aggregate(self, *_):
        original = daily_snapshot(date(2024, 1, 1), date(2024, 1, 7))
        opened = day_ms(date(2024, 1, 4))
        for field, value in (('missing', None), ('conflicted', 1), ('symbol', 'ETHUSDT'), ('high', '1'), ('is_closed', 0)):
            snap = deepcopy(original)
            if field == 'missing':
                del snap[1][opened]
            else:
                snap[1][opened][field] = value
            with self.subTest(field=field):
                row = build_bars(snap, 'BTCUSDT', '1w')['rows'][0]
                self.assertFalse(row['complete'])
                self.assertEqual(row['quality']['validDays'], 6)
                self.assertIsNone(row['volume'])
                self.assertTrue(row['errors'])

    def test_real_intraday_lichun_changes_month_after_exact_term(self):
        first = date(2024, 2, 4)
        server = day_ms(first + timedelta(days=1))
        bars = {}
        for opened in range(day_ms(first), server, FOUR_HOURS):
            row = normalize_four_hour(raw_bar(opened), server, opened, opened)
            row.update(symbol='BTCUSDT', request_id='R', body_sha256='H')
            bars[opened] = row
        state = {'symbol': 'BTCUSDT', 'range_start': str(first), 'range_end': str(first), 'range_end_ms': server - FOUR_HOURS, 'server_ms': server}
        report = build_bars((state, bars, {}), 'BTCUSDT', '4h')
        rows = report['rows']
        self.assertEqual(rows[0]['pillars']['month'], '乙丑')
        self.assertEqual(rows[-1]['pillars']['month'], '丙寅')
        self.assertEqual(rows[0]['pillars']['day'], rows[-1]['pillars']['day'])
        self.assertNotIn('hour', rows[0]['pillars'])
        transition = next(x for x in report['transitions'] if x['changesMonth'])
        self.assertTrue('2024-02-04T16:' in transition['atLocal'])

    @patch('market_multiframe.date_pillars', return_value=LABELS)
    @patch('market_multiframe.transitions_for', return_value=[])
    def test_daily_aggregation_reads_one_atomic_snapshot_and_never_networks(self, *_):
        self.daily.snapshot = daily_snapshot(date(2024, 1, 1), date(2024, 1, 7))
        before = deepcopy(self.daily.snapshot)
        value = self.feed.bars('BTCUSDT', '1w')
        self.assertEqual(self.daily.calls, ['BTCUSDT'])
        self.assertEqual(self.daily.snapshot, before)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(value['source']['generationScope'], 'daily_cache')


if __name__ == '__main__':
    unittest.main()
