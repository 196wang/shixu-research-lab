"""Numerical/contract tests, not independent ephemeris precision validation."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest
from unittest.mock import patch

import astro_events as a

UTC = timezone.utc


class DateAndBoundaryTests(unittest.TestCase):
    def test_strict_input_and_length(self):
        invalid = [(None, '2026-01-01'), ('2026-1-01', '2026-01-01'),
                   ('2026-02-30', '2026-03-01'), ('2026-01-01T00:00', '2026-01-01'),
                   ('1991-12-31', '1992-01-01'), ('2099-12-31', '2100-01-01'),
                   ('2026-01-02', '2026-01-01'), ('2026-01-01', '2026-03-04')]
        for start, end in invalid:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                a.calculate_astro_events(start, end)
        lo, hi, info = a._parse_range('2026-01-01', '2026-03-03')
        self.assertEqual(info['days'], 62)
        self.assertEqual(lo.isoformat(), '2025-12-31T16:00:00+00:00')
        self.assertEqual((hi - lo).days, 62)

    def test_local_supported_endpoints(self):
        for day in ('1992-01-01', '2099-12-31'):
            result = a.calculate_astro_events(day, day)
            self.assertTrue(result['available'])
            self.assertEqual(result['range']['days'], 1)
            for event in result['events']:
                self.assertEqual(event['atLocal'][:10], day)
        lo, _, _ = a._parse_range('1992-01-01', '1992-01-01')
        self.assertEqual(lo.year, 1991)  # Local range, not UTC-year rejection.

    def test_wrap_exact_start_included_end_excluded(self):
        zero = datetime(2026, 1, 1, tzinfo=UTC)
        longitude = lambda at: ((at - zero).total_seconds() * 12 / 86400) % 360
        left = a._nakshatra_crossings(longitude, zero - timedelta(days=1), zero)
        right = a._nakshatra_crossings(longitude, zero, zero + timedelta(days=1))
        self.assertEqual(left, [])
        self.assertEqual(len(right), 1)
        self.assertEqual(right[0][0], zero)
        self.assertEqual(right[0][1], 0)  # Revati -> Ashwini.

    def test_near_midnight_not_rounded_out_of_window(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = start + timedelta(days=1)
        crossing = end - timedelta(seconds=0.25)
        longitude = lambda at: ((at - crossing).total_seconds() * 12 / 86400) % 360
        events = a._nakshatra_crossings(longitude, start, end)
        self.assertEqual(len(events), 1)
        at, _, width = events[0]
        self.assertTrue(crossing <= at < end)
        self.assertLessEqual((at - crossing).total_seconds(), a.TOLERANCE_SECONDS)
        self.assertLessEqual(width, a.TOLERANCE_SECONDS)

    def test_synthetic_adjacent_queries_are_partition(self):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        split, end = start + timedelta(days=1), start + timedelta(days=3)
        longitude = lambda at: ((at - split).total_seconds() * 12 / 86400) % 360
        whole = a._nakshatra_crossings(longitude, start, end)
        parts = a._nakshatra_crossings(longitude, start, split) + a._nakshatra_crossings(longitude, split, end)
        self.assertEqual(parts, whole)
        self.assertEqual(sum(at == split for at, _, _ in whole), 1)

    def test_discontinuity_rejected(self):
        lo = datetime(2026, 1, 1, tzinfo=UTC)
        for longitude in (lambda at: 10, lambda at: ((lo - at).total_seconds() / 7200) % 360,
                          lambda at: float('nan')):
            with self.assertRaises(a.AstroEventsUnavailable):
                a._nakshatra_crossings(longitude, lo, lo + timedelta(days=1))

    def test_duplicate_handling(self):
        at = datetime(2026, 1, 1, tzinfo=UTC)
        event = a._event('moon_phase', at, '0', '新月', 0.0007, 'astronomy')
        self.assertEqual(a._deduplicate([event, dict(event)]), [event])
        with self.assertRaises(a.AstroEventsUnavailable):
            a._deduplicate([event, {**event, 'label': 'different'}])


class RealEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = a.calculate_astro_events('2026-09-01', '2026-10-31')
        cls.astronomy, cls.indian, _ = a._load_engines()

    def test_real_counts_order_metadata_and_no_market_semantics(self):
        result = self.result
        events = result['events']
        self.assertEqual(result['counts'], {'moon_phase': 8, 'nakshatra_ingress': 61, 'total': 69})
        self.assertEqual(events, sorted(events, key=lambda e: (e['atUtc'], e['kind'], e['id'])))
        self.assertEqual(len(events), len({e['id'] for e in events}))
        self.assertIsNone(result['marketDirection'])
        self.assertFalse(result['marketValidated'])
        self.assertFalse(result['precision']['externalEphemerisPrecisionValidated'])
        self.assertIsNotNone(datetime.fromisoformat(result['generatedAtUtc']).tzinfo)
        for event in events:
            self.assertIsNone(event['direction'])
            self.assertLessEqual(event['numerical']['bracketWidthSeconds'], 0.001)
            precise = datetime.fromisoformat(event['atLocal'])
            link = datetime.fromisoformat(event['analysisAtLocal'])
            self.assertTrue(0 <= (link - precise).total_seconds() < 1)
            self.assertEqual(precise.astimezone(UTC), datetime.fromisoformat(event['atUtc']))
            self.assertTrue('2026-09-01' <= event['atLocal'][:10] <= '2026-10-31')

    def test_real_adjacent_queries_have_no_lost_or_duplicate_events(self):
        left = a.calculate_astro_events('2026-09-01', '2026-09-30')['events']
        right = a.calculate_astro_events('2026-10-01', '2026-10-31')['events']
        self.assertEqual(left + right, self.result['events'])

    def test_real_moon_phases_have_crossing_signs(self):
        for event in self.result['events']:
            if event['kind'] != 'moon_phase':
                continue
            at = datetime.fromisoformat(event['atUtc'])
            angles = [self.astronomy.MoonPhase(a._astro_time(self.astronomy, at + timedelta(seconds=step)))
                      for step in (-1, 1)]
            with self.subTest(id=event['id']):
                self.assertLess(a._angle_offset(angles[0], event['phaseAngleDeg']), 0)
                self.assertGreater(a._angle_offset(angles[1], event['phaseAngleDeg']), 0)

    def test_real_nakshatras_have_crossing_signs_and_new_name(self):
        indian = self.indian
        with indian.LOCK:
            swe = indian.swe
            swe.close()
            swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
            for event in self.result['events']:
                if event['kind'] != 'nakshatra_ingress':
                    continue
                at = datetime.fromisoformat(event['atUtc'])
                angles = []
                for step in (-1, 1):
                    instant = at + timedelta(seconds=step)
                    _, jd = swe.utc_to_jd(instant.year, instant.month, instant.day, instant.hour,
                                          instant.minute, instant.second + instant.microsecond / 1e6, swe.GREG_CAL)
                    angles.append(swe.calc_ut(jd, swe.MOON, indian.FLAGS)[0][0])
                with self.subTest(id=event['id']):
                    self.assertLess(a._angle_offset(angles[0], event['boundaryLongitudeDeg']), 0)
                    self.assertGreater(a._angle_offset(angles[1], event['boundaryLongitudeDeg']), 0)
                    self.assertEqual(event['from']['index'], int(angles[0] / a.NAKSHATRA_WIDTH))
                    self.assertEqual(event['to']['index'], int(angles[1] / a.NAKSHATRA_WIDTH))
                    self.assertEqual(event['to']['name'], indian.NAMES[event['to']['index']])

    def test_reset_isolation_and_actual_flags(self):
        with self.indian.LOCK:
            self.indian.swe.set_sid_mode(self.indian.swe.SIDM_FAGAN_BRADLEY, 0, 0)
        result = a.calculate_astro_events('2026-09-01', '2026-09-01')
        expected = [e for e in self.result['events'] if e['atLocal'][:10] == '2026-09-01']
        self.assertEqual(result['events'], expected)
        swe = self.indian.swe
        for flags in result['engines']['swiss']['returnedFlags']:
            self.assertEqual(flags & 7, swe.FLG_MOSEPH)
            self.assertTrue(flags & swe.FLG_SIDEREAL)
            self.assertTrue(flags & swe.FLG_SPEED)

    def test_configuration_and_source_hashes(self):
        result = self.result
        digest = hashlib.sha256(json.dumps(result['configuration'], ensure_ascii=False,
                                sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        self.assertEqual(result['configurationSha256'], digest)
        self.assertEqual(result['sourceHashes']['astro_events.py'], a._hash_file('astro_events.py'))
        self.assertEqual(result['sourceHashes']['swissBinary'], result['engines']['swiss']['binarySha256'])
        self.assertEqual(result['configuration']['nakshatra']['count'], 27)
        self.assertEqual(result['configuration']['nakshatra']['ayanamsaMode'], 1)

    def test_unavailable_engine_is_not_empty_success(self):
        original_import = __import__

        def unavailable(name, *args, **kwargs):
            if name == 'astronomy':
                raise ImportError('synthetic missing astronomy')
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=unavailable):
            with self.assertRaisesRegex(a.AstroEventsUnavailable, '依赖不可用'):
                a.calculate_astro_events('2026-09-01', '2026-09-01')

    def test_wrong_version_is_unavailable(self):
        with patch.object(a.metadata, 'version', return_value='wrong'):
            with self.assertRaisesRegex(a.AstroEventsUnavailable, '版本'):
                a.calculate_astro_events('2026-09-01', '2026-09-01')

    def test_wrong_swiss_flags_is_unavailable(self):
        with patch.object(self.indian.swe, 'calc_ut', return_value=([0, 0, 1, 12, 0, 0], 0, '')):
            with self.assertRaisesRegex(a.AstroEventsUnavailable, '标志'):
                a.calculate_astro_events('2026-09-01', '2026-09-01')


if __name__ == '__main__':
    unittest.main(verbosity=2)
