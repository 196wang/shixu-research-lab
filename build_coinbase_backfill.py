"""Resumable, bounded Coinbase Exchange hourly backfill for the local cache.

Usage: python build_coinbase_backfill.py --data-dir PATH --start 2022-01-01
       --end 2026-07-18. Completed Beijing days already cached are skipped.
"""
from __future__ import annotations

from argparse import ArgumentParser
from datetime import date, timedelta
import json
from pathlib import Path
import time

from coinbase_feed import CoinbaseFeedError, CoinbaseMarketFeed, default_transport, local_open, ms

MAX_SPAN = 180


def courteous_transport(product, start, end):
    for attempt in range(5):
        try:
            status, body = default_transport(product, start, end)
            if status not in (429, 500, 502, 503, 504):
                time.sleep(0.12)
                return status, body
        except CoinbaseFeedError:
            if attempt == 4:
                raise
        if attempt == 4:
            raise CoinbaseFeedError(f'Coinbase HTTP {status} after retries')
        time.sleep(min(2 ** attempt, 12))
    raise AssertionError('unreachable')


def run(data_dir, first, last, symbols=('BTCUSD', 'ETHUSD')):
    feed = CoinbaseMarketFeed(data_dir, transport=courteous_transport)
    totals = {}
    for symbol in symbols:
        _, candles, gaps = feed._snapshot(symbol)
        cursor, chunks, skipped = first, [], 0
        while cursor <= last:
            stop = min(last, cursor + timedelta(days=MAX_SPAN - 1))
            days = [cursor + timedelta(days=i) for i in range((stop - cursor).days + 1)]
            if all(ms(local_open(day)) in candles and not candles[ms(local_open(day))]['conflicted']
                   and ms(local_open(day)) not in gaps for day in days):
                skipped += len(days)
            else:
                result = feed.refresh_range(symbol, cursor, stop)
                chunks.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                # Refresh the snapshot so an interrupted rerun can skip completed spans.
                _, candles, gaps = feed._snapshot(symbol)
            cursor = stop + timedelta(days=1)
        totals[symbol] = {'chunksFetched': len(chunks), 'daysSkipped': skipped,
                          'cachedCompleteDays': sum(r['is_closed'] and not r['conflicted'] for r in candles.values()),
                          'knownGapDays': len(gaps)}
    print(json.dumps({'done': totals}, ensure_ascii=False), flush=True)
    return totals


if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--symbol', choices=('BTCUSD', 'ETHUSD', 'both'), default='both')
    args = parser.parse_args()
    if args.start > args.end or args.end >= date.today() + timedelta(days=2):
        parser.error('date range invalid')
    run(args.data_dir, args.start, args.end,
        ('BTCUSD', 'ETHUSD') if args.symbol == 'both' else (args.symbol,))
