"""Deterministic divisions and a candidate Vimshottari timeline.

The caller supplies an explicit sidereal longitude and an aware birth instant.
This module does not compute ephemerides, choose an ayanamsa or asset birth,
interpret houses, or make financial predictions. Standard library only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import math
import re

VERSION = "0.5.0"
DIVISION_CONFIG_ID = "IA-PARASHARA-D9-EXACT-01"
VIMSHOTTARI_CONFIG_ID = "IA-VIM-MOON-3652425-CLIPFULL-01"
SIGNS = ("Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
         "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces")
SIGN_NAMES_ZH = ("白羊", "金牛", "双子", "巨蟹", "狮子", "处女",
                 "天秤", "天蝎", "射手", "摩羯", "水瓶", "双鱼")
ELEMENTS = ("fire", "earth", "air", "water")
D9_ELEMENT_STARTS = (0, 9, 6, 3)
LORDS = ("Ketu", "Venus", "Sun", "Moon", "Mars", "Rahu", "Jupiter", "Saturn", "Mercury")
LORD_YEARS = (7, 20, 6, 10, 7, 18, 16, 19, 17)
NAKSHATRA_WIDTH_DEG = Fraction(40, 3)
PADA_WIDTH_DEG = Fraction(10, 3)
YEAR_DAYS = Fraction(146097, 400)  # exactly 365.2425; fixed research convention
DAY_SECONDS = 86400
YEAR_SECONDS = YEAR_DAYS * DAY_SECONDS
MICROSECONDS_PER_SECOND = 1_000_000
ISO_WITH_OFFSET = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]{1,6})?)?"
    r"(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)


def _exact_text(value: Fraction | int) -> str:
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _longitude(value) -> Fraction:
    """Parse without epsilon snapping, binary-floor corrections or normalization."""
    if isinstance(value, bool):
        raise ValueError("longitude must not be bool")
    try:
        if isinstance(value, Fraction):
            exact = value
        elif isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("longitude must be finite")
            exact = Fraction(value)
        elif isinstance(value, int):
            exact = Fraction(value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("longitude must be finite")
            exact = Fraction(repr(value))
        elif isinstance(value, str):
            exact = Fraction(value)
        else:
            raise ValueError("longitude must be numeric or an exact decimal/fraction string")
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(f"invalid longitude: {exc}") from exc
    if not 0 <= exact < 360:
        raise ValueError("longitude must satisfy 0 <= longitude < 360; no implicit wrapping")
    return exact


def _sign_record(index: int, within: Fraction) -> dict:
    return {"signIndex": index, "signNumber": index + 1, "sign": SIGNS[index],
            "signZh": SIGN_NAMES_ZH[index], "degreeInSignExact": _exact_text(within),
            "degreeInSignApprox": float(within)}


def calculate_divisions(longitude_deg) -> dict:
    """D1, 27-nakshatra/four-pada and standard elemental-start Parashara D9.

    Angle boundaries are exact rational, left closed and right open. Int, finite
    float, Decimal, Fraction and decimal/fraction strings are accepted. Float is
    interpreted as its repr decimal value; use Fraction or e.g. '10/3' to provide
    a mathematically exact repeating-decimal boundary.
    """
    longitude = _longitude(longitude_deg)
    sign_index = longitude // 30
    within_sign = longitude - sign_index * 30
    navamsa_part_index = within_sign // PADA_WIDTH_DEG
    element_index = sign_index % 4
    d9_sign = (D9_ELEMENT_STARTS[element_index] + navamsa_part_index) % 12
    d9_longitude = longitude * 9 % 360
    d9_within = d9_longitude - d9_sign * 30
    nakshatra_index = longitude // NAKSHATRA_WIDTH_DEG
    within_nakshatra = longitude - nakshatra_index * NAKSHATRA_WIDTH_DEG
    fraction_elapsed = within_nakshatra / NAKSHATRA_WIDTH_DEG
    pada_index = within_nakshatra // PADA_WIDTH_DEG
    return {
        "schemaVersion": "1.0", "moduleVersion": VERSION, "configurationId": DIVISION_CONFIG_ID,
        "input": {"longitudeDegExact": _exact_text(longitude), "longitudeDegApprox": float(longitude),
                  "coordinateAssumption": "caller_supplied_sidereal_ecliptic_longitude",
                  "ayanamsa": "already_applied_by_caller; not_selected_here"},
        "boundaryPolicy": "exact_rational_left_closed_right_open; no_epsilon_snapping",
        "d1": {**_sign_record(sign_index, within_sign), "element": ELEMENTS[element_index]},
        "nakshatra": {
            "index": nakshatra_index, "number": nakshatra_index + 1,
            "widthDegExact": _exact_text(NAKSHATRA_WIDTH_DEG),
            "degreeWithinExact": _exact_text(within_nakshatra),
            "fractionElapsedExact": _exact_text(fraction_elapsed),
            "fractionRemainingExact": _exact_text(1 - fraction_elapsed),
            "padaIndex": pada_index, "padaNumber": pada_index + 1,
            "padaWidthDegExact": _exact_text(PADA_WIDTH_DEG),
            "vimshottariLord": LORDS[nakshatra_index % 9],
        },
        "d9": {
            **_sign_record(d9_sign, d9_within),
            "longitudeDegExact": _exact_text(d9_longitude), "longitudeDegApprox": float(d9_longitude),
            "partIndexWithinD1": navamsa_part_index, "partNumberWithinD1": navamsa_part_index + 1,
            "elementStartSign": SIGNS[D9_ELEMENT_STARTS[element_index]],
            "variant": "Parashara_element_starts_Aries_Capricorn_Libra_Cancer_9_equal_parts",
            "equivalentFormula": "D9 longitude = (9 * supplied sidereal longitude) mod 360",
        },
        "financialPrediction": None, "marketValidated": False,
    }


def _birth_utc(birth_at_iso: str) -> datetime:
    if not isinstance(birth_at_iso, str) or not ISO_WITH_OFFSET.fullmatch(birth_at_iso):
        raise ValueError("birth_at_iso must be YYYY-MM-DDTHH:MM[:SS[.microseconds]] with Z or +/-HH:MM")
    # RFC 3339 -00:00 may encode an unknown local offset, not a known UTC instant.
    if birth_at_iso.endswith("-00:00"):
        raise ValueError("unknown offset -00:00 is not accepted; use Z or +00:00")
    try:
        value = datetime.fromisoformat(birth_at_iso.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("birth_at_iso requires explicit timezone/UTC offset")
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"invalid birth instant: {exc}") from exc


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _instant(birth: datetime, offset_seconds: Fraction) -> str:
    # Round each absolute boundary offset once; Fraction round uses ties-to-even.
    # Exact offsets below are authoritative when sub-microsecond precision exists.
    rounded_microseconds = round(offset_seconds * MICROSECONDS_PER_SECOND)
    try:
        return _utc_text(birth + timedelta(microseconds=rounded_microseconds))
    except (OverflowError, ValueError) as exc:
        raise ValueError("calculated timeline exceeds datetime years 1..9999") from exc


def _window(birth: datetime, start: Fraction, end: Fraction) -> dict:
    duration_seconds = end - start
    return {
        "startAtUtc": _instant(birth, start), "endAtUtcExclusive": _instant(birth, end),
        "startOffsetSecondsExact": _exact_text(start), "endOffsetSecondsExact": _exact_text(end),
        "durationSecondsExact": _exact_text(duration_seconds),
        "durationDaysExact": _exact_text(duration_seconds / DAY_SECONDS),
        "durationYearsExact": _exact_text(duration_seconds / YEAR_SECONDS),
    }


def _period_record(birth: datetime, lord_index: int, start: Fraction, end: Fraction) -> dict:
    return {
        "lord": LORDS[lord_index], "lordIndex": lord_index,
        "fullWindow": _window(birth, start, end),
        "containsBirth": start <= 0 < end,
        "visibleFromBirth": None if end <= 0 else _window(birth, max(start, Fraction(0)), end),
    }


def calculate_vimshottari(birth_at_iso, moon_sidereal_longitude_deg) -> dict:
    """Nine full mahadashas and their nine antardashas, clipped separately at birth.

    The first full mahadasha begins before birth by the elapsed Moon-nakshatra
    fraction. Subperiods divide that original full period, never the remainder.
    A cycle here is 120 fixed research years starting at the first full period,
    not 120 new years starting at birth. All exact intervals are [start,end).
    """
    birth = _birth_utc(birth_at_iso)
    divisions = calculate_divisions(moon_sidereal_longitude_deg)
    nak = divisions["nakshatra"]
    first_lord = nak["index"] % 9
    fraction_elapsed = Fraction(nak["fractionElapsedExact"])
    first_full_years = Fraction(LORD_YEARS[first_lord])
    elapsed_years = fraction_elapsed * first_full_years
    first_start = -elapsed_years * YEAR_SECONDS
    cursor = first_start
    mahadashas = []
    active_subperiod = None
    for sequence_index in range(9):
        lord_index = (first_lord + sequence_index) % 9
        full_years = Fraction(LORD_YEARS[lord_index])
        end = cursor + full_years * YEAR_SECONDS
        maha = _period_record(birth, lord_index, cursor, end)
        maha.update(sequenceIndex=sequence_index, fullLordYears=LORD_YEARS[lord_index])
        sub_cursor = cursor
        subs = []
        for sub_index in range(9):
            sub_lord = (lord_index + sub_index) % 9
            sub_years = full_years * LORD_YEARS[sub_lord] / 120
            sub_end = sub_cursor + sub_years * YEAR_SECONDS
            sub = _period_record(birth, sub_lord, sub_cursor, sub_end)
            sub["sequenceIndex"] = sub_index
            if sub["containsBirth"]:
                active_subperiod = {"mahadashaLord": LORDS[lord_index], "antardashaLord": LORDS[sub_lord],
                                    "mahadashaSequenceIndex": sequence_index, "antardashaSequenceIndex": sub_index}
            subs.append(sub)
            sub_cursor = sub_end
        assert sub_cursor == end
        maha["antardashas"] = subs
        mahadashas.append(maha)
        cursor = end
    return {
        "schemaVersion": "1.0", "moduleVersion": VERSION, "configurationId": VIMSHOTTARI_CONFIG_ID,
        "birthAtUtc": _utc_text(birth), "moonDivisions": divisions,
        "settings": {
            "seed": "caller_supplied_Moon_sidereal_nakshatra_standard_candidate",
            "yearLengthDaysExact": _exact_text(YEAR_DAYS), "yearLengthDaysDecimal": "365.2425",
            "yearLengthSecondsExact": _exact_text(YEAR_SECONDS),
            "yearLengthStatus": "fixed_project_candidate; not_a_claim_of_unique_traditional_standard",
            "lordSequence": [{"lord": lord, "years": years} for lord, years in zip(LORDS, LORD_YEARS)],
            "boundaryPolicy": "exact_intervals_left_closed_right_open",
            "timestampRendering": "UTC_microseconds; exact_relative_offsets_authoritative; nearest_ties_to_even",
            "initialRemainderPolicy": "build_full_timeline_then_clip_at_birth",
            "ayanamsa": "caller_applied; module_never_selects",
        },
        "initialMahadasha": {
            "lord": LORDS[first_lord], "fullYearsExact": _exact_text(first_full_years),
            "fractionElapsedAtBirthExact": _exact_text(fraction_elapsed),
            "elapsedYearsExact": _exact_text(elapsed_years),
            "remainingYearsExact": _exact_text(first_full_years - elapsed_years),
        },
        "activeAtBirth": active_subperiod,
        "fullCycle": _window(birth, first_start, cursor),
        "visibleCycleFromBirth": _window(birth, Fraction(0), cursor),
        "mahadashas": mahadashas,
        "financialPrediction": None, "marketValidated": False,
        "scopeLimits": ["No birth-event selection, ephemeris or ayanamsa calculation.",
                        "No D9 houses, natal interpretation, Chara Dasa, SBC, Latta or return forecast.",
                        "Fixed 365.2425-day-year candidate; do not tune year length to match observed prices."],
    }
