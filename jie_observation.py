"""Read-only Jie transition windows for display, separate from sealed scoring."""
from datetime import date, timedelta

from calendar_adapter import calculate_calendar
from month_study import episode_plan


def jie_windows(first: date, last: date, radius_days: int = 2):
    if first > last or not 0 <= radius_days <= 7:
        raise ValueError('交节观察范围无效')
    _, boundaries = episode_plan(first - timedelta(days=radius_days),
                                 last + timedelta(days=radius_days))
    events, days = [], []
    for term, instant in boundaries:
        transition_day = instant.date()
        if not first <= transition_day <= last:
            continue
        before = calculate_calendar((instant - timedelta(seconds=1)).isoformat())['pillars']['month']
        after = calculate_calendar(instant.isoformat())['pillars']['month']
        event = {'term': term, 'atLocal': instant.isoformat(), 'date': transition_day.isoformat(),
                 'beforeMonth': before, 'afterMonth': after}
        events.append(event)
        for offset in range(-radius_days, radius_days + 1):
            observed = transition_day + timedelta(days=offset)
            if first <= observed <= last:
                days.append({'date': observed.isoformat(), 'eventDate': event['date'],
                             'term': term, 'offsetDays': offset, 'atLocal': event['atLocal'],
                             'beforeMonth': before, 'afterMonth': after})
    return {'timezone': 'UTC+8', 'radiusDays': radius_days, 'events': events,
            'days': sorted(days, key=lambda item: (item['date'], item['eventDate']))}
