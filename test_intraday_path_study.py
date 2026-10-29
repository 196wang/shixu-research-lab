import unittest
from datetime import date

from build_bitstamp_long_history import canonical, sha
from build_intraday_path_study import day_profile, matched


def sample_day(*, high_hours=(2,), low_hours=(17,)):
    rows = []
    for hour in range(24):
        rows.append({'timestamp': str(1600000000 + hour * 3600), 'open': '100',
                     'high': '110' if hour in high_hours else '101',
                     'low': '90' if hour in low_hours else '99',
                     'close': '100', 'volume': '1'})
    # Use the Beijing midnight timestamps required by the production builder.
    from datetime import datetime, time, timedelta, timezone
    local = timezone(timedelta(hours=8))
    first = int(datetime.combine(date(2026, 10, 15), time(), local).timestamp())
    hours = {first + i * 3600: row for i, row in enumerate(rows)}
    daily = {'open': '100', 'high': '110', 'low': '90', 'close': '100',
             'sourceHourSha256': sha(canonical(rows))}
    return daily, hours


class IntradayPathStudyTests(unittest.TestCase):
    def test_extreme_order_and_same_hour_ambiguity(self):
        daily, hours = sample_day()
        result = day_profile(date(2026, 10, 15), daily, hours)
        self.assertEqual(result['extremeOrder'], 'high_first')
        self.assertEqual((result['upExcursionPct'], result['downExcursionPct']), (10.0, 10.0))
        daily, hours = sample_day(high_hours=(2, 17), low_hours=(17,))
        self.assertEqual(day_profile(date(2026, 10, 15), daily, hours)['extremeOrder'], 'ambiguous')

    def test_daily_hash_change_rejected(self):
        daily, hours = sample_day()
        daily['sourceHourSha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
            day_profile(date(2026, 10, 15), daily, hours)

    def test_control_stays_in_same_year_and_solar_month(self):
        def row(year, episode, triggered, up_bias):
            return {'year': year, 'episodeId': episode, 'triggered': triggered,
                    'upBias': up_bias, 'downBias': not up_bias, 'biasTie': False,
                    'extremeOrder': 'low_first' if up_bias else 'high_first',
                    'upExcursionPct': 2 if up_bias else 1, 'downExcursionPct': 1 if up_bias else 2,
                    'rangePct': 3, 'returnPct': 0}
        rows = [row(2020, 'a', True, True), row(2020, 'a', False, False),
                row(2020, 'b', True, False), row(2020, 'b', False, True),
                row(2021, 'a', True, True)]
        result = matched(rows, lambda item: item['triggered'], 'up')
        self.assertEqual(result['trigger']['n'], 2)
        self.assertEqual(result['matchedControl']['favorableBiasRatePct'], 50.0)
        self.assertEqual(result['unmatchedTriggerDays'], 1)


if __name__ == '__main__':
    unittest.main()
