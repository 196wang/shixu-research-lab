"""Check event conventions against independently published 2026 timestamps."""
import unittest
from datetime import datetime, timezone

from build_astro_pattern_screen import mercury_stations, moon_events, full_hour_window


class AstroPatternEventsTest(unittest.TestCase):
    def test_mercury_station_matches_published_2026_almanac(self):
        events = mercury_stations(datetime(2026, 10, 20, tzinfo=timezone.utc),
                                  datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertEqual([e['kind'] for e in events], ['retrograde_start', 'direct_start'])
        # Astrodienst lists 2026-10-24 07:13 and 2026-11-13 15:54 UTC.
        targets = [datetime(2026, 10, 24, 7, 13, tzinfo=timezone.utc),
                   datetime(2026, 11, 13, 15, 54, tzinfo=timezone.utc)]
        for event, target in zip(events, targets):
            actual = datetime.fromisoformat(event['atUtc'])
            self.assertLess(abs((actual - target).total_seconds()), 5 * 60)

    def test_moon_events_match_usno(self):
        events = moon_events(datetime(2026, 9, 1, tzinfo=timezone.utc),
                             datetime(2026, 11, 1, tzinfo=timezone.utc))
        # US Naval Observatory 2026 phase table, UTC minute precision.
        targets = [('new_moon', datetime(2026, 9, 11, 3, 27, tzinfo=timezone.utc)),
                   ('full_moon', datetime(2026, 9, 26, 16, 49, tzinfo=timezone.utc)),
                   ('new_moon', datetime(2026, 10, 10, 15, 50, tzinfo=timezone.utc)),
                   ('full_moon', datetime(2026, 10, 26, 4, 12, tzinfo=timezone.utc))]
        self.assertEqual(len(events), len(targets))
        for event, (kind, target) in zip(events, targets):
            self.assertEqual(event['kind'], kind)
            self.assertLess(abs((datetime.fromisoformat(event['atUtc']) - target).total_seconds()), 60)

    def test_intraday_window_excludes_event_hour_and_missing_hours(self):
        instant = datetime(2026, 10, 24, 7, 13, tzinfo=timezone.utc)
        event_hour = int(instant.timestamp() // 3600) * 3600
        hourly = {event_hour + i * 3600: {'open': 100, 'close': 100,
                                           'high': 100, 'low': 100}
                  for i in range(-24, 25)}
        # The mixed hour could be an arbitrary price jump; it must be excluded.
        hourly[event_hour] = {'open': 1, 'close': 10000, 'high': 10000, 'low': 1}
        value = full_hour_window(hourly, instant)
        self.assertEqual(value['beforeReturnPct'], 0)
        self.assertEqual(value['afterReturnPct'], 0)
        self.assertEqual(value['afterRangePct'], 0)
        del hourly[event_hour + 3600]
        self.assertIsNone(full_hour_window(hourly, instant))
        hourly[event_hour + 3600] = {'open': 100, 'close': 100,
                                     'high': 100, 'low': 100}
        self.assertEqual(full_hour_window(hourly, instant.replace(minute=0))['afterReturnPct'], 0)


if __name__ == '__main__':
    unittest.main()
