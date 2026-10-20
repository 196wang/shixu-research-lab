"""H-BZ-DAY-001 v0.1.0 price-difference study scorer (not a trading backtest).

No network calls or calendar generation. Input labels and hourly aggregation
must be independently validated by upstream adapters. See scorer_readme.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "0.4.0"
HYPOTHESIS_ID = "H-BZ-DAY-001"
STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
SIXTY = {STEMS[i % 10] + BRANCHES[i % 12] for i in range(60)}
GROUP1 = frozenset("庚辛壬癸")
BLOCK_LENGTH = 30
REPLICATES = 10_000
SEED = 20260927
MIN_VALID_REPLICATES = 9_900
EFFECT_THRESHOLD = 0.001
DEFAULT_PERIODS = (
    ("historical_exploration", "2020-01-01", "2024-12-31"),
    ("contaminated_historical_review", "2025-01-01", "2026-09-26"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("date_must_be_iso_string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid_date") from exc
    if parsed.isoformat() != value:
        raise ValueError("date_must_be_YYYY-MM-DD")
    return parsed


def date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def decimal_price(value: object) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise ValueError("not_a_price")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("not_a_price") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("price_not_positive_finite")
    return number


def validate_row(row: dict, iso_date: str) -> dict:
    errors = []
    ganzhi = row.get("dayGanzhi")
    group = None
    if not isinstance(ganzhi, str) or ganzhi not in SIXTY:
        errors.append("invalid_day_ganzhi")
    else:
        group = int(ganzhi[0] in GROUP1)
    if type(row.get("hourCount")) is not int or row["hourCount"] != 24:
        errors.append("hour_count_not_24")
    if row.get("sourceErrors"):
        errors.append("upstream_source_errors")
    prices = {}
    for field in ("open", "high", "low", "close"):
        try:
            prices[field] = decimal_price(row.get(field))
        except ValueError as exc:
            errors.append(f"{field}:{exc}")
    result = {
        "date": iso_date,
        "dayGanzhi": ganzhi if isinstance(ganzhi, str) else None,
        "group": group,
        "groupLabel": None if group is None else ("metal_water_stem" if group else "other_stem"),
        "hourCount": row.get("hourCount"),
        "prices": {field: str(value) for field, value in prices.items()},
        "sourceErrors": row.get("sourceErrors", []),
        "valid": False,
        "errors": errors,
        "return": None,
        "returnBp": None,
    }
    if len(prices) == 4:
        op, hi, lo, cl = (prices[key] for key in ("open", "high", "low", "close"))
        if not (lo <= min(op, cl) <= max(op, cl) <= hi):
            errors.append("invalid_ohlc_order")
        simple_return = float(cl / op - 1)
        if not math.isfinite(simple_return):
            errors.append("nonfinite_return")
        elif not errors:
            result.update(valid=True, returnBp=simple_return * 10_000)
            result["return"] = simple_return
    return result


def describe(values: list[float]) -> dict:
    size = len(values)
    return {
        "n": size,
        "mean": statistics.mean(values) if size else None,
        "median": statistics.median(values) if size else None,
        "stdev": statistics.stdev(values) if size >= 2 else None,
        "stdevDefinition": "sample_standard_deviation_ddof_1",
        "upRatio": sum(value > 0 for value in values) / size if size else None,
        "upDefinition": "strictly_positive_return; zero_is_not_up",
    }


def group_stats(rows: list[dict]) -> dict:
    groups = {str(group): describe([r["return"] for r in rows if r["group"] == group]) for group in (0, 1)}
    delta = None
    if groups["0"]["n"] and groups["1"]["n"]:
        delta = groups["1"]["mean"] - groups["0"]["mean"]
    return {"groups": groups, "delta": delta, "deltaBp": None if delta is None else delta * 10_000}


def quantile_linear(sorted_values: list[float], probability: float) -> float:
    if not sorted_values or not 0 <= probability <= 1:
        raise ValueError("invalid_quantile_input")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def moving_block_bootstrap(rows: list[dict], replicates: int = REPLICATES) -> dict:
    """Ordered complete daily rows; paired (G,r) blocks, no retries.

    Prefix differences speed up sums but do not alter start sampling, block
    concatenation, final truncation or group counts. Floating arithmetic may
    differ at rounding level from repeated element-by-element addition.
    """
    if type(replicates) is not int or replicates <= 0:
        raise ValueError("replicates_must_be_positive_integer")
    size = len(rows)
    result = {
        "method": "moving_block_bootstrap_paired_G_r",
        "blockLength": BLOCK_LENGTH,
        "replicatesRequested": replicates,
        "seed": SEED,
        "rng": "Python random.Random Mersenne Twister",
        "startCall": "randrange(N-29)",
        "candidateStarts": [0, size - BLOCK_LENGTH] if size >= BLOCK_LENGTH else None,
        "blocksPerReplicate": math.ceil(size / BLOCK_LENGTH),
        "replicatesValid": 0,
        "replicatesInvalidMissingGroup": 0,
        "randomDrawCount": 0,
        "minimumValidReplicates": MIN_VALID_REPLICATES,
        "protocolRun": replicates == REPLICATES,
        "ci95": None,
        "ci95Bp": None,
        "available": False,
        "unavailableReasons": [],
        "quantileDefinition": "sorted index (M-1)*p; adjacent linear interpolation",
    }
    if size < BLOCK_LENGTH:
        result["unavailableReasons"].append("fewer_than_30_rows")
        return result
    sums0, sums1, counts1 = [0.0], [0.0], [0]
    for row in rows:
        group, value = row["group"], row["return"]
        if group not in (0, 1) or value is None or not math.isfinite(value):
            raise ValueError("bootstrap_requires_valid_G_r_rows")
        sums0.append(sums0[-1] + (value if group == 0 else 0.0))
        sums1.append(sums1[-1] + (value if group == 1 else 0.0))
        counts1.append(counts1[-1] + group)
    rng = random.Random(SEED)
    draws_per_replicate = math.ceil(size / BLOCK_LENGTH)
    last_width = size - BLOCK_LENGTH * (draws_per_replicate - 1)
    deltas = []
    for _ in range(replicates):
        total0, total1, n1 = 0.0, 0.0, 0
        for block_number in range(draws_per_replicate):
            start = rng.randrange(size - 29)
            width = BLOCK_LENGTH if block_number < draws_per_replicate - 1 else last_width
            stop = start + width
            total0 += sums0[stop] - sums0[start]
            total1 += sums1[stop] - sums1[start]
            n1 += counts1[stop] - counts1[start]
        n0 = size - n1
        if n0 and n1:
            deltas.append(total1 / n1 - total0 / n0)
        else:
            result["replicatesInvalidMissingGroup"] += 1
    result["randomDrawCount"] = draws_per_replicate * replicates
    result["replicatesValid"] = len(deltas)
    if replicates != REPLICATES:
        result["unavailableReasons"].append("test_only_replicate_count_not_10000")
    if len(deltas) < MIN_VALID_REPLICATES:
        result["unavailableReasons"].append("fewer_than_9900_valid_replicates")
    if not result["unavailableReasons"]:
        deltas.sort()
        interval = [quantile_linear(deltas, p) for p in (0.025, 0.975)]
        result.update(available=True, ci95=interval, ci95Bp=[value * 10_000 for value in interval])
    return result


def metadata_gate_errors(metadata: dict) -> list[str]:
    errors = []
    required_values = {"timezone": "Asia/Shanghai", "symbol": "BTCUSDT", "marketType": "spot"}
    for field, expected in required_values.items():
        if metadata.get(field) != expected:
            errors.append(f"metadata.{field}_must_equal_{expected}")
    for field in ("calendarValidated", "hourlyAggregationValidated", "registered", "scorerFixturesValidated"):
        if metadata.get(field) is not True:
            errors.append(f"metadata.{field}_not_true")
    if metadata.get("researchDataType") != "real_market":
        errors.append("metadata.researchDataType_not_real_market")
    return errors


def missing_record(iso_date: str, metadata: dict, reason: str) -> dict:
    label_map = metadata.get("expectedDayGanzhi", {})
    if not isinstance(label_map, dict):
        label_map = {}
    label = label_map.get(iso_date)
    group = int(label[0] in GROUP1) if isinstance(label, str) and label in SIXTY else None
    return {"date": iso_date, "dayGanzhi": label if group is not None else None, "group": group,
            "valid": False, "errors": [reason], "return": None, "returnBp": None}


def score_period(index: dict[str, list[dict]], metadata: dict, period: tuple[str, str, str],
                 replicates: int, global_errors: list[dict]) -> dict:
    name, start_raw, end_raw = period
    start, end = parse_date(start_raw), parse_date(end_raw)
    if end < start:
        raise ValueError("period_end_before_start")
    expected_days = (end - start).days + 1
    all_days = []
    for current in date_range(start, end):
        iso_date = current.isoformat()
        matches = index.get(iso_date, [])
        if not matches:
            record = missing_record(iso_date, metadata, "missing_date")
        elif len(matches) != 1:
            record = missing_record(iso_date, metadata, "duplicate_date")
            labels = {r.get("dayGanzhi") for r in matches if isinstance(r.get("dayGanzhi"), str)}
            if len(labels) == 1:
                label = next(iter(labels))
                if label in SIXTY:
                    record.update(dayGanzhi=label, group=int(label[0] in GROUP1))
            record["duplicateCount"] = len(matches)
            record["inputRows"] = matches
        else:
            record = validate_row(matches[0], iso_date)
            record["inputRow"] = matches[0]
        all_days.append(record)
    valid = [row for row in all_days if row["valid"]]
    invalid = [row for row in all_days if not row["valid"]]
    stats = group_stats(valid)
    coverage = len(valid) / expected_days
    thresholds = {
        "minDays": 180, "minGroupDays": 60, "minimumCoverage": 0.99,
        "effectThreshold": EFFECT_THRESHOLD, "effectThresholdBp": 10,
        "sampleCountPass": len(valid) >= 180,
        "groupCountPass": all(stats["groups"][str(g)]["n"] >= 60 for g in (0, 1)),
        "coveragePass": coverage >= 0.99,
        "gapFree": not invalid,
    }
    yearly = []
    for year in range(start.year, end.year + 1):
        year_days = [row for row in all_days if row["date"].startswith(str(year))]
        year_valid = [row for row in year_days if row["valid"]]
        yearly.append({"year": year, "start": year_days[0]["date"], "end": year_days[-1]["date"],
                       "expectedDays": len(year_days), "validDays": len(year_valid),
                       "invalidDays": len(year_days) - len(year_valid), "descriptiveOnly": True,
                       **group_stats(year_valid)})
    gates = metadata_gate_errors(metadata)
    if global_errors:
        gates.append("unassignable_invalid_input_rows")
    enough = thresholds["sampleCountPass"] and thresholds["groupCountPass"]
    if invalid:
        bootstrap = {"available": False, "ci95": None, "ci95Bp": None,
                     "blockLength": BLOCK_LENGTH, "seed": SEED, "replicatesRequested": replicates,
                     "replicatesValid": 0, "randomDrawCount": 0,
                     "unavailableReasons": ["date_sequence_has_invalid_or_missing_days"],
                     "protocolRun": replicates == REPLICATES}
    elif not enough:
        bootstrap = {"available": False, "ci95": None, "ci95Bp": None,
                     "blockLength": BLOCK_LENGTH, "seed": SEED, "replicatesRequested": replicates,
                     "replicatesValid": 0, "randomDrawCount": 0,
                     "unavailableReasons": ["minimum_sample_or_group_count_not_met"],
                     "protocolRun": replicates == REPLICATES}
    else:
        bootstrap = moving_block_bootstrap(valid, replicates)
    delta = stats["delta"]
    effect_pass = delta is not None and delta >= EFFECT_THRESHOLD
    ci_pass = bootstrap["available"] and bootstrap["ci95"][0] > 0
    structural_quality = not gates and thresholds["coveragePass"] and thresholds["gapFree"]
    protocol_mode = replicates == REPLICATES
    eligible = structural_quality and enough and bootstrap["available"] and protocol_mode
    raw_thresholds_met = enough and effect_pass and ci_pass and thresholds["coveragePass"] and thresholds["gapFree"]
    if gates or not thresholds["coveragePass"] or invalid:
        status = "data_validation_failed"
    elif not enough:
        status = "insufficient_sample"
    elif not protocol_mode or not bootstrap["available"]:
        status = "data_validation_failed"
    elif effect_pass and ci_pass:
        status = "exploratory_supported"
    else:
        status = "not_supported"
    stage = "custom_exploratory_period"
    for fixed_name, fixed_start, fixed_end in DEFAULT_PERIODS:
        if start_raw == fixed_start and end_raw == fixed_end:
            stage = fixed_name
    caveats = [
        "Price-difference study only; no positions, leverage, costs or execution backtest.",
        "Arithmetic threshold satisfaction does not establish causation or a stable trading edge.",
        "Per-year results are descriptive; unstable years are retained rather than selected away.",
    ]
    if stage == "contaminated_historical_review":
        caveats.append("This historical period is contaminated by previously read cases; it is not a clean holdout.")
    if stage == "custom_exploratory_period":
        caveats.append("Custom dates are exploratory; this scorer never assigns forward_supported.")
    if not protocol_mode:
        caveats.append("Reduced replicate count is for tests only and cannot produce a formal confidence interval or support status.")
    if gates:
        caveats.append("Upstream gates are incomplete: the numbers are unvalidated descriptive/calculation diagnostics.")
    return {
        "periodName": name, "researchStage": stage, "start": start_raw, "end": end_raw,
        "status": status, "expectedDays": expected_days, "validDays": len(valid),
        "invalidDays": len(invalid), "coverage": coverage, "thresholds": thresholds,
        **stats, "yearly": yearly, "bootstrap": bootstrap, "gateErrors": gates,
        "effectThresholdPass": effect_pass, "ciLowerPositive": ci_pass,
        "arithmeticThresholdsMet": raw_thresholds_met,
        "eligibleForSupportConclusion": eligible,
        "conclusion": ("值得进一步验证；历史研究，不解锁交易执行" if status == "exploratory_supported"
                       else "本操作化版本未获支持" if status == "not_supported"
                       else "仅供描述或诊断；运行门槛/数据/样本尚未满足"),
        "allDays": all_days, "invalidDayRecords": invalid, "caveats": caveats,
    }


def score_document(document: dict, periods=None, replicates: int = REPLICATES) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get("metadata"), dict):
        raise ValueError("document_requires_metadata_object")
    rows = document.get("dailyRows")
    if not isinstance(rows, list):
        raise ValueError("document_requires_dailyRows_array")
    if type(replicates) is not int or replicates <= 0:
        raise ValueError("replicates_must_be_positive_integer")
    index = defaultdict(list)
    invalid_input = []
    for number, row in enumerate(rows):
        if not isinstance(row, dict):
            invalid_input.append({"inputIndex": number, "reason": "row_not_object", "input": row})
            continue
        try:
            day = parse_date(row.get("date"))
        except ValueError as exc:
            invalid_input.append({"inputIndex": number, "reason": str(exc), "input": row})
            continue
        index[day.isoformat()].append(row)
    configured_periods = list(DEFAULT_PERIODS if periods is None else periods)
    if not configured_periods:
        raise ValueError("at_least_one_period_required")
    scores = [score_period(index, document["metadata"], period, replicates, invalid_input) for period in configured_periods]
    assigned = set()
    for _, start, end in configured_periods:
        assigned.update(day.isoformat() for day in date_range(parse_date(start), parse_date(end)))
    return {
        "schemaVersion": "1.0", "scorerVersion": VERSION,
        "hypothesisId": HYPOTHESIS_ID, "hypothesisVersion": "0.1.0",
        "researchKind": "observational_price_difference_not_trading_backtest",
        "runtime": {"pythonVersion": sys.version, "pythonImplementation": platform.python_implementation(),
                    "scorerSha256": sha256(Path(__file__))},
        "inputMetadata": document["metadata"],
        "protocolRun": replicates == REPLICATES,
        "invalidInputRows": invalid_input,
        "outsideRequestedPeriods": [row for day, entries in sorted(index.items()) if day not in assigned for row in entries],
        "periods": scores,
    }


def parse_period(value: str) -> tuple[str, str, str]:
    pieces = value.split(":")
    if len(pieces) != 3 or not pieces[0]:
        raise argparse.ArgumentTypeError("period must be NAME:YYYY-MM-DD:YYYY-MM-DD")
    try:
        start, end = parse_date(pieces[1]), parse_date(pieces[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if end < start:
        raise argparse.ArgumentTypeError("period end precedes start")
    return tuple(pieces)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--period", action="append", type=parse_period,
                        help="NAME:START:END; repeat for several independent periods")
    parser.add_argument("--test-replicates", type=int,
                        help="test-only diagnostic count; disables formal CI/support unless exactly 10000")
    args = parser.parse_args()
    replicates = REPLICATES if args.test_replicates is None else args.test_replicates
    try:
        def reject_nonfinite_constant(token):
            raise ValueError(f"nonstandard_json_number:{token}; use valid JSON and positive finite prices")
        document = json.loads(args.input.read_text(encoding="utf-8-sig"), parse_constant=reject_nonfinite_constant)
        result = score_document(document, periods=args.period, replicates=replicates)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Input/scoring error: {exc}\n")
    result["inputFileSha256"] = sha256(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "protocolRun": result["protocolRun"],
                      "periods": [{"name": p["periodName"], "status": p["status"],
                                   "validDays": p["validDays"], "deltaBp": p["deltaBp"]} for p in result["periods"]]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
