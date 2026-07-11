"""
Unit tests for index_reconciliation.py, using a real captured /api/index
response (2026-07-11T04:04:11.860Z) as a fixture so the arithmetic checks are
verified deterministically without hitting the network.

Run: python test_index_reconciliation.py
"""
import json

import index_reconciliation as ir

FIXTURE = json.loads("""
{
  "schema": "hormuz-index-v0.2",
  "asOf": "2026-07-11T04:04:11.860Z",
  "methodologyVersion": "0.4.0",
  "indices": {
    "crisisPressure": {
      "value": 87, "confidence": 4, "band": "extreme", "delta24h": -2, "delta7d": 3,
      "indexHealth": "fresh",
      "components": {
        "aisTransitDeviation": {"raw": 38.63636363636363, "score": 71.36363636363637, "weight": 0.3, "contribution": 21.40909090909091, "health": "fresh", "asOf": "2026-07-10T14:00:24.133Z"},
        "insuranceMultiple": {"raw": 8, "score": 90, "weight": 0.25, "contribution": 22.5, "health": "fresh", "asOf": "2026-07-09T15:42:10.619Z"},
        "gdeltEventPressure": {"raw": 12.600548687557179, "score": 80.20109737511436, "weight": 0.25, "contribution": 20.05027434377859, "health": "fresh", "asOf": "2026-07-11T00:00:00.000Z"},
        "brentOptionsDread": {"raw": 44.67, "score": 57.78333333333334, "weight": 0.2, "contribution": 11.556666666666668, "health": "fresh", "asOf": "2026-07-11T04:03:07.180Z"}
      },
      "reconciliation": {"rawComposite": 75.52, "decayed": 86.81, "smoothedValue": 86.81, "anchor24h": 89, "hoursSincePrevious": 0.09}
    },
    "escalationProbability": {
      "value": 61, "confidence": 12, "band": "high", "delta24h": 0, "delta7d": 0,
      "indexHealth": "fresh",
      "components": {
        "polymarketAggregate": {"raw": 0.8472249414088596, "score": 84.72249414088596, "weight": 0.4, "contribution": 33.888997656354384, "health": "fresh", "asOf": "2026-07-11T04:03:21.663Z", "totalVolumeUsd": 27832317.70626115},
        "brentTermStructureSlope": {"raw": 4.49, "score": 40.71666666666667, "weight": 0.2, "contribution": 8.143333333333334, "health": "fresh", "asOf": "2026-07-11T03:52:04.976Z", "totalVolumeUsd": 0},
        "manifoldForwardContracts": {"raw": 0.12978426439592772, "score": 12.978426439592772, "weight": 0.25, "contribution": 3.244606609898193, "health": "fresh", "asOf": "2026-07-11T04:03:21.663Z", "totalVolumeUsd": 36593.76959676884},
        "kalshiForwardContracts": {"raw": 0.8476995405753721, "score": 84.7699540575372, "weight": 0.15, "contribution": 12.71549310863058, "health": "fresh", "asOf": "2026-07-11T04:03:21.663Z", "totalVolumeUsd": 9689275.96}
      },
      "reconciliation": {"rawComposite": 57.99, "decayed": 60.92, "smoothedValue": 60.92, "anchor24h": 61, "hoursSincePrevious": 0.09}
    }
  }
}
""")


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def test_weighted_sum():
    cp = FIXTURE["indices"]["crisisPressure"]
    r = ir.verify_weighted_sum(cp["components"], cp["reconciliation"]["rawComposite"])
    check(f"crisisPressure weighted sum: expected={r['expected']} reported={r['reported']} diff={r['diff']}", r["ok"])

    ep = FIXTURE["indices"]["escalationProbability"]
    r = ir.verify_weighted_sum(ep["components"], ep["reconciliation"]["rawComposite"])
    check(f"escalationProbability weighted sum: expected={r['expected']} reported={r['reported']} diff={r['diff']}", r["ok"])


def test_documented_weights_match():
    cp = FIXTURE["indices"]["crisisPressure"]
    r = ir.verify_documented_weights(cp["components"], ir.CRISIS_PRESSURE_WEIGHTS)
    check("crisisPressure weights match documented methodology", r["ok"])

    ep = FIXTURE["indices"]["escalationProbability"]
    r = ir.verify_documented_weights(ep["components"], ir.ESCALATION_WEIGHTS)
    check("escalationProbability weights match documented methodology", r["ok"])


def test_documented_weights_defer_to_redistribution_when_stale():
    import copy
    data = copy.deepcopy(FIXTURE)
    cp = data["indices"]["crisisPressure"]
    components = cp["components"]
    stale_name = "brentOptionsDread"
    components[stale_name]["health"] = "stale"
    components[stale_name]["weight"] = 0.0
    components[stale_name]["contribution"] = 0.0

    fresh_names = [name for name in ir.CRISIS_PRESSURE_WEIGHTS if name != stale_name]
    fresh_total = sum(ir.CRISIS_PRESSURE_WEIGHTS[name] for name in fresh_names)
    for name in fresh_names:
        weight = ir.CRISIS_PRESSURE_WEIGHTS[name] / fresh_total
        components[name]["weight"] = weight
        components[name]["contribution"] = components[name]["score"] * weight
    cp["reconciliation"]["rawComposite"] = sum(
        component["score"] * component["weight"] for component in components.values()
    )

    checks = ir.validate_crisis_pressure(data)
    nominal = next(item for item in checks if item["check"] == "documented_weights")
    redistribution = next(item for item in checks if item["check"] == "missing_data_redistribution")
    check("nominal weight check is advisory during legitimate redistribution", nominal["ok"] is None)
    check("health-based redistribution remains authoritative and passes", redistribution["ok"])
    check("correct stale-component redistribution produces no hard failures", not ir.hard_failures(checks))


