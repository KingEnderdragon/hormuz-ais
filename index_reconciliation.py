"""
Independent reproduction/verification harness for straits.live's two composite
indices (Crisis Pressure, Escalation Probability), per GitHub issue #6 section 4
("Composite-index reproducibility").

Source of truth for the documented rules below: https://straits.live/methodology#hormuz-index
(methodologyVersion 0.4.0, extracted verbatim into docs/composite_index_methodology.md).
This module does not trust that document alone - every rule it encodes is checked
against straits.live's own https://straits.live/api/index audit endpoint, which
exposes per-component raw/score/weight/contribution plus a reconciliation block
(rawComposite, decayed, smoothedValue, anchor24h). A check fails loudly (hard
failure) if the documented rule stops matching what the API actually reports,
rather than silently trusting either source.

Usage:
    python index_reconciliation.py            # run continuously, poll every 5 min
    python index_reconciliation.py --once      # single poll, print JSON report, exit
                                                # nonzero if any hard check fails
"""
import argparse
import csv
import hashlib
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

import escalation_forecast_scoring

INDEX_URL = "https://straits.live/api/index"
HERE = os.path.dirname(__file__)
SNAPSHOT_DIR = os.path.join(HERE, "index_snapshots")
TIMESERIES_CSV = os.path.join(HERE, "index_components_timeseries.csv")
POLL_INTERVAL_SECONDS = 300  # matches the API's own updateFrequency (PT5M)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

# --- Documented methodology constants (straits.live/methodology#hormuz-index v0.4.0) ---
# Component insertion order is preserved (Python dict) and used as the fixed
# column order in TIMESERIES_CSV.
CRISIS_PRESSURE_WEIGHTS = {
    "aisTransitDeviation": 0.30,
    "insuranceMultiple": 0.25,
    "gdeltEventPressure": 0.25,
    "brentOptionsDread": 0.20,
}
ESCALATION_WEIGHTS = {
    "polymarketAggregate": 0.40,
    "brentTermStructureSlope": 0.20,
    "manifoldForwardContracts": 0.25,
    "kalshiForwardContracts": 0.15,
}

OVERRIDE_SCORE_THRESHOLD = 75  # Crisis Pressure only - no override rule for Escalation
OVERRIDE_FLOOR_MULT = 0.9

EWMA_HALF_LIFE_FALL_HOURS = 36  # asymmetric decay: rises propagate instantly, falls decay
RAIL_MAX_RISE_24H = 25
RAIL_MAX_FALL_24H = 10
SMOOTHING_TOLERANCE = 0.15  # points; accounts for the API's own internal rounding

BAND_BOUNDARIES = [
    (0, 19, "calm"),
    (20, 39, "watchful"),
    (40, 59, "elevated"),
    (60, 79, "high"),
    (80, 100, "extreme"),
]

DISPERSION_CLAMP = (2, 15)

WEIGHT_SUM_TOLERANCE = 0.05


# --- Fetch + archive ---

def fetch_index():
    req = urllib.request.Request(INDEX_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def archive_raw(data):
    """Content-addressed archival: filename encodes asOf + a hash of the body,
    so re-archiving an identical poll is a no-op and any post-hoc edit to an
    archived file is detectable (hash no longer matches filename)."""
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    as_of = data.get("asOf", datetime.now(timezone.utc).isoformat())
    safe_ts = as_of.replace(":", "-")
    raw_bytes = json.dumps(data, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(raw_bytes).hexdigest()[:12]
    path = os.path.join(SNAPSHOT_DIR, f"{safe_ts}_{digest}.json")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2, sort_keys=True))
    return path


