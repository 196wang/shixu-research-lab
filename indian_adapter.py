"""Explicit Lahiri/Moshier geocentric snapshots; asset natal times remain unknown."""
from datetime import timezone
import hashlib
from importlib import metadata
import math
from pathlib import Path
import sys
import threading

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'vendor'))
import swisseph as swe
from astro_adapter import parse_instant
from indian_math import calculate_divisions, calculate_vimshottari

VERSION = '0.5.0'
CONFIGURATION_ID = 'IA-LAHIRI1-MOSHIER-GEOCENTRIC-V05'
LOCK = threading.RLock()
FLAGS = swe.FLG_MOSEPH | swe.FLG_SPEED | swe.FLG_SIDEREAL
NAMES = ['Ashwini','Bharani','Krittika','Rohini','Mrigashira','Ardra','Punarvasu','Pushya','Ashlesha',
         'Magha','Purva Phalguni','Uttara Phalguni','Hasta','Chitra','Swati','Vishakha','Anuradha',
         'Jyeshtha','Mula','Purva Ashadha','Uttara Ashadha','Shravana','Dhanishtha','Shatabhisha',
         'Purva Bhadrapada','Uttara Bhadrapada','Revati']
BODIES = [('Sun', swe.SUN), ('Moon', swe.MOON), ('Mercury', swe.MERCURY), ('Venus', swe.VENUS),
          ('Mars', swe.MARS), ('Jupiter', swe.JUPITER), ('Saturn', swe.SATURN), ('Rahu', swe.MEAN_NODE)]


def engine_info():
    version = metadata.version('pysweph')
    if version != '2.10.3.6' or swe.version != '2.10.03':
        raise RuntimeError('Swiss版本与已核对的配置不一致')
    return {'name': 'Swiss Ephemeris', 'version': swe.version, 'wrapper': 'pysweph',
            'wrapperVersion': version, 'wrapperProvenance': 'community_fork_not_Astrodienst_Python_binding',
            'backendRequested': 'Moshier', 'externalEphemerisFiles': [], 'license': 'AGPL-3.0',
            'binarySha256': hashlib.sha256(Path(swe.__file__).read_bytes()).hexdigest()}


def calculate_indian(at: str, scenario: bool = False):
    if type(scenario) is not bool:
        raise ValueError('scenario必须是布尔值')
    utc = parse_instant(at)
    if not 1992 <= utc.year <= 2099:
        raise ValueError('印度星位适配器本版范围为1992–2099年')
    info = engine_info()
    positions = []
    boundary_warnings = []
    with LOCK:
        # close clears cached model state; set_sid_mode alone does not undo legacy
        # precession changes caused by SIDBIT_PREC_ORIG in the same thread.
        swe.close()
        swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
        jd_tt, jd_ut = swe.utc_to_jd(utc.year, utc.month, utc.day, utc.hour, utc.minute,
                                   utc.second + utc.microsecond / 1e6, swe.GREG_CAL)
        aya_flags, aya = swe.get_ayanamsa_ex_ut(jd_ut, swe.FLG_MOSEPH)
        mean_flags, mean_aya = swe.get_ayanamsa_ex_ut(jd_ut, swe.FLG_MOSEPH | swe.FLG_NONUT)
        for name, body in BODIES:
            coords, flags, message = swe.calc_ut(jd_ut, body, FLAGS)
            if flags & 7 != swe.FLG_MOSEPH or not flags & swe.FLG_SIDEREAL or not flags & swe.FLG_SPEED:
                raise RuntimeError('星历返回标志不符合固定配置，拒绝静默切换')
            if not all(math.isfinite(x) for x in coords) or not 0 <= coords[0] < 360:
                raise RuntimeError('星位输出异常')
            positions.append({'body': name, 'longitudeDeg': coords[0], 'latitudeDeg': coords[1],
                              'distanceAu': coords[2], 'longitudeSpeedDegPerDay': coords[3],
                              'retrograde': coords[3] < 0, 'requestedFlags': FLAGS,
                              'returnedFlags': flags, 'engineMessage': message,
                              'nodeDefinition': 'mean_ascending_node' if name == 'Rahu' else None})
    rahu = positions[-1]
    positions.append({**rahu, 'body': 'Ketu', 'longitudeDeg': (rahu['longitudeDeg'] + 180) % 360,
                      'latitudeDeg': -rahu['latitudeDeg'], 'nodeDefinition': 'mean_descending_node_derived_opposite_Rahu'})
    for p in positions:
        divisions = calculate_divisions(p['longitudeDeg'])
        p['divisions'] = divisions
        p['nakshatraName'] = NAMES[divisions['nakshatra']['index']]
        # D9 and pada share 3deg20 boundaries; this is an alert band, not an accuracy guarantee.
        distance = abs((p['longitudeDeg'] * 108 / 360) - round(p['longitudeDeg'] * 108 / 360)) * 360 / 108
        p['nearestPadaBoundaryArcseconds'] = distance * 3600
        if distance <= 1 / 60:
            boundary_warnings.append({'body': p['body'], 'distanceArcseconds': distance * 3600,
                                      'alertBandArcseconds': 60, 'meaning': 'project_alert_band_not_error_bound'})
    moon = next(p for p in positions if p['body'] == 'Moon')
    dasa = calculate_vimshottari(at, moon['longitudeDeg']) if scenario else None
    if dasa:
        dasa['context'] = 'hypothetical_anchor_at_selected_analysis_time_not_asset_natal'
    return {'available': True, 'adapterVersion': VERSION, 'configurationId': CONFIGURATION_ID,
            'inputAt': at, 'snapshotType': 'transit_at_supplied_instant', 'engine': info,
            'time': {'atUtc': utc.astimezone(timezone.utc).isoformat(), 'jdTT': jd_tt, 'jdUT1': jd_ut,
                     'deltaTSeconds': (jd_tt - jd_ut) * 86400,
                     'conversion': 'Swiss utc_to_jd; built-in leap seconds and Delta-T; no live EOP/updated leap table'},
            'settings': {'origin': 'geocentric', 'zodiac': 'sidereal', 'ayanamsaMode': 'Lahiri',
                         'ayanamsaModeNumber': 1, 'ayanamsaExtraBits': 0,
                         'positionFlagsRequested': FLAGS, 'nodeType': 'mean', 'housesEnabled': False,
                         'nonutNote': 'SIDEREAL automatically adds NONUT to returned flags per Swiss documentation',
                         'resetPolicy': 'swe_close then set_sid_mode(LAHIRI,0,0) under adapter lock',
                         'displayAyanamsa': 'including_nutation_from_get_ayanamsa_ex_ut; not_manually_subtracted',
                         'D9': 'Parashara_standard_element_start', 'yearLengthDays': 365.2425,
                         'boundaryPolicy': 'unrounded_longitude_to_exact_decimal_fraction_left_closed_right_open',
                         'annualLengthStatus': 'project_candidate_not_only_traditional_choice',
                         'SBC': False, 'Latta': False},
            'ayanamsaDeg': aya, 'ayanamsaReturnedFlags': aya_flags,
            'meanAyanamsaDeg': mean_aya, 'meanAyanamsaReturnedFlags': mean_flags,
            'positions': positions, 'boundaryWarnings': boundary_warnings, 'vimshottari': dasa,
            'natal': {'assetBirthTime': None, 'assetBirthPlace': None, 'assetNatalDasaAvailable': False},
            'validation': {'externalEphemerisPrecisionValidated': False,
                           'JHoraRunVerified': False, 'marketValidated': False,
                           'note': '公开例题和同引擎岁差回归不构成独立星历精度验证'},
            'marketDirection': None}
