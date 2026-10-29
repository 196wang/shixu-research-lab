import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from coinbase_feed import CoinbaseFeedError, CoinbaseMarketFeed, ScoringMarketRouter, local_open, parse_page
from prediction_ledger import PredictionLedger
from prediction_scoring import score_prediction


class CoinbaseFeedTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).resolve().parent / 'local_data' / ('test_coinbase_' + uuid.uuid4().hex)
        self.path.mkdir(parents=True)
        self.now = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        shutil.rmtree(self.path)

    @staticmethod
    def rows(start, end, omitted=None):
        omitted = omitted or set()
        stamps = range(int(start.timestamp()), int(end.timestamp()), 3600)
        return [[stamp, 90, 120, 100, 110, 1] for stamp in stamps if stamp not in omitted]

    def transport(self, omitted=None):
        def fetch(product, start, end):
            self.assertEqual(product, 'BTC-USD')
            return 200, json.dumps(self.rows(start, end, omitted)).encode()
        return fetch

    def test_complete_beijing_day_scores_usd_prediction(self):
        feed = CoinbaseMarketFeed(self.path, transport=self.transport(), clock=lambda: self.now)
        result = feed.refresh('BTCUSD', 1)
        self.assertEqual(result['complete'], 1)
        state, candles, gaps = feed._snapshot('BTCUSD')
        self.assertEqual(state['product'], 'BTC-USD')
        self.assertEqual(len(candles), 1)
        self.assertFalse(gaps)
        record = {'id': 'test', 'symbol': 'BTCUSD', 'kind': 'up', 'targetDate': '2026-10-16',
                  'horizonDays': 1, 'createdAtUtc': '2026-10-15T00:00:00+00:00',
                  'invalidation': None}
        score = score_prediction(record, (state, candles, gaps), self.now)
        self.assertEqual(score['status'], 'correct')
        self.assertEqual(score['dataQuality']['usableDays'], 1)
        self.assertEqual(feed.history('BTCUSD')['rows'][-1]['close'], '110')

    def test_intraday_order_uses_verified_hourly_sources(self):
        def fetch(product, start, end):
            rows = self.rows(start, end)
            rows[2][2] = 130
            rows[17][1] = 80
            return 200, json.dumps(rows).encode()
        feed = CoinbaseMarketFeed(self.path, transport=fetch, clock=lambda: self.now)
        feed.refresh('BTCUSD', 1)
        path = feed.intraday_paths('BTCUSD', [date(2026, 10, 16)])['2026-10-16']
        self.assertEqual((path['status'], path['extremeOrder']), ('verified_hourly', 'high_before_low'))
        self.assertEqual((path['highHourLocal'], path['lowHourLocal']), ('02:00', '17:00'))
        self.assertEqual((path['upsidePct'], path['downsidePct']), (30.0, -20.0))
        with feed.connect() as db:
            db.execute("UPDATE requests SET body=? WHERE symbol='BTCUSD'", (b'[]',))
        self.assertEqual(feed.intraday_paths('BTCUSD', [date(2026, 10, 16)])['2026-10-16']['status'],
                         'unavailable')

    def test_event_window_keeps_missing_and_future_days_blank(self):
        feed = CoinbaseMarketFeed(self.path, transport=self.transport(), clock=lambda: self.now)
        feed.refresh('BTCUSD', 1)
        window = feed.window('BTCUSD', '2026-10-16')
        self.assertEqual(len(window['rows']), 7)
        self.assertEqual(window['rows'][3]['status'], 'complete')
        self.assertEqual(window['rows'][3]['close'], '110')
        self.assertEqual(window['rows'][2]['status'], 'missing')
        self.assertIsNone(window['rows'][2]['close'])
        future = feed.window('BTCUSD', '2099-12-28')
        self.assertTrue(all(row['status'] == 'future' and row['close'] is None for row in future['rows']))

    def test_repair_fetches_only_missing_days_and_keeps_prior_candles(self):
        current = [self.now]
        calls = []
        def transport(product, start, end):
            calls.append((start, end))
            return 200, json.dumps(self.rows(start, end)).encode()
        feed = CoinbaseMarketFeed(self.path, transport=transport, clock=lambda: current[0])
        feed.refresh('BTCUSD', 1)
        original = feed.window('BTCUSD', '2026-10-16')['rows'][3]['sourceSha256']
        current[0] = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        result = feed.repair_range('BTCUSD', date(2026, 10, 16), date(2026, 10, 18))
        self.assertEqual((result['requestedDays'], result['complete'], result['missing']), (2, 3, 0))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][0], local_open(date(2026, 10, 17)).astimezone(timezone.utc))
        self.assertEqual(feed.window('BTCUSD', '2026-10-16')['rows'][3]['sourceSha256'], original)
        self.assertEqual(feed.repair_range('BTCUSD', date(2026, 10, 16), date(2026, 10, 18))['requestedDays'], 0)
        self.assertEqual(len(calls), 2)

    def test_failed_repair_records_error_without_losing_cached_day(self):
        current = [self.now]
        feed = CoinbaseMarketFeed(self.path, transport=self.transport(), clock=lambda: current[0])
        feed.refresh('BTCUSD', 1)
        current[0] = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
        attempts = []
        def unavailable(*_):
            attempts.append(1)
            raise CoinbaseFeedError('Coinbase 网络代理无法连接；未写入不完整行情')
        feed.transport = unavailable
        with patch('coinbase_feed.clock_time.sleep'):
            with self.assertRaises(CoinbaseFeedError):
                feed.repair_range('BTCUSD', date(2026, 10, 16), date(2026, 10, 18))
        self.assertEqual(len(attempts), 2)
        status = next(item for item in feed.status()['symbols'] if item['symbol'] == 'BTCUSD')
        self.assertIn('代理无法连接', status['lastError'])
        self.assertIsNotNone(status['lastAttemptAtUtc'])
        self.assertEqual(status['completeDays'], 1)

    def test_missing_hour_is_not_scored_as_complete(self):
        missing = {int(datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc).timestamp())}
        feed = CoinbaseMarketFeed(self.path, transport=self.transport(missing), clock=lambda: self.now)
        result = feed.refresh('BTCUSD', 1)
        self.assertEqual(result['complete'], 0)
        self.assertEqual(result['missing'], 1)
        self.assertEqual(next(iter(feed._snapshot('BTCUSD')[2].values())), ['missing_hour'])

    def test_page_rejects_bad_prices(self):
        start = datetime(2026, 10, 15, 16, tzinfo=timezone.utc)
        with self.assertRaises(Exception):
            parse_page(json.dumps([[int(start.timestamp()), 110, 90, 100, 100, 1]]).encode(),
                       start, start + timedelta(hours=1))

    def test_ledger_routes_usd_to_coinbase_and_scores_after_target(self):
        current = [datetime(2026, 10, 15, 0, 0, tzinfo=timezone.utc)]
        feed = CoinbaseMarketFeed(self.path, transport=self.transport(), clock=lambda: current[0])
        ledger = PredictionLedger(self.path, ScoringMarketRouter(None, feed), clock=lambda: current[0])
        created = ledger.create({'symbol': 'BTCUSD', 'kind': 'up', 'targetDate': '2026-10-16',
                                 'horizonDays': 1, 'title': '测试目标日上涨', 'note': '临时账本',
                                 'rules': [], 'invalidation': None})['record']
        current[0] = self.now
        feed.refresh('BTCUSD', 1)
        ledger.evaluate()
        reports = ledger.export()['reports']
        report = next(r for r in reports if r['recordId'] == created['id'])
        self.assertEqual(report['score']['status'], 'correct')
        self.assertEqual(report['marketSnapshot']['state']['provider'], 'Coinbase Exchange')

    def test_equal_price_tokens_do_not_create_false_conflicts(self):
        start = datetime(2026, 10, 15, 16, tzinfo=timezone.utc)
        a = parse_page(json.dumps([[int(start.timestamp()), 90, 120, 100, 110, 1]]).encode(), start, start + timedelta(hours=1))
        b = parse_page(json.dumps([[int(start.timestamp()), 90.0, 120.00, 100.0, 110.0, 1.00]]).encode(), start, start + timedelta(hours=1))
        self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
