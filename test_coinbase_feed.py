import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import unittest
import uuid

from coinbase_feed import CoinbaseMarketFeed, ScoringMarketRouter, parse_page
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