def load_latest_archived_snapshot():
    """Used by --once mode to recover `prev` across separate process invocations."""
    if not os.path.isdir(SNAPSHOT_DIR):
        return None
    files = sorted(f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json"))
    if not files:
        return None
    with open(os.path.join(SNAPSHOT_DIR, files[-1]), encoding="utf-8") as f:
        return json.load(f)


# --- Individual rule checks ---

def verify_weighted_sum(components, reported_raw_composite, tol=WEIGHT_SUM_TOLERANCE):
    total = sum(c["score"] * c["weight"] for c in components.values())
    diff = abs(total - reported_raw_composite)
    return {
        "check": "weighted_sum",
        "expected": round(total, 4),
        "reported": reported_raw_composite,
        "diff": round(diff, 4),
        "ok": diff <= tol,
    }


def verify_documented_weights(components, expected_weights, tol=1e-6):
    missing = sorted(name for name in expected_weights if name not in components)
    if missing:
        return {
            "check": "documented_weights",
            "missing_components": missing,
            "mismatches": {},
            "ok": False,
        }

    nonfresh = sorted(
        name for name in expected_weights
        if components[name].get("health") != "fresh"
    )
    if nonfresh:
        return {
            "check": "documented_weights",
            "missing_components": [],
            "nonfresh_components": nonfresh,
            "mismatches": {},
            "ok": None,
            "note": "nominal weights do not apply while health-based redistribution is active",
        }

    mismatches = {}
    for name, expected_w in expected_weights.items():
        actual_w = components.get(name, {}).get("weight")
        if actual_w is None or abs(actual_w - expected_w) > tol:
            mismatches[name] = {"expected": expected_w, "actual": actual_w}
    return {
        "check": "documented_weights",
        "missing_components": [],
        "nonfresh_components": [],
        "mismatches": mismatches,
        "ok": not mismatches,
    }


def band_for(value):
    for lo, hi, label in BAND_BOUNDARIES:
        if lo <= value <= hi:
            return label
    return None


def verify_band(value, reported_band):
    expected = band_for(value)
    return {
        "check": "band_boundary",
        "expected": expected,
        "reported": reported_band,
        "ok": expected == reported_band,
    }


def verify_override_floor(components, published_value,
                           threshold=OVERRIDE_SCORE_THRESHOLD, mult=OVERRIDE_FLOOR_MULT):
    """Crisis Pressure only. 'When any single input scores >= 75, the headline
    is floored at 0.9x that input's score' - this is an invariant on the
    published value, checkable every poll even without a triggering event."""
    qualifying = [c["score"] for c in components.values() if c["score"] >= threshold]
    if not qualifying:
        return {"check": "override_floor", "floor": None, "ok": True,
                 "note": "no component >= threshold this poll"}
    floor = mult * max(qualifying)
    return {
        "check": "override_floor",
        "floor": round(floor, 4),
        "published_value": published_value,
        "ok": published_value >= floor - 1e-6,
    }


def verify_dispersion(components, reported_confidence, clamp=DISPERSION_CLAMP):
    """straits.live documents the confidence figure only as 'weighted standard
    deviation of component scores, scaled and clamped to 2-15' without giving
    the scale factor, so this check is advisory (ok=None), not a hard failure."""
    scores = [c["score"] for c in components.values()]
    weights = [c["weight"] for c in components.values()]
    wsum = sum(weights)
    mean = sum(s * w for s, w in zip(scores, weights)) / wsum
    variance = sum(w * (s - mean) ** 2 for s, w in zip(scores, weights)) / wsum
    stdev = math.sqrt(variance)
    clamped = max(clamp[0], min(clamp[1], stdev))
    return {
        "check": "dispersion_confidence",
        "raw_weighted_stdev": round(stdev, 4),
        "clamped_estimate": round(clamped, 4),
        "reported": reported_confidence,
        "ok": None,
        "note": "advisory - scale factor from stdev to reported confidence is undocumented",
    }


def verify_missing_data_redistribution(components, expected_weights):
    stale = {name: c for name, c in components.items() if c.get("health") != "fresh"}
    if not stale:
        return {"check": "missing_data_redistribution", "stale_components": [], "ok": True}
    fresh_names = [n for n in expected_weights if n not in stale]
    fresh_original_total = sum(expected_weights[n] for n in fresh_names)
    redistributed = {n: expected_weights[n] / fresh_original_total for n in fresh_names}
    mismatches = {}
    for n in fresh_names:
        actual_w = components[n]["weight"]
        if abs(actual_w - redistributed[n]) > 1e-3:
            mismatches[n] = {"expected": redistributed[n], "actual": actual_w}
    return {
        "check": "missing_data_redistribution",
        "stale_components": list(stale.keys()),
        "expected_redistributed_weights": {k: round(v, 4) for k, v in redistributed.items()},
        "mismatches": mismatches,
        "ok": not mismatches,
    }


def reconstruct_smoothed(prev_smoothed, raw_composite, hours_since_prev,
                          half_life_fall_hours=EWMA_HALF_LIFE_FALL_HOURS):
    """Asymmetric EWMA toward rawComposite: rises propagate instantly, falls
    decay with a documented 36h half-life."""
    if raw_composite >= prev_smoothed:
        return raw_composite
    decay_factor = 0.5 ** (hours_since_prev / half_life_fall_hours)
    return raw_composite + (prev_smoothed - raw_composite) * decay_factor


def verify_smoothing_and_rails(prev_reconciliation, curr_reconciliation):
    """Requires the previous poll's reconciliation block. Returns ok=None
    (skip, not a failure) when there is no prior snapshot to chain from -
    e.g. the very first poll in a fresh archive."""
    if prev_reconciliation is None:
        return {"check": "smoothing_and_rails", "ok": None, "note": "no prior snapshot"}
    prev_smoothed = prev_reconciliation["smoothedValue"]
    raw_composite = curr_reconciliation["rawComposite"]
    hours_since_prev = curr_reconciliation["hoursSincePrevious"]
    anchor = curr_reconciliation["anchor24h"]

    reconstructed_decayed = reconstruct_smoothed(prev_smoothed, raw_composite, hours_since_prev)
    rail_lo, rail_hi = anchor - RAIL_MAX_FALL_24H, anchor + RAIL_MAX_RISE_24H
    reconstructed_smoothed = max(rail_lo, min(rail_hi, reconstructed_decayed))

    diff = abs(reconstructed_smoothed - curr_reconciliation["smoothedValue"])
    return {
        "check": "smoothing_and_rails",
        "reconstructed_decayed": round(reconstructed_decayed, 4),
        "reported_decayed": curr_reconciliation["decayed"],
        "reconstructed_smoothed": round(reconstructed_smoothed, 4),
        "reported_smoothed": curr_reconciliation["smoothedValue"],
        "diff": round(diff, 4),
        "ok": diff <= SMOOTHING_TOLERANCE,
    }


# --- Composed per-index validation ---

def validate_crisis_pressure(curr, prev=None):
    cp = curr["indices"]["crisisPressure"]
    components = cp["components"]
    recon = cp["reconciliation"]
    checks = [
        verify_weighted_sum(components, recon["rawComposite"]),
        verify_documented_weights(components, CRISIS_PRESSURE_WEIGHTS),
        verify_band(cp["value"], cp["band"]),
        verify_override_floor(components, cp["value"]),
        verify_missing_data_redistribution(components, CRISIS_PRESSURE_WEIGHTS),
        verify_dispersion(components, cp["confidence"]),
    ]
    prev_recon = prev["indices"]["crisisPressure"]["reconciliation"] if prev else None
    checks.append(verify_smoothing_and_rails(prev_recon, recon))
    return checks


def validate_escalation_probability(curr, prev=None):
    ep = curr["indices"]["escalationProbability"]
    components = ep["components"]
    recon = ep["reconciliation"]
    checks = [
        verify_weighted_sum(components, recon["rawComposite"]),
        verify_documented_weights(components, ESCALATION_WEIGHTS),
        verify_band(ep["value"], ep["band"]),
        verify_missing_data_redistribution(components, ESCALATION_WEIGHTS),
        verify_dispersion(components, ep["confidence"]),
    ]
    prev_recon = prev["indices"]["escalationProbability"]["reconciliation"] if prev else None
    checks.append(verify_smoothing_and_rails(prev_recon, recon))
    return checks


def hard_failures(checks):
    return [c["check"] for c in checks if c["ok"] is False]


# --- Compact preserved-component CSV ---

def _csv_fields():
    fields = ["timestamp_utc", "straits_as_of", "methodology_version"]
    for prefix, weights in (("cp", CRISIS_PRESSURE_WEIGHTS), ("ep", ESCALATION_WEIGHTS)):
        fields += [f"{prefix}_value", f"{prefix}_band", f"{prefix}_confidence",
                   f"{prefix}_raw_composite", f"{prefix}_decayed", f"{prefix}_smoothed",
                   f"{prefix}_anchor24h"]
        for name in weights:
            fields += [f"{prefix}_{name}_raw", f"{prefix}_{name}_score",
                       f"{prefix}_{name}_weight", f"{prefix}_{name}_contribution",
                       f"{prefix}_{name}_health"]
    fields.append("hard_failures")
    return fields


CSV_FIELDS = _csv_fields()


def ensure_csv():
    if not os.path.exists(TIMESERIES_CSV):
        with open(TIMESERIES_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
        return
    with open(TIMESERIES_CSV, newline="", encoding="utf-8") as f:
        existing_header = next(csv.reader(f), [])
    if existing_header != CSV_FIELDS:
        raise RuntimeError(
            f"{TIMESERIES_CSV} header does not match current CSV_FIELDS - refusing to "
            f"append (see straits_poller.py's ensure_csv for why this matters: a "
            f"schema change here previously corrupted straits_live_timeseries.csv "
            f"by silent column misalignment).\n"
            f"existing header: {existing_header}\n"
            f"current CSV_FIELDS: {CSV_FIELDS}"
        )


def _row_for_index(prefix, index_block, weights):
    row = {
        f"{prefix}_value": index_block["value"],
        f"{prefix}_band": index_block["band"],
        f"{prefix}_confidence": index_block["confidence"],
        f"{prefix}_raw_composite": index_block["reconciliation"]["rawComposite"],
        f"{prefix}_decayed": index_block["reconciliation"]["decayed"],
        f"{prefix}_smoothed": index_block["reconciliation"]["smoothedValue"],
        f"{prefix}_anchor24h": index_block["reconciliation"]["anchor24h"],
    }
    for name in weights:
        c = index_block["components"].get(name, {})
        row[f"{prefix}_{name}_raw"] = c.get("raw")
        row[f"{prefix}_{name}_score"] = c.get("score")
        row[f"{prefix}_{name}_weight"] = c.get("weight")
        row[f"{prefix}_{name}_contribution"] = c.get("contribution")
        row[f"{prefix}_{name}_health"] = c.get("health")
    return row


def write_row(data, all_checks):
    row = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "straits_as_of": data.get("asOf"),
        "methodology_version": data.get("methodologyVersion"),
    }
    row.update(_row_for_index("cp", data["indices"]["crisisPressure"], CRISIS_PRESSURE_WEIGHTS))
    row.update(_row_for_index("ep", data["indices"]["escalationProbability"], ESCALATION_WEIGHTS))
    row["hard_failures"] = "|".join(hard_failures(all_checks))
    assert set(row.keys()) == set(CSV_FIELDS), (
        f"row keys don't match CSV_FIELDS: {set(row.keys()) ^ set(CSV_FIELDS)}"
    )
    with open(TIMESERIES_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)


# --- Entry points ---

def run_once(prev=None):
    data = fetch_index()
    archive_raw(data)
    escalation_forecast_scoring.freeze(data)
    cp_checks = validate_crisis_pressure(data, prev)
    ep_checks = validate_escalation_probability(data, prev)
    all_checks = cp_checks + ep_checks
    ensure_csv()
    write_row(data, all_checks)
    report = {
        "asOf": data.get("asOf"),
        "methodologyVersion": data.get("methodologyVersion"),
        "crisisPressure": cp_checks,
        "escalationProbability": ep_checks,
        "hard_failures": hard_failures(all_checks),
    }
    return data, report


def run():
    prev = load_latest_archived_snapshot()
    print(f"Polling {INDEX_URL} every {POLL_INTERVAL_SECONDS}s -> {SNAPSHOT_DIR}, {TIMESERIES_CSV}")
    while True:
        try:
            data, report = run_once(prev)
            failures = report["hard_failures"]
            if failures:
                print(f"[HARD FAILURE] {data['asOf']} checks failed: {failures}")
                for c in report["crisisPressure"] + report["escalationProbability"]:
                    if c["ok"] is False:
                        print(f"  {c}")
            else:
                print(f"{data['asOf']} crisis={data['indices']['crisisPressure']['value']} "
                      f"escalation={data['indices']['escalationProbability']['value']} "
                      f"all composite-index checks passed")
            prev = data
        except Exception as e:
            print(f"Poll failed: {e}")
        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="single poll, print JSON report, exit nonzero on hard failure")
    args = parser.parse_args()
    if args.once:
        prev = load_latest_archived_snapshot()
        data, report = run_once(prev)
        print(json.dumps(report, indent=2))
        sys.exit(1 if report["hard_failures"] else 0)
    else:
        try:
            run()
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()