def test_band_boundaries():
    check("87 -> extreme", ir.band_for(87) == "extreme")
    check("61 -> high", ir.band_for(61) == "high")
    check("0 -> calm", ir.band_for(0) == "calm")
    check("19 -> calm, 20 -> watchful (boundary)", ir.band_for(19) == "calm" and ir.band_for(20) == "watchful")
    check("100 -> extreme", ir.band_for(100) == "extreme")

    cp = FIXTURE["indices"]["crisisPressure"]
    r = ir.verify_band(cp["value"], cp["band"])
    check("reported crisisPressure band matches computed band", r["ok"])


def test_override_floor_holds():
    cp = FIXTURE["indices"]["crisisPressure"]
    r = ir.verify_override_floor(cp["components"], cp["value"])
    # insuranceMultiple scores 90 >= 75 -> floor = 0.9*90 = 81; published value 87 >= 81
    check(f"override floor: floor={r['floor']} published={r['published_value']}", r["ok"])


def test_missing_data_redistribution_noop_when_all_fresh():
    cp = FIXTURE["indices"]["crisisPressure"]
    r = ir.verify_missing_data_redistribution(cp["components"], ir.CRISIS_PRESSURE_WEIGHTS)
    check("no stale components -> redistribution check is a no-op pass", r["ok"] and not r["stale_components"])


def test_missing_data_redistribution_detects_stale():
    import copy
    cp = copy.deepcopy(FIXTURE["indices"]["crisisPressure"])
    cp["components"]["brentOptionsDread"]["health"] = "stale"
    # Simulate weights NOT being redistributed (a bug) - should be caught.
    r = ir.verify_missing_data_redistribution(cp["components"], ir.CRISIS_PRESSURE_WEIGHTS)
    check("stale component with un-redistributed weights is flagged", r["stale_components"] == ["brentOptionsDread"] and not r["ok"])

    # Now simulate the correct redistribution (pro-rata across remaining 30/25/25 -> ~40/33.3/26.7)
    remaining = {"aisTransitDeviation": 0.3, "insuranceMultiple": 0.25, "gdeltEventPressure": 0.25}
    total = sum(remaining.values())
    for name, w in remaining.items():
        cp["components"][name]["weight"] = w / total
    r = ir.verify_missing_data_redistribution(cp["components"], ir.CRISIS_PRESSURE_WEIGHTS)
    check("correctly pro-rata redistributed weights pass", r["ok"])


def test_smoothing_reconstruction_matches_when_chained():
    # Fabricate a "previous" poll 5 minutes earlier where smoothedValue was
    # slightly higher, then verify the fixture's own smoothedValue is
    # reproduced by the documented asymmetric-decay + rails formula.
    cp = FIXTURE["indices"]["crisisPressure"]
    recon = cp["reconciliation"]
    prev_recon = {"smoothedValue": recon["smoothedValue"] + 0.02}  # tiny drift, matches real decay pace
    r = ir.verify_smoothing_and_rails(prev_recon, recon)
    check(f"smoothing reconstruction: reconstructed={r['reconstructed_smoothed']} reported={r['reported_smoothed']} diff={r['diff']}", r["ok"])


def test_no_prior_snapshot_is_skip_not_failure():
    cp = FIXTURE["indices"]["crisisPressure"]
    r = ir.verify_smoothing_and_rails(None, cp["reconciliation"])
    check("no prior snapshot -> ok is None (skip), not False (failure)", r["ok"] is None)


def test_csv_row_fields_complete():
    all_checks = ir.validate_crisis_pressure(FIXTURE) + ir.validate_escalation_probability(FIXTURE)
    row = {
        "timestamp_utc": "x", "straits_as_of": FIXTURE["asOf"], "methodology_version": FIXTURE["methodologyVersion"],
    }
    row.update(ir._row_for_index("cp", FIXTURE["indices"]["crisisPressure"], ir.CRISIS_PRESSURE_WEIGHTS))
    row.update(ir._row_for_index("ep", FIXTURE["indices"]["escalationProbability"], ir.ESCALATION_WEIGHTS))
    row["hard_failures"] = "|".join(ir.hard_failures(all_checks))
    check("row keys exactly match CSV_FIELDS", set(row.keys()) == set(ir.CSV_FIELDS))


if __name__ == "__main__":
    test_weighted_sum()
    test_documented_weights_match()
    test_documented_weights_defer_to_redistribution_when_stale()
    test_band_boundaries()
    test_override_floor_holds()
    test_missing_data_redistribution_noop_when_all_fresh()
    test_missing_data_redistribution_detects_stale()
    test_smoothing_reconstruction_matches_when_chained()
    test_no_prior_snapshot_is_skip_not_failure()
    test_csv_row_fields_complete()
    print("\nAll tests passed.")
