"""Independent validation for straits.live layers 1, 3, and 5.

The commands consume archived inputs and emit machine-readable JSON. Network
fetching is deliberately kept out of this module so every audit can be rerun
against the exact source artifacts used for a report.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


COUNT_ALIASES = {
    "total": ("total", "n_total", "count", "transits_count", "vessel_count_total"),
    "tanker": ("tanker", "n_tanker", "nTanker", "daily_tanker_count", "vessel_count_tanker"),
    "cargo": ("cargo", "n_cargo", "nCargo", "daily_cargo_count"),
    "container": ("container", "n_container", "nContainer", "daily_container_count", "vessel_count_container"),
    "dry_bulk": ("dry_bulk", "n_dry_bulk", "dryBulk", "vessel_count_dry_bulk"),
    "general_cargo": ("general_cargo", "n_general_cargo", "generalCargo", "vessel_count_general_cargo"),
    "roro": ("roro", "n_roro", "RoRo", "vessel_count_RoRo"),
}
DATE_ALIASES = ("date", "Date", "asOfDate", "transits_as_of_date")
# Bahamas, St Kitts, Panama, Palau, Cook Islands, Marshall Islands,
# Cameroon, CÃ´te d'Ivoire, Gabon, Liberia, SÃ£o TomÃ©, and Togo. Keep this
# versioned with the methodology; it is intentionally not a generic FOC list.
FOC_MIDS = {308, 309, 310, 311, 341, 351, 352, 353, 354, 355, 356, 357, 511, 518, 538, 613, 619, 626, 636, 637, 668, 671}


def read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def first(row: dict[str, Any], aliases: Iterable[str]) -> Any:
    for name in aliases:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def integer(value: Any) -> int | None:
    value = number(value)
    return None if value is None else int(value)


def boolean(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def normalize_transits(rows: list[dict[str, str]]) -> tuple[dict[str, dict[str, int | None]], list[str]]:
    normalized: dict[str, dict[str, int | None]] = {}
    duplicates: list[str] = []
    for row in rows:
        raw_date = first(row, DATE_ALIASES)
        if raw_date is None:
            raise ValueError(f"transit row lacks a date field: {row}")
        day = str(raw_date)[:10]
        if day in normalized:
            duplicates.append(day)
        normalized[day] = {key: integer(first(row, aliases)) for key, aliases in COUNT_ALIASES.items()}
    return normalized, sorted(set(duplicates))


def portwatch_parity(
    portwatch_rows: list[dict[str, str]],
    straits_rows: list[dict[str, str]],
    baseline_start: str,
    baseline_end: str,
    previous_portwatch_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    portwatch, pw_duplicates = normalize_transits(portwatch_rows)
    straits, st_duplicates = normalize_transits(straits_rows)
    pw_dates, st_dates = set(portwatch), set(straits)
    common = sorted(pw_dates & st_dates)
    mismatches = []
    field_coverage = {}
    for field in COUNT_ALIASES:
        pw_count = sum(values[field] is not None for values in portwatch.values())
        st_count = sum(values[field] is not None for values in straits.values())
        field_coverage[field] = {"portwatch_rows": pw_count, "straits_rows": st_count, "comparable": bool(pw_count and st_count)}
    for day in common:
        for field in COUNT_ALIASES:
            expected, actual = portwatch[day][field], straits[day][field]
            if expected is not None and actual is not None and expected != actual:
                mismatches.append({"date": day, "field": field, "portwatch": expected, "straits": actual})

    baseline_values = [
        values["total"] for day, values in portwatch.items()
        if baseline_start <= day <= baseline_end and values["total"] is not None
    ]
    baseline = statistics.median(baseline_values) if baseline_values else None
    baseline_checks = []
    if baseline:
        for row in straits_rows:
            day = str(first(row, DATE_ALIASES) or "")[:10]
            count = integer(first(row, COUNT_ALIASES["total"]))
            published_baseline = number(first(row, ("baseline", "transits_baseline")))
            throughput = integer(first(row, ("throughputPct", "throughput_pct")))
            if published_baseline is not None and published_baseline != baseline:
                baseline_checks.append({"date": day, "field": "baseline", "computed": baseline, "published": published_baseline})
            expected_pct = round(100 * count / baseline) if count is not None else None
            if throughput is not None and throughput != expected_pct:
                baseline_checks.append({"date": day, "field": "throughput_pct", "computed": expected_pct, "published": throughput})

    revisions = []
    if previous_portwatch_rows is not None:
        previous, _ = normalize_transits(previous_portwatch_rows)
        for day in sorted(set(previous) & pw_dates):
            for field in COUNT_ALIASES:
                old, new = previous[day][field], portwatch[day][field]
                if old != new:
                    revisions.append({"date": day, "field": field, "previous": old, "current": new})

    hard_failures = len(pw_duplicates) + len(st_duplicates) + len(mismatches) + len(baseline_checks)
    return {
        "audit": "portwatch_parity",
        "summary": {
            "portwatch_dates": len(pw_dates), "straits_dates": len(st_dates), "common_dates": len(common),
            "mismatch_count": len(mismatches), "baseline_check_failures": len(baseline_checks),
            "revision_count": len(revisions), "hard_failures": hard_failures,
        },
        "duplicates": {"portwatch": pw_duplicates, "straits": st_duplicates},
        "missing_dates": {"from_straits": sorted(pw_dates - st_dates), "from_portwatch": sorted(st_dates - pw_dates)},
        "field_coverage": field_coverage,
        "count_mismatches": mismatches,
        "baseline": {"start": baseline_start, "end": baseline_end, "observations": len(baseline_values), "median": baseline},
        "baseline_checks": baseline_checks,
        "portwatch_revisions": revisions,
    }


def audit_label(row: dict[str, str]) -> str:
    if boolean(row.get("feed_outage")):
        return "indeterminate_feed_outage"
    if boolean(row.get("corroborated_dark")):
        return "corroborated_dark"
    if boolean(row.get("port_arrival")):
        return "false_positive_port_arrival"
    if row.get("reappeared_at") or boolean(row.get("other_provider_seen")):
        return "false_positive_reappeared"
    return "unresolved"


def candidate_matches(row: dict[str, str], min_sightings: int, min_hours: float, max_hours: float, bounds: tuple[float, float, float, float]) -> bool:
    try:
        vessel_type = row.get("vessel_type", "").lower()
        sightings = int(row["total_sightings"])
        hours = float(row["hours_missing"])
        lat, lon = float(row["last_lat"]), float(row["last_lon"])
    except (KeyError, TypeError, ValueError):
        return False
    min_lat, max_lat, min_lon, max_lon = bounds
    return vessel_type == "tanker" and sightings >= min_sightings and min_hours <= hours <= max_hours and min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def classification_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    resolved = [row for row in rows if audit_label(row) in {"corroborated_dark", "false_positive_port_arrival", "false_positive_reappeared"}]
    true_positive = sum(audit_label(row) == "corroborated_dark" for row in resolved)
    false_positive = len(resolved) - true_positive
    precision = true_positive / len(resolved) if resolved else None
    return {"resolved": len(resolved), "true_positive": true_positive, "false_positive": false_positive, "precision": precision, "false_positive_rate": (false_positive / len(resolved) if resolved else None)}


def ais_gap_audit(rows: list[dict[str, str]]) -> dict[str, Any]:
    labels = Counter(audit_label(row) for row in rows)
    sensitivity = []
    for sightings in (25, 50, 100):
        for window in ((2, 24), (3, 24), (3, 36)):
            selected = [row for row in rows if candidate_matches(row, sightings, *window, (24, 28, 55, 58))]
            sensitivity.append({"min_sightings": sightings, "min_hours": window[0], "max_hours": window[1], "flagged": len(selected), **classification_metrics(selected)})
    default = [row for row in rows if candidate_matches(row, 50, 3, 24, (24, 28, 55, 58))]
    return {
        "audit": "ais_gap",
        "semantic_label": "vessels satisfying the straits.live disappearance rule",
        "sample_size": len(rows), "outcomes": dict(sorted(labels.items())),
        "default_rule": {"min_sightings": 50, "missing_hours": [3, 24], "bounds": {"lat": [24, 28], "lon": [55, 58]}, "flagged": len(default), **classification_metrics(default)},
        "sensitivity": sensitivity,
    }


def nearest_reference(point: dict[str, str], reference: list[dict[str, str]], max_seconds: int) -> dict[str, str] | None:
    stamp = parse_iso(str(first(point, ("iso", "timestamp_utc", "as_of"))))
    candidates = []
    for row in reference:
        ref_stamp = parse_iso(str(first(row, ("iso", "timestamp_utc", "as_of"))))
        delta = abs((stamp - ref_stamp).total_seconds())
        if delta <= max_seconds:
            candidates.append((delta, row))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def market_audit(straits: list[dict[str, str]], reference: list[dict[str, str]], tolerance: float, max_seconds: int) -> dict[str, Any]:
    comparisons, missing = [], []
    for point in straits:
        stamp = str(first(point, ("iso", "timestamp_utc", "as_of")))
        match = nearest_reference(point, reference, max_seconds)
        if match is None:
            missing.append(stamp)
            continue
        for field in ("brent", "wti"):
            actual, expected = number(point.get(field)), number(match.get(field))
            if actual is None or expected is None:
                continue
            delta = abs(actual - expected)
            comparisons.append({"timestamp": stamp, "field": field, "straits": actual, "reference": expected, "absolute_delta": delta, "ok": delta <= tolerance})
    return {"compared_values": len(comparisons), "missing_reference_timestamps": missing, "tolerance_usd": tolerance, "failures": [item for item in comparisons if not item["ok"]], "comparisons": comparisons}


def imo_is_valid(value: str) -> bool:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return len(digits) == 7 and sum(int(digits[i]) * (7 - i) for i in range(6)) % 10 == int(digits[-1])


def expected_risk(row: dict[str, str]) -> str:
    sanctioned = boolean(row.get("ofac_match"))
    dark = boolean(row.get("ais_dark"))
    tanker = row.get("vessel_type", "").lower() == "tanker"
    mmsi = "".join(ch for ch in row.get("mmsi", "") if ch.isdigit())
    foc_tanker = tanker and len(mmsi) >= 3 and int(mmsi[:3]) in FOC_MIDS
    imo = row.get("imo", "")
    spoofed = bool(imo) and not imo_is_valid(imo)
    if sanctioned or (foc_tanker and dark) or (spoofed and (dark or foc_tanker)):
        return "high"
    if dark or foc_tanker or spoofed:
        return "moderate"
    return "low"


def risk_audit(rows: list[dict[str, str]]) -> dict[str, Any]:
    mismatches = []
    for row in rows:
        expected = expected_risk(row)
        actual = row.get("published_risk", "").lower()
        if actual != expected:
            mismatches.append({"mmsi": row.get("mmsi"), "imo": row.get("imo"), "published": actual, "computed": expected})
    return {"vessels": len(rows), "mismatch_count": len(mismatches), "mismatches": mismatches}


def editorial_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    required = ("field", "value", "classification", "method_version", "as_of", "source_name", "source_url")
    allowed = {"observed", "estimated", "derived", "forecast", "editorial"}
    failures = []
    for index, record in enumerate(records):
        missing = [field for field in required if record.get(field) in (None, "")]
        if missing:
            failures.append({"index": index, "field": record.get("field"), "problem": "missing provenance", "missing": missing})
        if record.get("classification") not in allowed:
            failures.append({"index": index, "field": record.get("field"), "problem": "invalid classification", "value": record.get("classification")})
        try:
            parse_iso(str(record.get("as_of")))
        except (TypeError, ValueError):
            failures.append({"index": index, "field": record.get("field"), "problem": "invalid as_of"})
    return {"records": len(records), "failure_count": len(failures), "failures": failures}


def section5_audit(oil_path: str, reference_path: str, risk_path: str, editorial_path: str, tolerance: float, max_seconds: int) -> dict[str, Any]:
    with Path(editorial_path).open(encoding="utf-8") as handle:
        editorial = json.load(handle)
    market = market_audit(read_csv(oil_path), read_csv(reference_path), tolerance, max_seconds)
    risk = risk_audit(read_csv(risk_path))
    provenance = editorial_audit(editorial)
    return {"audit": "market_risk_editorial", "market": market, "risk": risk, "editorial": provenance, "hard_failures": len(market["failures"]) + risk["mismatch_count"] + provenance["failure_count"]}


def emit(report: dict[str, Any], output: str | None) -> int:
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(serialized, encoding="utf-8")
    else:
        print(serialized, end="")
    failures = report.get("hard_failures", report.get("summary", {}).get("hard_failures", 0))
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    parity = sub.add_parser("portwatch", help="compare archived PortWatch and straits transit CSVs")
    parity.add_argument("--portwatch", required=True); parity.add_argument("--straits", required=True)
    parity.add_argument("--previous-portwatch"); parity.add_argument("--baseline-start", default="2025-02-28")
    parity.add_argument("--baseline-end", default="2026-02-27"); parity.add_argument("--output")
    gaps = sub.add_parser("ais-gaps", help="audit a labeled AIS-gap candidate CSV")
    gaps.add_argument("--labels", required=True); gaps.add_argument("--output")
    fields = sub.add_parser("fields", help="audit oil parity, vessel-risk rules, and editorial provenance")
    fields.add_argument("--oil", required=True); fields.add_argument("--oil-reference", required=True)
    fields.add_argument("--risk", required=True); fields.add_argument("--editorial", required=True)
    fields.add_argument("--price-tolerance", type=float, default=0.25); fields.add_argument("--max-time-delta", type=int, default=900)
    fields.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "portwatch":
        previous = read_csv(args.previous_portwatch) if args.previous_portwatch else None
        return emit(portwatch_parity(read_csv(args.portwatch), read_csv(args.straits), args.baseline_start, args.baseline_end, previous), args.output)
    if args.command == "ais-gaps":
        return emit(ais_gap_audit(read_csv(args.labels)), args.output)
    return emit(section5_audit(args.oil, args.oil_reference, args.risk, args.editorial, args.price_tolerance, args.max_time_delta), args.output)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"validation input error: {exc}", file=sys.stderr)
        raise SystemExit(2)


