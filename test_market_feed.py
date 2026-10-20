"""Deterministic public-feed fixtures: no live requests or user cache writes."""
from datetime import date, datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error
import ssl

from market_feed import (DAY_MS, MAX_PAGE_DAYS, MarketFeed, MarketFeedError, day_ms,
                         ms_day, normalize_bar, parse_query)
from market_feed import default_transport, safe_network_error


def response(payload, status=200, headers=None):
    return {'status': status, 'headers': headers or {}, 'body': json.dumps(payload).encode('utf-8')}


def bar(opened, close=101, volume=12):
    return [opened, '100', str(max(110, close)), '90', str(close), str(volume),
            opened + DAY_MS - 1, '1200', 20, '6', '600', '0']


class FakeAPI:
    def __init__(self, server_ms):
        self.server_ms = server_ms
        self.calls = []
        self.skip = set()
        self.overrides = {}
        self.extra = []
        self.fail_on_call = None
        self.failure_status = 500
        self.failure_headers = {}

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if self.fail_on_call == len(self.calls):
            return response({'msg': 'fixture failure'}, self.failure_status, self.failure_headers)
        if path == '/api/v3/time':
            return response({'serverTime': self.server_ms})
        rows = []
        for opened in range(params['startTime'], params['endTime'] + 1, DAY_MS):
            if opened in self.skip:
                continue
            rows.append(self.overrides.get((params['symbol'], opened), bar(opened)))
        rows += self.extra
        return response(rows)


class MarketFeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = lambda: datetime(2026, 9, 27, tzinfo=timezone.utc)
        self.api = FakeAPI(day_ms(date(2020, 1, 4)) + 12 * 3_600_000)
        self.feed = MarketFeed(self.tmp.name, self.api, self.clock)

    def tearDown(self):
        if self.feed.thread:
            self.feed.thread.join(5)
        self.tmp.cleanup()

    def test_ms_boundary_UTC8_and_closed_day_detection(self):
        opened = day_ms(date(2020, 1, 1))
        self.assertEqual(opened, 1577808000000)
        self.assertEqual(ms_day(opened), date(2020, 1, 1))
        value = normalize_bar(bar(opened), opened + DAY_MS, opened, opened)
        self.assertEqual(value['is_closed'], 1)
        value = normalize_bar(bar(opened), opened + DAY_MS - 1, opened, opened)
        self.assertEqual(value['is_closed'], 0)
        raw = bar(opened + 8 * 3_600_000)
        with self.assertRaisesRegex(ValueError, 'UTC8'):
            normalize_bar(raw, opened + DAY_MS, opened, opened + DAY_MS)

    def test_invalid_prices_volume_timestamp_and_ohlc_rejected(self):
        opened = day_ms(date(2020, 1, 1))
        for index, value in ((1, 'NaN'), (2, '1'), (3, '-1'), (4, True), (5, '-0.1'), (6, opened + DAY_MS), (0, opened // 1000)):
            raw = bar(opened)
            raw[index] = value
            with self.subTest(index=index, value=value), self.assertRaises(ValueError):
                normalize_bar(raw, opened + DAY_MS, opened, opened)
        self.assertEqual(normalize_bar(bar(opened, volume=0), opened + DAY_MS, opened, opened)['volume'], '0')

    def test_initial_pagination_bounded_and_correct_UTC_parameters(self):
        self.api.server_ms = day_ms(date(2020, 1, 1)) + 2501 * DAY_MS + DAY_MS // 2
        result = self.feed.refresh('BTCUSDT')
        self.assertEqual(result['job']['status'], 'succeeded')
        pages = [params for path, params in self.api.calls if path == '/api/v3/klines']
        self.assertEqual(len(pages), 3)
        self.assertEqual([p['startTime'] for p in pages], [day_ms(date(2020, 1, 1)) + i * 1000 * DAY_MS for i in range(3)])
        self.assertTrue(all(p['timeZone'] == '8' and p['interval'] == '1d' and p['limit'] == 1000 for p in pages))
        self.assertEqual(pages[-1]['endTime'], self.api.server_ms)
        with self.feed.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM candles WHERE symbol='BTCUSDT'").fetchone()[0], 2502)

    def test_raw_response_provenance_and_idempotent_candles(self):
        self.feed.refresh('BTCUSDT')
        self.feed.refresh('BTCUSDT')
        with self.feed.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candles').fetchone()[0], 4)
            captures = list(db.execute('SELECT * FROM feed_requests'))
            self.assertEqual(len(captures), 4)
            self.assertTrue(all(r['body_sha256'] == hashlib.sha256(r['body']).hexdigest() for r in captures))
            self.assertTrue(all(r['fetched'] and r['requested'] and json.loads(r['params']) is not None for r in captures))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candle_conflicts').fetchone()[0], 0)

    def test_current_open_bar_retained_but_hidden_then_closed_on_new_observation(self):
        self.feed.refresh('BTCUSDT')
        value = self.feed.history('BTCUSDT')
        self.assertEqual(value['coverage'], {'total': 4, 'complete': 3, 'missing': 0, 'pending': 1, 'conflicts': 0})
        current = value['rows'][-1]
        self.assertFalse(current['isClosed'])
        self.assertFalse(current['complete'])
        self.assertIsNone(current['volume'])
        self.assertIsNone(current['close'])
        self.assertEqual(current['errors'], ['not_closed'])
        self.api.server_ms += DAY_MS
        self.api.overrides[('BTCUSDT', day_ms(date(2020, 1, 4)))] = bar(day_ms(date(2020, 1, 4)), close=108)
        self.feed.refresh('BTCUSDT')
        value = self.feed.history('BTCUSDT')
        self.assertEqual(value['rows'][-2]['close'], 108)
        self.assertTrue(value['rows'][-2]['complete'])
        self.assertEqual(value['coverage']['conflicts'], 0)

    def test_gaps_keep_slots_and_incremental_refresh_attempts_old_gap(self):
        self.api.server_ms = day_ms(date(2020, 2, 1)) + DAY_MS // 2
        gap = day_ms(date(2020, 1, 3))
        self.api.skip = {gap}
        self.feed.refresh('BTCUSDT')
        history = self.feed.history('BTCUSDT')
        self.assertEqual(len(history['rows']), 32)
        self.assertEqual(history['rows'][2]['errors'], ['missing_bar'])
        self.assertIsNone(history['rows'][2]['open'])
        self.api.skip.clear()
        self.api.calls.clear()
        self.feed.refresh('BTCUSDT')
        pages = [params for path, params in self.api.calls if path == '/api/v3/klines']
        self.assertEqual(len(pages), 2)
        self.assertTrue(any(p['startTime'] == gap for p in pages))
        self.assertTrue(self.feed.history('BTCUSDT')['rows'][2]['complete'])

    def test_duplicate_conflict_masks_one_day_and_records_candidates(self):
        opened = day_ms(date(2020, 1, 2))
        self.api.extra = [bar(opened, close=109)]
        self.feed.refresh('BTCUSDT')
        history = self.feed.history('BTCUSDT')
        self.assertEqual(history['coverage']['conflicts'], 1)
        self.assertEqual(history['rows'][1]['errors'], ['source_conflict'])
        self.assertIsNone(history['rows'][1]['close'])
        with self.feed.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM candle_conflicts').fetchone()[0], 1)

    def test_closed_bar_revision_preserves_accepted_price_and_masks_conflict(self):
        self.feed.refresh('BTCUSDT')
        opened = day_ms(date(2020, 1, 2))
        self.api.overrides[('BTCUSDT', opened)] = bar(opened, close=109)
        self.feed.refresh('BTCUSDT')
        with self.feed.connect() as db:
            row = db.execute('SELECT close,conflicted FROM candles WHERE symbol=? AND open_ms=?', ('BTCUSDT', opened)).fetchone()
            self.assertEqual(float(row['close']), 101)
            self.assertEqual(row['conflicted'], 1)
        self.assertFalse(self.feed.history('BTCUSDT')['rows'][1]['complete'])

    def test_invalid_new_day_does_not_replace_valid_cache(self):
        self.feed.refresh('BTCUSDT')
        self.api.server_ms += DAY_MS
        raw = bar(day_ms(date(2020, 1, 5)))
        raw[1] = 'NaN'
        self.api.overrides[('BTCUSDT', day_ms(date(2020, 1, 5)))] = raw
        self.feed.refresh('BTCUSDT')
        history = self.feed.history('BTCUSDT')
        self.assertFalse(history['rows'][-1]['complete'])
        self.assertIn('invalid_open', history['rows'][-1]['errors'])
        self.assertEqual(history['rows'][0]['close'], 101)

    def test_network_failure_keeps_previous_cache_transactionally(self):
        self.feed.refresh('BTCUSDT')
        before = self.feed.history('BTCUSDT')
        self.api.server_ms += DAY_MS
        self.api.fail_on_call = len(self.api.calls) + 2
        result = self.feed.refresh('BTCUSDT')
        self.assertEqual(result['job']['status'], 'failed')
        after = self.feed.history('BTCUSDT')
        self.assertEqual(before['rows'], after['rows'])
        self.assertEqual(before['range'], after['range'])
        self.assertEqual(before['source']['generation'], after['source']['generation'])
        self.assertTrue(after['source']['lastError'])

    def test_rate_limit_is_persisted_shared_between_symbols_and_never_busy_retried(self):
        for status in (429, 418):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                api = FakeAPI(self.api.server_ms)
                api.fail_on_call, api.failure_status, api.failure_headers = 1, status, {'Retry-After': '123'}
                feed = MarketFeed(directory, api, self.clock)
                result = feed.refresh('BTCUSDT')
                self.assertEqual(result['job']['status'], 'rate_limited')
                self.assertEqual(len(api.calls), 1)
                self.assertEqual(result['job']['cooldownUntilUtc'], '2026-09-27T00:02:03+00:00')
                reopened = MarketFeed(directory, api, self.clock)
                with self.assertRaises(MarketFeedError) as raised:
                    reopened.refresh('ETHUSDT')
                self.assertEqual(raised.exception.status_code, 429)
                self.assertEqual(len(api.calls), 1)

    def test_ETH_cache_and_profile_isolation_no_implicit_BTC_substitution(self):
        self.assertFalse(self.feed.history('ETHUSDT')['available'])
        self.feed.refresh('BTCUSDT')
        self.assertFalse(self.feed.history('ETHUSDT')['available'])
        self.api.overrides[('ETHUSDT', day_ms(date(2020, 1, 1)))] = bar(day_ms(date(2020, 1, 1)), close=109)
        self.feed.refresh('ETHUSDT')
        self.assertEqual(self.feed.history('ETHUSDT')['rows'][0]['close'], 109)
        self.assertEqual(self.feed.history('BTCUSDT')['rows'][0]['close'], 101)
        self.assertEqual(self.feed.history('ETHUSDT')['volumeUnit'], 'ETH')
        a = self.feed.detail('ETHUSDT', '2020-01-01', 'ETH-BAZI-A')
        b = self.feed.detail('ETHUSDT', '2020-01-01', 'ETH-BAZI-B')
        self.assertEqual(a['calendar']['pillars'], b['calendar']['pillars'])
        self.assertNotEqual(a['features']['treasury']['wealthElement'], b['features']['treasury']['wealthElement'])
        with self.assertRaises(MarketFeedError):
            self.feed.detail('ETHUSDT', '2020-01-01', 'BTC-BZ-V01')

    def test_single_background_job_and_status_reads_never_fetch(self):
        entered, release = threading.Event(), threading.Event()
        api = self.api
        def blocked(path, params):
            entered.set()
            release.wait(5)
            return api(path, params)
        self.feed.transport = blocked
        result = self.feed.start_refresh('BTCUSDT')
        self.assertTrue(result['accepted'])
        self.assertTrue(entered.wait(2))
        self.assertIn(self.feed.status()['job']['status'], ('queued', 'running'))
        with self.assertRaises(MarketFeedError) as raised:
            self.feed.start_refresh('ETHUSDT')
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(len(api.calls), 0)
        release.set()
        self.feed.thread.join(5)
        before = len(api.calls)
        self.feed.status()
        self.feed.history('BTCUSDT')
        self.assertEqual(len(api.calls), before)

    def test_query_whitelist_and_server_time_regression(self):
        for query in ('symbol=BTCUSDT&symbol=', 'symbol=DOGEUSDT', 'symbol=BTCUSDT&x=1'):
            with self.assertRaises(MarketFeedError):
                parse_query(query)
        with self.assertRaises(MarketFeedError):
            parse_query('symbol=ETHUSDT&date=2020-01-01&profile=BTC-BZ-V01', detail=True)
        self.feed.refresh('BTCUSDT')
        previous = self.feed.history('BTCUSDT')['range']
        self.api.server_ms -= DAY_MS
        self.assertEqual(self.feed.refresh('BTCUSDT')['job']['status'], 'failed')
        self.assertEqual(self.feed.history('BTCUSDT')['range'], previous)

    def test_history_snapshot_cannot_mix_state_and_candles_from_concurrent_commit(self):
        self.feed.refresh('BTCUSDT')
        original_connect = self.feed.connect
        attempted_commit, finished = threading.Event(), threading.Event()
        writers = []
        def writer():
            with original_connect() as db:
                db.execute("UPDATE candles SET close='102' WHERE symbol='BTCUSDT'")
                db.execute("UPDATE symbol_state SET generation=generation+1 WHERE symbol='BTCUSDT'")
                attempted_commit.set()
            finished.set()
        def trace(sql):
            if 'SELECT c.*,r.body_sha256' in sql and not writers:
                thread = threading.Thread(target=writer)
                writers.append(thread)
                thread.start()
                attempted_commit.wait(2)
                # Without an explicit read transaction, the concurrent commit
                # would finish here before the candle SELECT starts reading.
                finished.wait(.05)
        @contextmanager
        def instrumented_connect():
            with original_connect() as db:
                db.set_trace_callback(trace)
                yield db
        self.feed.connect = instrumented_connect
        try:
            state, candles, _ = self.feed._snapshot('BTCUSDT')
        finally:
            self.feed.connect = original_connect
        writers[0].join(3)
        self.assertTrue(finished.is_set())
        self.assertEqual(state['generation'], 1)
        self.assertEqual(float(next(iter(candles.values()))['close']), 101)
        final_state, final_candles, _ = self.feed._snapshot('BTCUSDT')
        self.assertEqual(final_state['generation'], 2)
        self.assertEqual(float(next(iter(final_candles.values()))['close']), 102)

    def test_restart_marks_abandoned_job_failed_keeps_cache_and_allows_manual_retry(self):
        finished = self.feed.refresh('BTCUSDT')
        before = self.feed.history('BTCUSDT')
        # Reproduce the durable state left when a process exits mid-job.
        with self.feed.connect() as db:
            db.execute("UPDATE feed_jobs SET status='running',phase='daily_klines',finished=NULL WHERE id=?", (finished['job']['id'],))
            db.execute("UPDATE symbol_state SET status='running' WHERE symbol='BTCUSDT'")
        reopened = MarketFeed(self.tmp.name, self.api, self.clock)
        status = reopened.status()
        self.assertEqual(status['job']['status'], 'failed')
        self.assertEqual(status['job']['phase'], 'interrupted')
        self.assertIn('手动重试', status['job']['lastError'])
        after = reopened.history('BTCUSDT')
        self.assertEqual(before['rows'], after['rows'])
        self.assertEqual(before['source']['lastSuccessAtUtc'], after['source']['lastSuccessAtUtc'])
        self.assertEqual(reopened.refresh('BTCUSDT')['job']['status'], 'succeeded')

    def test_network_error_keeps_reason_type_and_redacts_sensitive_url(self):
        detail = safe_network_error(error.URLError(TimeoutError('timed out')))
        self.assertIn('URLError/TimeoutError', detail)
        self.assertIn('timeout=20s', detail)
        detail = safe_network_error(error.URLError(ssl.SSLCertVerificationError(1, 'certificate verify failed')))
        self.assertIn('TLS', detail)
        self.assertIn('certificate verify failed', detail)
        detail = safe_network_error(error.URLError('proxy https://name:credential@example.com/path?token=private password=hidden'))
        self.assertIn('example.com', detail)
        for secret in ('credential', 'private', 'hidden', 'name:'):
            self.assertNotIn(secret, detail)
        with patch('market_feed.request.build_opener') as opener:
            opener.return_value.open.side_effect = error.URLError(TimeoutError('fixture timeout'))
            with self.assertRaises(MarketFeedError) as raised:
                default_transport('/api/v3/time', {})
        self.assertIn('fixture timeout', str(raised.exception))


if __name__ == '__main__':
    unittest.main(verbosity=2)
