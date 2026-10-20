"""Validate calendar subfields against dated external references, never full-chart truth."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / 'calendar_reference.json'
OUTPUT = HERE / 'calendar_validation.json'
LOCAL = timezone(timedelta(hours=8))
STEMS = '甲乙丙丁戊己庚辛壬癸'
BRANCHES = '子丑寅卯辰巳午未申酉戌亥'
SIXTY = tuple(STEMS[i % 10] + BRANCHES[i % 12] for i in range(60))
JIE_MONTH = dict(zip(('立春', '惊蛰', '清明', '立夏', '芒种', '小暑',
                     '立秋', '白露', '寒露', '立冬', '大雪', '小寒'), '寅卯辰巳午未申酉戌亥子丑'))
NORMALIZE = str.maketrans({'驚': '惊', '蟄': '蛰', '滿': '满', '種': '种', '處': '处', '穀': '谷'})
# JieQiTable uses romanized keys for repeated terms at its surrounding-year edges.
# These are lookup aliases only; published observations and tolerance stay unchanged.
TERM_KEY_ALIASES = {
    'DA_XUE': '大雪', 'DONG_ZHI': '冬至', 'XIAO_HAN': '小寒',
    'DA_HAN': '大寒', 'LI_CHUN': '立春', 'YU_SHUI': '雨水', 'JING_ZHE': '惊蛰',
}
TOLERANCE_SECONDS = 120


def independent_day_label(civil_date: str) -> tuple[int, str]:
    """CWA printed JDN rule; Python Gregorian ordinal, no lunar library dependency."""
    day = date.fromisoformat(civil_date)
    jdn = day.toordinal() + 1721425
    return jdn, SIXTY[(jdn - 11) % 60]


def eligible_for_day_only(checks: list[dict]) -> bool:
    """Only this narrow gate; term/month checks and market validity remain separate."""
    required_minimum = {'day_anchor': 12, 'independent_day_formula': 12,
                        'official_formula_example': 1, 'civil_midnight': 6,
                        'utc_equivalence': 6, 'late_zi_contract': 1,
                        'relation_acceptance': 6}
    return all(len(rows := [x for x in checks if x['group'] == group]) >= minimum
               and all(x['passed'] for x in rows)
               for group, minimum in required_minimum.items())


def _local(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=LOCAL) if dt.tzinfo is None else dt.astimezone(LOCAL)


def _term_time(api, name: str, printed_time: datetime) -> tuple[str, datetime]:
    data = api.at_local_iso(printed_time.isoformat())
    candidates = []
    for key, value in data['jieQi'].items():
        canonical_key = TERM_KEY_ALIASES.get(key, key).translate(NORMALIZE)
        if canonical_key == name.translate(NORMALIZE):
            actual = _local(value)
            if actual.year == printed_time.year:
                candidates.append((key, actual))
    if not candidates:
        raise ValueError(f'library JieQi table has no {printed_time.year} {name}')
    return min(candidates, key=lambda x: abs((x[1] - printed_time).total_seconds()))


def run_validation(reference_path: Path = REFERENCE, api=None) -> dict:
    raw = reference_path.read_bytes()
    ref = json.loads(raw.decode('utf-8-sig'))
    checks: list[dict] = []

    def add(group, ident, passed, **detail):
        checks.append({'group': group, 'id': ident, 'passed': bool(passed), **detail})

    def capture(group, ident, operation):
        try:
            passed, detail = operation()
            add(group, ident, passed, **detail)
        except Exception as exc:
            add(group, ident, False, errorType=type(exc).__name__, error=str(exc))

    # Run genuinely independent arithmetic even when the optional adapter is inaccessible.
    for row in ref['dayAnchors']:
        jdn, gz = independent_day_label(row['gregorianDate'])
        add('independent_day_formula', row['id'], gz == row['dayGanZhi'],
            expected=row['dayGanZhi'], actual=gz, civilDateJDN=jdn,
            sourceIds=['CWA-JDN16', row['observationSourceId']],
            evidenceTier='official_indexed_day_table_plus_independent_published_algorithm')
    anchor = ref['officialDayWorkedExample']
    jdn, gz = independent_day_label(anchor['gregorianDate'])
    add('official_formula_example', anchor['id'],
        jdn == anchor['civilDateJDN'] and gz == anchor['dayGanZhi'],
        expected={'jdn': anchor['civilDateJDN'], 'dayGanZhi': anchor['dayGanZhi']},
        actual={'jdn': jdn, 'dayGanZhi': gz}, sourceIds=anchor['sourceIds'],
        evidenceTier='direct_official_worked_example')

    dependency_error = None
    if api is None:
        try:
            api = importlib.import_module('calendar_adapter')
        except Exception as exc:
            dependency_error = {'type': type(exc).__name__, 'message': str(exc)}

    if api is not None:
        for row in ref['dayAnchors']:
            def run_day(row=row):
                actual = api.day_label(row['gregorianDate'])
                return actual == row['dayGanZhi'], {
                    'date': row['gregorianDate'], 'expected': row['dayGanZhi'], 'actual': actual,
                    'sourceIds': [row['observationSourceId']],
                    'evidenceTier': 'official_indexed_text_not_pdf_visual_collation'}
            capture('day_anchor', row['id'], run_day)

        # Validate every independently published observation, including the disagreeing pair.
        term_rows = [(row, obs) for row in ref['solarTerms'] for obs in row['observations']]
        term_rows += [(row, {'localDateTime': row['localDateTime'],
                            'sourceId': row['sourceIds'][0]}) for row in ref['jieBoundaryInputs']]
        for row, obs in term_rows:
            def run_term(row=row, obs=obs):
                printed = _local(obs['localDateTime'])
                raw_key, actual = _term_time(api, row['term'], printed)
                delta = (actual - printed).total_seconds()
                return abs(delta) <= TOLERANCE_SECONDS, {
                    'term': row['term'], 'publishedLocalMinute': obs['localDateTime'],
                    'libraryRawKey': raw_key, 'calculatedAt': actual.isoformat(), 'deltaSeconds': delta,
                    'toleranceSeconds': TOLERANCE_SECONDS, 'sourceIds': [obs['sourceId']],
                    'evidenceTier': 'opened_official_pdf_minute_table',
                    'sourcePrecision': 'minute', 'secondPrecisionVerified': False}
            capture('solar_term_time', row['id'] + ':' + obs['sourceId'], run_term)

        # Twelve monthly jie plus next LiChun: defined branch mapping, not external full pillars.
        for row in ref['jieBoundaryInputs']:
            def run_month(row=row):
                center = _local(row['localDateTime'])
                before = api.calculate_calendar((center - timedelta(minutes=2)).isoformat())
                after = api.calculate_calendar((center + timedelta(minutes=2)).isoformat())
                target = JIE_MONTH[row['term'].translate(NORMALIZE)]
                previous = BRANCHES[(BRANCHES.index(target) - 1) % 12]
                actual = [before['pillars']['month'][1], after['pillars']['month'][1]]
                passed = actual == [previous, target]
                year_check = None
                if row['term'] == '立春':
                    expected_year = [SIXTY[(center.year - 5) % 60], SIXTY[(center.year - 4) % 60]]
                    actual_year = [before['pillars']['year'], after['pillars']['year']]
                    year_check = {'expected': expected_year, 'actual': actual_year}
                    passed = passed and expected_year == actual_year
                return passed, {'term': row['term'], 'centerPublishedMinute': row['localDateTime'],
                                'beforeAt': before['atResearchTimezone'], 'afterAt': after['atResearchTimezone'],
                                'expectedMonthBranches': [previous, target], 'actualMonthBranches': actual,
                                'lichunYearCheck': year_check, 'sourceIds': row['sourceIds'],
                                'evidenceTier': 'published_minute_plus_explicit_bazi_boundary_convention',
                                'fullFourPillarsExternallyVerified': False}
            capture('jie_month_transition', row['id'], run_month)

        for row in ref['solarTerms']:
            def run_major(row=row):
                times = [_local(o['localDateTime']) for o in row['observations']]
                before = api.calculate_calendar((min(times) - timedelta(minutes=2)).isoformat())
                after = api.calculate_calendar((max(times) + timedelta(minutes=2)).isoformat())
                values = [before['pillars']['month'], after['pillars']['month']]
                return values[0] == values[1], {'term': row['term'], 'monthPillars': values,
                                              'evidenceTier': 'explicit_jie_not_zhongqi_convention'}
            capture('zhongqi_not_month_boundary', row['id'], run_major)

        for row in ref['contractBoundaryTests']:
            def run_boundary(row=row):
                result = api.calculate_calendar(row['localDateTime'])
                actual = result['pillars']
                return actual['day'] == row['expectedDayGanZhi'] and actual['hour'][1] == row['expectedHourBranch'], {
                    'at': row['localDateTime'], 'expectedDay': row['expectedDayGanZhi'],
                    'actualDay': actual['day'], 'expectedHourBranch': row['expectedHourBranch'],
                    'actualHour': actual['hour'], 'evidenceTier': 'project_civil_midnight_contract',
                    'externalFullChartSample': False}
            capture('civil_midnight', row['id'], run_boundary)

            def run_utc(row=row):
                utc = _local(row['localDateTime']).astimezone(timezone.utc).isoformat()
                a = api.calculate_calendar(row['localDateTime'])
                b = api.calculate_calendar(utc)
                return a['pillars'] == b['pillars'] and a['atResearchTimezone'] == b['atResearchTimezone'], {
                    'localAt': row['localDateTime'], 'utcAt': utc,
                    'evidenceTier': 'same_instant_timezone_contract'}
            capture('utc_equivalence', row['id'], run_utc)

            def run_relations(row=row):
                result = api.calculate_relations('BTC-BZ-V01', row['localDateTime'])
                expected_ref = 'next_day_late_zi' if _local(row['localDateTime']).hour == 23 else 'same_day'
                relation = result['relationships']
                flow_checks = relation['checks']['flow']
                hour_checks = [x for x in flow_checks if x['check'] == 'day_hour_stem']
                passed = (relation['context'].get('flowHourReference') == expected_ref
                          and len(hour_checks) == 1
                          and hour_checks[0]['result'] == 'pass'
                          and hour_checks[0]['hourStemReference'] == expected_ref
                          and result['marketDirection'] is None)
                return passed, {'at': row['localDateTime'], 'expectedHourReference': expected_ref,
                                'hourChecks': hour_checks, 'evidenceTier': 'adapter_engine_contract'}
            capture('relation_acceptance', row['id'], run_relations)

        def run_late_zi():
            late = api.calculate_calendar('2024-02-09T23:00:00+08:00')
            midnight = api.calculate_calendar('2024-02-10T00:00:00+08:00')
            # Day anchor says next day is 甲; 五鼠遁 then gives 甲子 at this 子 hour.
            passed = (late['pillars']['day'] == '癸卯' and midnight['pillars']['day'] == '甲辰'
                      and late['hourStemReference'] == 'next_day_late_zi'
                      and midnight['hourStemReference'] == 'same_day'
                      and late['pillars']['hour'] == midnight['pillars']['hour'] == '甲子')
            return passed, {'lateZi': {'pillars': late['pillars'], 'reference': late['hourStemReference']},
                            'midnight': {'pillars': midnight['pillars'], 'reference': midnight['hourStemReference']},
                            'evidenceTier': 'chosen_late_zi_hour_stem_contract_not_external_full_chart'}
        capture('late_zi_contract', '23_then_00', run_late_zi)

        def run_year_fields():
            # Official lunar year and chosen LiChun year intentionally differ between these dates.
            a = api.calculate_calendar('2024-02-05T12:00:00+08:00')
            b = api.calculate_calendar('2024-02-10T12:00:00+08:00')
            expected = [('甲辰', '癸卯'), ('甲辰', '甲辰')]
            actual = [(x['pillars']['year'], x['lunarCalendarYear']) for x in (a, b)]
            return actual == expected, {'expectedBaziAndLunarYears': expected,
                                       'actualBaziAndLunarYears': actual,
                                       'sourceIds': ['HKO-YEAR', 'HKO-CONV24', 'CWA-CAL24'],
                                       'evidenceTier': 'official_dates_plus_separate_year_boundary_policies'}
        capture('separate_year_fields', '2024_lichun_vs_new_year', run_year_fields)

    grouped = {}
    for group in sorted({x['group'] for x in checks}):
        rows = [x for x in checks if x['group'] == group]
        grouped[group] = {'total': len(rows), 'passed': sum(x['passed'] for x in rows),
                          'failed': sum(not x['passed'] for x in rows)}
    return {
        'schemaVersion': 1, 'validationVersion': '0.4.0',
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'referenceFile': reference_path.name, 'referenceSha256': hashlib.sha256(raw).hexdigest(),
        'adapterVersion': getattr(api, 'VERSION', None),
        'status': 'dependency_error' if dependency_error else ('passed' if all(x['passed'] for x in checks) else 'failed'),
        'dependencyError': dependency_error,
        'counts': {'executedChecks': len(checks), 'passed': sum(x['passed'] for x in checks),
                   'failed': sum(not x['passed'] for x in checks), 'byGroup': grouped,
                   'externalReferenceScope': ref['counts'], 'verifiedFullFourPillarSamples': 0,
                   'verifiedSecondPrecisionSolarTerms': 0},
        'sourceTiers': dict(Counter(x.get('evidenceTier', 'execution_error') for x in checks)),
        'sources': ref['sources'], 'checks': checks,
        'eligibleForDayOnlyHypothesis': dependency_error is None and eligible_for_day_only(checks),
        'eligibilityMeaning': '仅日历日期层可供预先冻结的日级研究假设；不表示行情预测有效或完整四柱已外部验证',
        'limits': ref['limitations'] + [
            '120秒是当前公布分钟资料的核对容差，不是官方秒精度或天体模型误差保证。',
            '日名HKO证据仍为官方PDF索引文本，未升级为原页逐字视觉校勘。',
            '月界与晚子时时干的项目规则检查不是新增外部完整八字样本。'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, default=REFERENCE)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--no-write', action='store_true')
    args = parser.parse_args()
    report = run_validation(args.reference)
    if not args.no_write:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'counts': report['counts'],
                      'eligibleForDayOnlyHypothesis': report['eligibleForDayOnlyHypothesis'],
                      'dependencyError': report['dependencyError']}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['status'] == 'passed' else 2)


if __name__ == '__main__':
    main()
