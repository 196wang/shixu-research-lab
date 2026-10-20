"""Tropical geocentric transit snapshot; no natal interpretation or forecasts.

Portable directory layout: this file alongside vendor/astronomy and its dist-info.
The installed Astronomy Engine 2.1.19 source uses GeoMoon directly for the Moon
inside GeoVector; its planetary light-time/aberration path does not apply there.
"""
from __future__ import annotations

from datetime import datetime, timezone
from importlib import metadata
import itertools
import json
import math
from pathlib import Path
import re
import sys

VENDOR = Path(__file__).resolve().parent / 'vendor'
sys.path.insert(0, str(VENDOR))
import astronomy  # noqa: E402

ADAPTER_VERSION = '0.5.0'
PINNED_ENGINE_VERSION = '2.1.19'
BODY_NAMES = ('Sun', 'Moon', 'Mercury', 'Venus', 'Mars', 'Jupiter',
              'Saturn', 'Uranus', 'Neptune', 'Pluto')
ASPECTS = ((0.0, 'conjunction'), (60.0, 'sextile'), (90.0, 'square'),
           (120.0, 'trine'), (180.0, 'opposition'))
ASPECT_ORB_DEG = 1.0
ISO_WITH_OFFSET = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?'
    r'(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])$')


def parse_instant(at: str) -> datetime:
    """Accept explicit ISO offset, normalize UTC; never assume a local zone."""
    if not isinstance(at, str) or not ISO_WITH_OFFSET.fullmatch(at):
        raise ValueError('at must be ISO date/time with Z or an explicit ±HH:MM offset')
    # RFC 3339 -00:00 can mean unknown local offset, not a known instant.
    if at.endswith('-00:00'):
        raise ValueError('unknown offset -00:00 is not accepted; use Z or +00:00')
    try:
        value = datetime.fromisoformat(at.replace('Z', '+00:00'))
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError('invalid ISO calendar date, time or offset') from exc


def angular_distance(a: float, b: float) -> float:
    """Smallest longitude separation, in degrees [0, 180]."""
    if not math.isfinite(a) or not math.isfinite(b):
        raise ValueError('angles must be finite')
    return abs((a - b + 180.0) % 360.0 - 180.0)


def longitude_aspects(positions: list[dict]) -> list[dict]:
    """Five geometric longitude angles; fixed research orb, no interpretation."""
    result = []
    for a, b in itertools.combinations(positions, 2):
        separation = angular_distance(a['longitudeDeg'], b['longitudeDeg'])
        for exact_angle, label in ASPECTS:
            deviation = abs(separation - exact_angle)
            if deviation <= ASPECT_ORB_DEG:
                result.append({'bodyA': a['body'], 'bodyB': b['body'],
                               'aspect': label, 'exactAngleDeg': exact_angle,
                               'separationDeg': separation, 'deviationDeg': deviation,
                               'orbDeg': ASPECT_ORB_DEG,
                               'status': 'geometric_label_only'})
    return result


def calculate_sky(at: str) -> dict:
    """Return ten-body geocentric sky positions for one explicit transit instant."""
    utc = parse_instant(at)
    engine_version = metadata.version('astronomy-engine')
    if engine_version != PINNED_ENGINE_VERSION:
        raise RuntimeError(f'Expected astronomy-engine {PINNED_ENGINE_VERSION}; got {engine_version}')
    time = astronomy.Time.Make(utc.year, utc.month, utc.day, utc.hour, utc.minute,
                               utc.second + utc.microsecond / 1_000_000)
    positions = []
    for name in BODY_NAMES:
        body = getattr(astronomy.Body, name)
        vector = astronomy.GeoVector(body, time, True)
        ecliptic = astronomy.Ecliptic(vector)
        longitude = ecliptic.elon % 360.0
        latitude = ecliptic.elat
        distance = math.sqrt(vector.x**2 + vector.y**2 + vector.z**2)
        if not (math.isfinite(longitude) and math.isfinite(latitude) and
                math.isfinite(distance) and -90.0 <= latitude <= 90.0 and distance > 0):
            raise RuntimeError(f'Non-finite or invalid coordinates for {name}')
        is_moon = name == 'Moon'
        positions.append({
            'body': name, 'longitudeDeg': longitude, 'latitudeDeg': latitude,
            'distanceAu': distance,
            'calculationPath': 'GeoVector(Moon)->GeoMoon->Ecliptic' if is_moon
                               else 'GeoVector(aberration=True)->Ecliptic',
            'corrections': {
                'planetaryLightTimePathApplied': not is_moon,
                'aberrationArgumentEffective': not is_moon,
                'note': 'Engine Moon branch directly uses GeoMoon at supplied time; '
                        'do not label it as the same corrected planetary path.' if is_moon
                        else 'Engine geocentric backdated position with aberration enabled.'
            }
        })
    return {
        'schemaVersion': 1, 'adapterVersion': ADAPTER_VERSION,
        'engine': {'name': 'Astronomy Engine', 'package': 'astronomy-engine',
                   'version': engine_version, 'license': 'MIT'},
        'snapshotType': 'transit_at_supplied_instant', 'inputAt': at,
        'time': {
            'utc': utc.isoformat(timespec='microseconds').replace('+00:00', 'Z'),
            'utDaysSinceJ2000': time.ut, 'ttDaysSinceJ2000': time.tt,
            'julianDayUt': time.ut + 2451545.0,
            'julianDayTt': time.tt + 2451545.0,
            'deltaTSeconds': (time.tt - time.ut) * 86400.0,
            'timeScaleNote': 'UT/TT are Astronomy Engine representations; UTC input '
                             'and the engine default Delta-T model are used. '
                             'No measured DUT1/EOP or external time-scale validation applied.'
        },
        'coordinates': {'origin': 'geocentric', 'zodiac': 'tropical',
                        'frame': 'true_ecliptic_of_date', 'anglesUnit': 'degree',
                        'ayanamsa': None, 'topocentric': False,
                        'moonCorrectionException': True},
        'positions': positions,
        'aspectSettings': {'anglesDeg': [x[0] for x in ASPECTS],
                           'orbDeg': ASPECT_ORB_DEG, 'orbInclusive': True,
                           'basis': 'minimum_longitude_separation_ignoring_latitude',
                           'selectionOrigin': 'project_research_configuration',
                           'applyingSeparating': 'not_calculated'},
        'aspects': longitude_aspects(positions),
        'interpretation': {'marketDirection': None, 'marketValidated': False,
                           'state': 'astronomical_snapshot_only'},
        'unavailable': ['natal_chart', 'asset_birth_chart', 'houses', 'ascendant',
                        'sidereal_zodiac', 'Indian_divisions', 'dasas', 'SBC',
                        'Latta', 'price_targets', 'validated_market_prediction'],
        'sourceRefs': ['S11', 'S12', 'S13'],
        'warnings': [
            'Supplied time is a transit snapshot, never an inferred asset/person birth time.',
            'No observer location is used; houses and ascendant are not produced.',
            'A named geometric aspect does not supply a financial direction or causal claim.',
            'Seasonal smoke checks are not external ephemeris precision validation.'
        ]
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('at', help='ISO instant such as 2026-09-27T12:00:00+08:00')
    args = parser.parse_args()
    print(json.dumps(calculate_sky(args.at), ensure_ascii=False, indent=2, allow_nan=False))
