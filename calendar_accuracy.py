"""Additive calendar accuracy audit; external examples, formula and contracts stay separate."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / 'calendar_accuracy_reference.json'
OUTPUT = HERE / 'calendar_accuracy_evidence.json'
VERSION = '0.12.0'
LOCAL = timezone(timedelta(hours=8))
STEMS = '甲乙丙丁戊己庚辛壬癸'
BRANCHES = '子丑寅卯辰巳午未申酉戌亥'
SIXTY = tuple(STEMS[i % 10] + BRANCHES[i % 12] for i in range(60))
TERM_ALIASES = {'LI_CHUN': '立春', 'JING_ZHE': '惊蛰', 'XIAO_HAN': '小寒'}
NORMALIZE = str.maketrans({'驚': '惊', '蟄': '蛰'})
INPUT_NAMES = ('calendar_accuracy.py', 'calendar_accuracy_reference.json', 'calendar_adapter.py',
               'bazi_engine.py', 'calendar_reference.json', 'calendar_validation.py',
               'calendar_validation_evidence.json', 'profiles.json')
CATEGORIES = ('userScreenshotFullCharts', 'formulaFourPillarChecks',
              'boundaryContractChecks', 'officialDayExamples')


class CalendarAccuracyEvidenceError(ValueError):
    """The saved report no longer matches its inputs, and must not look current."""


class ReferencePrecisionError(ValueError):
    """Minute-only references cannot give an independent second-level answer."""


def parse_local(at: str) -> datetime:
    """Independent parser; deliberately no adapter helper calls."""
    if not isinstance(at, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})', at):
        raise ValueError('需要整秒且显式时区的ISO时间')
    if at.endswith('-00:00'):
        raise ValueError('未知时区不能用于独立核验')
    result = datetime.fromisoformat(at.replace('Z', '+00:00')).astimezone(LOCAL)
    if not 1992 <= result.year <= 2099:
        raise ValueError('超出所选UTC+8公历范围')
    return result


def independent_day(civil_date: date | str) -> tuple[int, str]:
    """CWA day-name equation, implemented with proleptic Gregorian ordinal only."""
    value = date.fromisoformat(civil_date) if isinstance(civil_date, str) else civil_date
    jdn = value.toordinal() + 1721425
    return jdn, SIXTY[(jdn - 11) % 60]


def independent_pillars(at: str, ref: dict, *, exact_term_overrides=None) -> dict:
    """Oracle built from official minutes and HKO tables, not lunar-library results.

    Overrides are ONLY for separately labelled internal boundary contracts; a
    supplied library second never becomes external astronomical evidence.
    """
    local = parse_local(at)
    coverage = ref['formulaCoverage']
    if not parse_local(coverage['start']) <= local < parse_local(coverage['endExclusive']):
        raise ValueError('缺少该时段独立节气表，不能外推公式对照范围')
    overrides = exact_term_overrides or {}
    boundaries = []
    for row in ref['jie']:
        boundary = parse_local(overrides.get(row['id'], row['publishedAt']))
        if row['id'] not in overrides and abs((local - boundary).total_seconds()) <= coverage['guardSeconds']:
            raise ReferencePrecisionError('处于公布分钟附近，不能独立判定秒级前后')
        boundaries.append((boundary, row))
    lichun = next(t for t, r in boundaries if r['name'] == '立春' and t.year == local.year)
    effective_year = local.year if local >= lichun else local.year - 1
    year = SIXTY[(effective_year - 4) % 60]
    preceding = [(t, r) for t, r in boundaries if t <= local]
    month_branch = max(preceding, key=lambda x: x[0])[1]['monthBranchAfter'] if preceding else coverage['initialMonthBranch']
    month_index = (BRANCHES.index(month_branch) - 2) % 12
    # HKO table four: 甲己丙寅, 乙庚戊寅, 丙辛庚寅, 丁壬壬寅, 戊癸甲寅.
    month_stem = STEMS[(2 * (STEMS.index(year[0]) % 5) + 2 + month_index) % 10]
    _, day = independent_day(local.date())
    hour_day = local.date() + timedelta(days=1) if local.hour == 23 else local.date()
    _, hour_reference_day = independent_day(hour_day)
    branch_index = ((local.hour + 1) // 2) % 12
    # HKO table five plus the project's declared late-Zi convention.
    hour_stem = STEMS[(2 * (STEMS.index(hour_reference_day[0]) % 5) + branch_index) % 10]
    return {'year': year, 'month': month_stem + month_branch,
            'day': day, 'hour': hour_stem + BRANCHES[branch_index]}


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vendor_hash() -> str:
    rows = [f'{p.relative_to(HERE).as_posix()}:{_hash_file(p)}'
            for p in sorted((HERE / 'vendor' / 'lunar_python').rglob('*.py'))]
    if not rows:
        raise CalendarAccuracyEvidenceError('历法库源码缺失')
    return hashlib.sha256('\n'.join(rows).encode()).hexdigest()


def _artifact_state() -> dict:
    return {'inputSha256': {name: _hash_file(HERE / name) for name in INPUT_NAMES},
            'lunarPythonSourceTreeSha256': _vendor_hash()}


def _totals(rows: list[dict]) -> dict:
    return {'total': len(rows), 'passed': sum(r['passed'] for r in rows),
            'failed': sum(not r['passed'] for r in rows)}


def _summary(checks: list[dict]) -> dict:
    totals = _totals(checks)
    return {'executedChecks': totals['total'], 'passed': totals['passed'], 'failed': totals['failed'],
            **{c: _totals([r for r in checks if r['category'] == c]) for c in CATEGORIES},
            'officialFullFourPillarSamples': 0, 'secondPrecisionSolarTermsVerified': 0,
            'headline': '问真截图单例、独立四柱公式与边界检查分别核对',
            'interpretation': '通过表示本轮所列样例符合既定口径；不表示所有排盘流派一致、资产原局已确认或行情可预测。'}


def _library_term_time(api, row: dict) -> str:
    printed = parse_local(row['publishedAt'])
    candidates = []
    for key, value in api.at_local_iso(printed.isoformat())['jieQi'].items():
        name = TERM_ALIASES.get(key, key).translate(NORMALIZE)
        instant = datetime.fromisoformat(value).replace(tzinfo=LOCAL)
        if name == row['name'] and instant.year == printed.year:
            candidates.append(instant)
    if not candidates:
        raise ValueError('待测库没有对应节气时刻')
    return min(candidates, key=lambda x: abs((x - printed).total_seconds())).isoformat()


def run_accuracy_validation(reference_path: Path = REFERENCE, api=None) -> dict:
    reference_raw = Path(reference_path).read_bytes()
    ref = json.loads(reference_raw.decode('utf-8-sig'))
    checks = []
    examples = []
    dependency_error = None
    if api is None:
        try:
            api = importlib.import_module('calendar_adapter')
        except Exception as exc:
            dependency_error = {'type': type(exc).__name__, 'message': str(exc)}

    def capture(category, ident, tier, operation, **metadata):
        try:
            passed, detail = operation()
            row = {'id': ident, 'category': category, 'evidenceTier': tier,
                   'passed': bool(passed), **metadata, **detail}
        except Exception as exc:
            row = {'id': ident, 'category': category, 'evidenceTier': tier, 'passed': False,
                   **metadata, 'errorType': type(exc).__name__, 'error': str(exc)}
        checks.append(row)
        return row

    def compare(at, expected):
        actual = api.calculate_calendar(at)['pillars']
        return actual == expected, {'at': at, 'expected': expected, 'actual': actual}

    def formula_case(ident, at, group='formulaFourPillarChecks', tier='independent_formula_with_official_minute_inputs', **metadata):
        return capture(group, ident, tier,
                       lambda: compare(at, independent_pillars(at, ref)),
                       sourceIds=['V12-CWA-CAL24', 'V12-HKO-STEMS', 'V12-CWA-JDN16'], **metadata)

    for sample in ref['userScreenshotCases']:
        row = capture('userScreenshotFullCharts', sample['id'], sample['evidenceTier'],
                      lambda s=sample: compare(s['at'], s['expected']),
                      label=sample['label'], sourceIds=sample['sourceIds'])
        examples.append(row)
    for sample in ref['officialDayExamples']:
        def official_check(sample=sample):
            jdn, day = independent_day(sample['date'])
            actual = api.calculate_calendar(sample['date'] + 'T12:00:00+08:00')['pillars']['day']
            return (jdn == sample['jdn'] and day == sample['day'] == actual), {
                'date': sample['date'], 'expected': {'jdn': sample['jdn'], 'day': sample['day']},
                'actual': {'independentJdn': jdn, 'independentDay': day, 'adapterDay': actual}}
        capture('officialDayExamples', sample['id'], 'direct_official_day_example_not_full_chart',
                official_check, sourceIds=sample['sourceIds'])

    coverage = ref['formulaCoverage']
    for i, at in enumerate(coverage['ordinaryMonths'], 1):
        formula_case(f'V12-ORDINARY-{i:02}', at, subkind='ordinary_month_noon')
    start = date.fromisoformat(coverage['hourCycleStart'])
    for offset in range(coverage['hourCycleDays']):
        for hour in coverage['hourCycleHours']:
            at = f'{start + timedelta(days=offset)}T{hour:02}:00:00+08:00'
            formula_case(f'V12-HOUR-{offset:02}-{hour:02}', at, subkind='ten_day_stems_selected_hours')
    for table, kind in ((ref['jie'], 'jie_year_month_transition'), (ref['zhongqi'], 'zhongqi_month_unchanged')):
        for row in table:
            for direction in (-1, 1):
                at = (parse_local(row['publishedAt']) + timedelta(seconds=direction * coverage['boundarySampleOffsetSeconds'])).isoformat()
                formula_case(f'{row["id"]}-{direction:+}', at, subkind=kind, term=row['name'],
                             publishedAt=row['publishedAt'], sourcePrecision='minute',
                             secondPrecisionExternallyVerified=False)
    for i, at in enumerate(coverage['clockBoundaryTimes'], 1):
        formula_case(f'V12-CLOCK-{i:02}', at, 'boundaryContractChecks',
                     'independent_formula_plus_chosen_clock_convention', subkind='civil_midnight_late_zi')
    for row in ref['jie']:
        if row['id'] not in coverage['exactLibraryBoundaryIds']:
            continue
        # The center is obtained from the system under test, and is labelled internal.
        for offset in (-1, 0, 1):
            def exact_check(row=row, offset=offset):
                center = _library_term_time(api, row)
                at = (parse_local(center) + timedelta(seconds=offset)).isoformat()
                expected = independent_pillars(at, ref, exact_term_overrides={row['id']: center})
                passed, detail = compare(at, expected)
                return passed, {**detail, 'libraryBoundaryAt': center,
                                'boundaryTimeOrigin': 'system_under_test',
                                'secondPrecisionExternallyVerified': False}
            capture('boundaryContractChecks', f'{row["id"]}-SECOND-{offset:+}',
                    'internal_second_boundary_contract_not_external_time_verification', exact_check,
                    term=row['name'], subkind='exact_library_boundary')
    for at in ('2024-02-09T23:00:00+08:00', '2024-02-10T00:00:00+08:00', '2024-03-05T10:26:00+08:00'):
        def timezone_check(at=at):
            utc = parse_local(at).astimezone(timezone.utc).isoformat()
            first, second = api.calculate_calendar(at), api.calculate_calendar(utc)
            return (first['pillars'] == second['pillars'] and first['atResearchTimezone'] == second['atResearchTimezone']), {
                'at': at, 'utcAt': utc, 'actual': first['pillars']}
        capture('boundaryContractChecks', 'V12-UTC-' + at, 'same_instant_input_contract', timezone_check,
                subkind='timezone_equivalence')
    for at, should_accept in (
            ('1992-01-01T00:00:00+08:00', True), ('2099-12-31T23:59:59+08:00', True),
            ('1991-12-31T23:59:59+08:00', False), ('2100-01-01T00:00:00+08:00', False)):
        def range_check(at=at, should_accept=should_accept):
            try:
                api.calculate_calendar(at)
                accepted = True
            except ValueError:
                accepted = False
            return accepted == should_accept, {'at': at, 'expectedAccepted': should_accept, 'actualAccepted': accepted,
                                               'fullPillarsAtRangeEndpointVerified': False}
        capture('boundaryContractChecks', 'V12-RANGE-' + at, 'supported_input_range_only', range_check,
                subkind='input_range')

    artifacts = _artifact_state()
    unchanged = all(artifacts['inputSha256'].get(n) == digest for n, digest in ref['frozenFilesBefore'].items())
    old = json.loads((HERE / 'calendar_validation_evidence.json').read_text(encoding='utf-8-sig'))
    summary = _summary(checks)
    status = 'dependency_error' if dependency_error else ('passed' if not summary['failed'] and unchanged else 'failed')
    return {'schemaVersion': 1, 'validationVersion': VERSION,
            'generatedAt': datetime.now(timezone.utc).isoformat(), 'status': status,
            'referenceSha256': hashlib.sha256(reference_raw).hexdigest(),
            'dependencyError': dependency_error, 'summary': summary, 'policy': ref['policy'],
            'oldValidation': {k: old['counts'][k] for k in ('executedChecks', 'passed', 'failed')} | {
                'unchanged': unchanged, 'evidenceSha256': artifacts['inputSha256']['calendar_validation_evidence.json'],
                'note': '旧107项原样保留；与本轮有重叠，不相加为独立样本数。'},
            'testedScope': {'formulaPeriod': [coverage['start'], coverage['endExclusive']],
                'periodEndExclusive': True, 'selectedHours': coverage['hourCycleHours'],
                'fullSupportedRangeExhaustivelyVerified': False},
            'examples': examples, 'sources': ref['sources'], 'limitations': ref['limitations'],
            'checks': checks, 'artifacts': artifacts,
            'marketPredictionValidated': False, 'assetNatalProfilesValidated': False}


def load_accuracy_evidence(evidence_path: Path = OUTPUT) -> dict:
    """Read-only API helper; refuse stale inputs instead of showing an old green card."""
    try:
        report = json.loads(Path(evidence_path).read_text(encoding='utf-8-sig'))
        if report.get('schemaVersion') != 1 or report.get('validationVersion') != VERSION:
            raise CalendarAccuracyEvidenceError('排盘核验报告版本不匹配')
        if report['artifacts'] != _artifact_state():
            raise CalendarAccuracyEvidenceError('核验输入已变更，请重新执行排盘核验')
        if report['referenceSha256'] != _hash_file(REFERENCE):
            raise CalendarAccuracyEvidenceError('该报告使用的参考资料与当前文件不一致')
        if report['summary'] != _summary(report['checks']):
            raise CalendarAccuracyEvidenceError('核验摘要与明细不一致')
        return report
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CalendarAccuracyEvidenceError('排盘核验证据缺失或结构不完整') from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--no-write', action='store_true')
    args = parser.parse_args()
    report = run_accuracy_validation()
    if not args.no_write:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'summary': report['summary'],
                      'oldValidation': report['oldValidation']}, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
