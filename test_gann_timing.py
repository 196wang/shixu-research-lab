"""Synthetic time/pivot cases; no network, production cache, or scoring."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gann_timing as g


START = date(2026, 9, 1)


def make_row(index, high='100', low='90', symbol='BTCUSDT', **changes):
    opened = g.day_open(START + timedelta(days=index))
    key = int(opened.timestamp() * 1000)
    midpoint = str((Decimal(high) + Decimal(low)) / 2)
    row = {'symbol': symbol, 'open_ms': key, 'close_ms': key + g.DAY_MS - 1,
           'open': midpoint, 'high': high, 'low': low, 'close': midpoint,
           'volume': '0', 'is_closed': 1, 'conflicted': 0,
           'request_id': 'fixture-' + str(index), 'body_sha256': 'a' * 64,
           'updated': (opened + timedelta(days=1)).isoformat()}
    row.update(changes)
    return key, row


class Market:
    def __init__(self, days=15, symbol='BTCUSDT'):
        self.state = {'generation': 7, 'last_success': '2026-09-27T04:00:00+00:00', 'symbol': symbol}
        self.candles = dict(make_row(i, symbol=symbol) for i in range(days))
        self.gaps, self.calls = {}, []

    def put(self, i, **values):
        key, row = make_row(i, symbol=self.state['symbol'], **values)
        self.candles[key] = row

    def _snapshot(self, symbol):
        self.calls.append(symbol)
        return deepcopy(self.state), deepcopy(self.candles), deepcopy(self.gaps)


class GannTimingTests(unittest.TestCase):
    def calc(self, market, days=15, now='2026-10-01T00:00:00+08:00', start='2026-09-01', symbol=None):
        end = (date.fromisoformat(start) + timedelta(days=days - 1)).isoformat()
        return g.calculate_gann(market, symbol or market.state['symbol'], start, end,
                                clock=lambda: datetime.fromisoformat(now))

    def test_decimal_strict_pivot_and_precise_price(self):
        market = Market(5)
        market.put(2, high='100.000000000000000001')
        result = self.calc(market, 5)
        self.assertEqual(len(result['anchors']), 1)
        self.assertEqual(result['anchors'][0]['type'], 'high')
        self.assertEqual(result['anchors'][0]['price'], '100.000000000000000001')
        self.assertEqual(len(result['anchors'][0]['sourceRefs']), 5)

    def test_platform_equal_high_or_low_does_not_trigger(self):
        market = Market(7)
        market.put(2, high='110', low='80')
        market.put(3, high='110', low='80')
        self.assertEqual(self.calc(market, 7)['anchors'], [])

    def test_confirmation_is_right_second_bar_next_open(self):
        market = Market(5)
        market.put(2, high='110')
        before = self.calc(market, 5, now='2026-09-05T23:59:59.999+08:00')
        self.assertEqual(before['anchors'], [])
        result = self.calc(market, 5, now='2026-09-06T00:00:00+08:00')
        anchor = result['anchors'][0]
        self.assertEqual(anchor['date'], '2026-09-03')
        self.assertEqual(anchor['knownAt'], '2026-09-06T00:00:00+08:00')
        self.assertEqual(anchor['knownAtUtc'], '2026-09-05T16:00:00+00:00')
        self.assertEqual(anchor['confirmationDate'], '2026-09-05')

    def test_no_future_or_unclosed_neighbors_even_when_cached_as_closed(self):
        market = Market(10)
        market.put(2, high='110')
        market.put(7, low='80')
        result = self.calc(market, 10, now='2026-09-06T00:00:00+08:00')
        self.assertEqual([a['date'] for a in result['anchors']], ['2026-09-03'])
        self.assertEqual(result['dataQuality']['validDays'], 5)
        self.assertEqual(result['pairs'], [])
        self.assertTrue(any('future_bar' in row['reasons'] for row in result['dataQuality']['excluded']))

    def test_gap_breaks_segment_and_does_not_pair_across(self):
        market = Market(11)
        market.put(2, high='110')
        market.put(8, low='80')
        del market.candles[make_row(5)[0]]
        result = self.calc(market, 11)
        self.assertEqual(len(result['anchors']), 2)
        self.assertEqual(len(result['dataQuality']['segments']), 2)
        self.assertEqual(result['pairs'], [])
        self.assertEqual(result['dataQuality']['excluded'][0], {'date': '2026-09-06', 'reasons': ['missing_bar']})

    def test_conflict_unclosed_invalid_price_boundary_and_wrong_asset_break_segment(self):
        for changes in ({'conflicted': 1}, {'is_closed': 0}, {'high': 'NaN'},
                        {'close': '1000'}, {'close_ms': 1}, {'updated': '2030-01-01T00:00:00+08:00'}):
            with self.subTest(changes=changes):
                market = Market(11)
                market.put(2, high='110')
                market.put(8, low='80')
                key, row = make_row(5)
                row.update(changes)
                market.candles[key] = row
                result = self.calc(market, 11)
                self.assertEqual(result['dataQuality']['excludedDays'], 1)
                self.assertEqual(result['pairs'], [])
        market = Market(11)
        market.candles[make_row(5)[0]]['symbol'] = 'ETHUSDT'
        self.assertIn('wrong_symbol', self.calc(market, 11)['dataQuality']['excluded'][0]['reasons'])

    def test_ambiguous_high_and_low_is_retained_and_breaks_pairing(self):
        market = Market(15)
        market.put(2, low='80')
        market.put(7, high='120', low='70')
        market.put(12, high='110')
        result = self.calc(market)
        self.assertEqual([a['type'] for a in result['anchors']], ['low', 'ambiguous', 'high'])
        self.assertIsNone(result['anchors'][1]['price'])
        self.assertFalse(result['anchors'][1]['eligibleForPairing'])
        self.assertEqual(result['dataQuality']['ambiguousAnchorCount'], 1)
        self.assertEqual(result['pairs'], [])

    def test_consecutive_same_type_not_skipped_or_replaced_by_more_extreme(self):
        market = Market(15)
        market.put(2, high='120')
        market.put(7, high='110')
        market.put(12, low='80')
        result = self.calc(market)
        self.assertEqual([a['type'] for a in result['anchors']], ['high', 'high', 'low'])
        self.assertEqual(len(result['pairs']), 1)
        self.assertEqual(result['pairs'][0]['anchorADate'], '2026-09-08')
        self.assertEqual(result['pairs'][0]['intervalDays'], 5)

    def test_all_pairs_retained_and_statuses_have_no_direction(self):
        market = Market(20)
        for i, values in ((2, {'low': '80'}), (7, {'high': '110'}), (12, {'low': '80'}), (17, {'high': '110'})):
            market.put(i, **values)
        result = self.calc(market, 20, now='2026-09-23T00:00:00+08:00')
        self.assertEqual(len(result['pairs']), 3)
        self.assertEqual(len(result['projections']), 6)
        last_one, last_two = result['projections'][-2:]
        self.assertEqual(last_one['targetDate'], '2026-09-23')
        self.assertEqual(last_one['status'], 'historical')  # equality is already historical
        self.assertEqual(last_two['targetDate'], '2026-09-28')
        self.assertEqual(last_two['status'], 'future_candidate')
        for item in result['projections']:
            self.assertIsNone(item['direction'])
            self.assertFalse(item['registeredPrediction'])
        self.assertFalse(result['settings']['scoringPerformed'])

    def test_retrospective_projection_at_confirmation_boundary(self):
        market = Market(8)
        market.put(2, high='110')
        market.put(5, low='80')
        result = self.calc(market, 8, now='2026-09-09T00:00:00+08:00')
        first, second = result['projections']
        self.assertEqual(first['targetAtLocal'], first['knownAt'])
        self.assertEqual(first['status'], 'retrospective')
        self.assertTrue(first['historical'])
        self.assertEqual(second['status'], 'future_candidate')

    def test_requested_range_has_no_padding_and_inclusive_180_day_limit(self):
        market = Market(10)
        market.put(2, high='110')
        result = self.calc(market, 4, start='2026-09-02')
        self.assertEqual(result['anchors'], [])
        self.assertEqual(result['dataQuality']['requestedDays'], 4)
        start = date(2024, 1, 1)
        end = (start + timedelta(days=179)).isoformat()
        result = g.calculate_gann(Market(0), 'BTCUSDT', start.isoformat(), end)
        self.assertEqual(result['dataQuality']['requestedDays'], 180)
        result = g.calculate_gann(Market(0), 'BTCUSDT', '2024-02-28', '2024-03-01')
        self.assertEqual([r['date'] for r in result['dataQuality']['excluded']], ['2024-02-28', '2024-02-29', '2024-03-01'])
        for args in [('BTCUSDT', '2024-01-01', '2024-06-29'), ('DOGEUSDT', '2024-01-01', '2024-01-02'),
                     ('BTCUSDT', '2024-01-02', '2024-01-01'), ('BTCUSDT', '20240101', '2024-01-02'),
                     ('BTCUSDT', '2019-12-31', '2020-01-01')]:
            with self.assertRaises(g.GannTimingError) as raised:
                g.calculate_gann(market, *args)
            self.assertEqual(raised.exception.status_code, 400)

    def test_eth_and_btc_are_isolated_and_snapshot_is_read_only(self):
        market = Market(5, 'ETHUSDT')
        market.put(2, high='110')
        initial = deepcopy((market.state, market.candles, market.gaps))
        result = self.calc(market, 5)
        self.assertEqual(market.calls, ['ETHUSDT'])
        self.assertEqual(initial, (market.state, market.candles, market.gaps))
        self.assertTrue(result['anchors'][0]['id'].startswith('ETHUSDT-'))
        self.assertEqual(result['sources'][0]['marketGeneration'], 7)
        self.assertEqual(len(result['sources'][0]['snapshotSha256']), 64)
        json.dumps(result, allow_nan=False)

    def test_bad_snapshot_clock_and_gap_metadata_fail_or_explicitly_exclude(self):
        class Broken:
            def _snapshot(self, symbol):
                raise RuntimeError('fixture')
        with self.assertRaises(g.GannTimingError) as raised:
            self.calc(Broken(), 5, symbol='BTCUSDT')
        self.assertEqual(raised.exception.status_code, 503)
        with self.assertRaises(g.GannTimingError):
            g.calculate_gann(Market(5), 'BTCUSDT', '2026-09-01', '2026-09-05', clock=lambda: datetime(2026, 10, 1))
        market = Market(5)
        market.gaps[make_row(2)[0]] = ['source_conflict']
        result = self.calc(market, 5)
        self.assertEqual(result['dataQuality']['excluded'][0]['reasons'], ['source_conflict'])
        self.assertEqual(result['anchors'], [])

    def test_bundled_spec_tamper_fails_and_output_tracks_calculation_version(self):
        market = Market(5)
        result = self.calc(market, 5)
        self.assertEqual(result['settingsSha256'], g.snapshot_hash(result['settings']))
        self.assertEqual(len(result['codeSha256']), 64)
        self.assertEqual(result['sources'][1]['path'], '来源溯源/v0.14/江恩计算规范.md')
        with tempfile.TemporaryDirectory() as temp:
            altered = Path(temp) / 'spec.md'
            altered.write_text('altered specification', encoding='utf-8')
            with patch.object(g, 'SPEC_PATH', altered):
                with self.assertRaises(g.GannTimingError) as raised:
                    self.calc(market, 5)
                self.assertEqual(raised.exception.status_code, 503)
                self.assertIn('哈希不匹配', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
