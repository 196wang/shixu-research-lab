"""Calendar browser boundary and read-only route checks; no scoring or real records."""
from datetime import timedelta
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error, parse, request

from calendar_adapter import calculate_calendar, parse_at
from calendar_browser import calendar_browser, parse_browser_query, term_at
from app_server import make_server


class CalendarBrowserTests(unittest.TestCase):
    def test_2024_lichun_exact_transition(self):
        boundary = term_at(2024, '立春')
        self.assertEqual(boundary.isoformat(), '2024-02-04T16:27:07+08:00')
        before = calendar_browser((boundary - timedelta(seconds=1)).isoformat())
        after = calendar_browser(boundary.isoformat())
        self.assertEqual(before['selected']['pillars']['year'], '癸卯')
        self.assertEqual(after['selected']['pillars']['year'], '甲辰')
        self.assertEqual(before['baziYear'], 2023)
        self.assertEqual(after['baziYear'], 2024)
        self.assertEqual(after['months'][0]['startAt'], boundary.isoformat())
        self.assertTrue(after['months'][0]['selected'])
        self.assertTrue(after['selected']['nearBoundaryWarnings'])

    def test_2024_jingzhe_exact_transition_and_card_metadata(self):
        boundary = term_at(2024, '惊蛰')
        self.assertEqual(boundary.isoformat(), '2024-03-05T10:22:45+08:00')
        before = calendar_browser((boundary - timedelta(seconds=1)).isoformat())
        after = calendar_browser(boundary.isoformat())
        self.assertEqual(before['selected']['pillars']['month'], '丙寅')
        self.assertEqual(after['selected']['pillars']['month'], '丁卯')
        day = next(d for d in after['days'] if d['date'] == '2024-03-05')
        transition = day['termTransitions'][0]
        self.assertEqual(transition['at'], boundary.isoformat())
        self.assertEqual((transition['beforePillar'], transition['afterPillar']), ('丙寅', '丁卯'))
        self.assertTrue(transition['changesMonth'])
        self.assertFalse(transition['changesYear'])
        self.assertTrue(after['months'][1]['selected'])

    def test_daily_noon_does_not_erase_afternoon_month_transition(self):
        data = calendar_browser('2024-02-04T18:00:00+08:00')
        day = next(d for d in data['days'] if d['date'] == '2024-02-04')
        self.assertEqual(day['pillars']['year'], '癸卯')
        self.assertEqual(data['selected']['pillars']['year'], '甲辰')
        self.assertTrue(day['hasYearBoundary'])
        self.assertTrue(day['hasMonthBoundary'])
        self.assertEqual(day['termTransitions'][0]['afterPillars']['year'], '甲辰')

    def test_zhongqi_does_not_change_month(self):
        data = calendar_browser('2024-03-20T12:00:00+08:00')
        day = data['days'][19]
        self.assertTrue(day['hasTermBoundary'])
        self.assertFalse(day['hasMonthBoundary'])
        self.assertEqual(day['termTransitions'][0]['term'], '春分')

    def test_month_days_follow_gregorian_leap_year(self):
        leap = calendar_browser('2024-02-29T12:00:00+08:00')
        common = calendar_browser('2023-02-10T12:00:00+08:00')
        self.assertEqual(len(leap['days']), 29)
        self.assertEqual(len(common['days']), 28)
        self.assertEqual(leap['days'][-1]['date'], '2024-02-29')
        self.assertTrue(leap['days'][-1]['selected'])

    def test_23_hour_uses_midnight_day_and_next_day_hour_stem(self):
        early = calendar_browser('2024-02-04T22:59:59+08:00')
        late = calendar_browser('2024-02-04T23:00:00+08:00')
        next_day = calendar_browser('2024-02-05T00:00:00+08:00')
        self.assertEqual(early['selected']['pillars']['day'], late['selected']['pillars']['day'])
        self.assertNotEqual(late['selected']['pillars']['day'], next_day['selected']['pillars']['day'])
        self.assertEqual(late['selected']['hourStemReference'], 'next_day_late_zi')
        self.assertEqual(late['selected']['pillars']['hour'], next_day['selected']['pillars']['hour'])

    def test_ranges_are_contiguous_and_navigation_pillars_match(self):
        data = calendar_browser('2026-09-27T12:00:00+08:00')
        self.assertEqual(len(data['years']), 11)
        self.assertEqual(len(data['months']), 12)
        for collection, key in ((data['years'], 'year'), (data['months'], 'month')):
            self.assertEqual(sum(c['selected'] for c in collection), 1)
            for a, b in zip(collection, collection[1:]):
                self.assertEqual(a['endAt'], b['startAt'])
            for card in collection:
                self.assertEqual(calculate_calendar(card['at'])['pillars'][key], card['pillar'])
                self.assertEqual(parse_at(card['at']) - parse_at(card['startAt']), timedelta(minutes=3))
        self.assertIsNone(data['marketDirection'])
        self.assertFalse(data['marketValidated'])

    def test_supported_edges_clip_but_never_offer_invalid_click(self):
        low = calendar_browser('1992-01-01T00:00:00+08:00')
        high = calendar_browser('2099-12-31T23:59:59+08:00')
        self.assertEqual(low['baziYear'], 1991)
        self.assertEqual(next(y for y in low['years'] if y['selected'])['year'], 1991)
        self.assertIsNone(low['navigation']['previousMonthAt'])
        self.assertIsNone(high['navigation']['nextMonthAt'])
        for data in (low, high):
            self.assertEqual(len(data['months']), 12)
            for card in data['years'] + data['months']:
                if card['available']:
                    parse_at(card['at'])
                else:
                    self.assertIsNone(card['at'])
                    self.assertIsNone(card['pillar'])
        self.assertTrue(any(not c['available'] for c in low['months']))
        self.assertTrue(any(not c['available'] for c in high['months']))

    def test_utc_input_normalizes_before_selecting_date(self):
        data = calendar_browser('2024-02-28T18:00:00Z')
        self.assertEqual(data['selected']['at'], '2024-02-29T02:00:00+08:00')
        self.assertEqual(data['selected']['day'], 29)

    def test_invalid_or_ambiguous_queries_rejected(self):
        at = parse.quote('2024-02-04T12:00:00+08:00')
        bad = ['', 'at=', 'at=2024-02-04T12:00:00', 'at=2024-02-04T12:00:00-00:00',
               'at=2024-02-04T12:00:00.01Z', 'at=2100-01-01T00:00:00Z',
               'at=1991-12-31T12:00:00Z', 'at=' + at + '&at=',
               'at=' + at + '&at=' + at, 'at=' + at + '&profile=BTC-BZ-V01',
               'at=2024-02-30T12:00:00Z', 'at=' + at + '&unexpected']
        for query in bad:
            with self.subTest(query=query), self.assertRaises(ValueError):
                parse_browser_query(query)
        self.assertEqual(parse_browser_query('at=' + at), '2024-02-04T12:00:00+08:00')

    def test_cached_objects_are_not_mutable_by_callers(self):
        at = '2024-06-01T12:00:00+08:00'
        first = calendar_browser(at)
        first['days'][0]['pillar'] = '破坏'
        self.assertNotEqual(calendar_browser(at)['days'][0]['pillar'], '破坏')


class CalendarBrowserRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = make_server(0, cls.tmp.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(5)
        cls.tmp.cleanup()

    def call(self, query):
        try:
            with request.urlopen(self.origin + '/api/calendar-browser?' + query, timeout=15) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_route_is_calendar_only_and_does_not_write_records(self):
        with (patch('app_server.calculate', side_effect=AssertionError('No full calculation')),
              patch('app_server.calculate_sky', side_effect=AssertionError('No sky lookup'))):
            status, data = self.call(parse.urlencode({'at': '2024-02-04T17:00:00+08:00'}))
        self.assertEqual(status, 200)
        self.assertEqual(data['version'], '0.9.0')
        self.assertEqual(data['selected']['pillars']['year'], '甲辰')
        self.assertEqual(self.server.store.list(), [])

    def test_route_rejects_duplicate_and_missing_offset(self):
        at = parse.quote('2024-02-04T17:00:00+08:00')
        for query in ('at=' + at + '&at=', 'at=2024-02-04T17:00:00'):
            self.assertEqual(self.call(query)[0], 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
