"""
Unit tests for escalation_forecast_scoring.py, using synthetic frozen
forecasts + resolved outcomes (real outcomes can't exist yet - they require
a 30-day window plus manual labeling, per the module's own docstring).

Run: python test_escalation_forecast_scoring.py
"""
import csv
import json
import os
import shutil
import tempfile

import escalation_forecast_scoring as efs


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        raise SystemExit(1)


def _fake_snapshot(as_of, ep_value, polymarket_score):
    return {
        "asOf": as_of,
        "methodologyVersion": "0.4.0",
        "indices": {
            "escalationProbability": {
                "value": ep_value, "band": "high", "confidence": 10,
                "components": {
                    "polymarketAggregate": {"score": polymarket_score, "weight": 0.4},
                    "brentTermStructureSlope": {"score": 40, "weight": 0.2},
                    "manifoldForwardContracts": {"score": 20, "weight": 0.25},
                    "kalshiForwardContracts": {"score": 60, "weight": 0.15},
                },
                "reconciliation": {"rawComposite": ep_value, "decayed": ep_value,
                                   "smoothedValue": ep_value, "anchor24h": ep_value,
                                   "hoursSincePrevious": 0.08},
            }
        }
    }


def with_temp_paths(fn):
    """Redirect FROZEN_PATH/RESOLVED_OUTCOMES_CSV to a scratch dir so tests
    don't touch the repo's real (append-only, source-of-truth) files."""
    def wrapper():
        tmp = tempfile.mkdtemp()
        orig_frozen, orig_resolved = efs.FROZEN_PATH, efs.RESOLVED_OUTCOMES_CSV
        efs.FROZEN_PATH = os.path.join(tmp, "frozen.jsonl")
        efs.RESOLVED_OUTCOMES_CSV = os.path.join(tmp, "resolved.csv")
        try:
            fn()
        finally:
            efs.FROZEN_PATH, efs.RESOLVED_OUTCOMES_CSV = orig_frozen, orig_resolved
            shutil.rmtree(tmp, ignore_errors=True)
    return wrapper


@with_temp_paths
def test_freeze_is_append_only_and_idempotent():
    snap = _fake_snapshot("2026-01-01T00:00:00.000Z", 61, 85)
    r1 = efs.freeze(snap)
    check("first freeze returns a record", r1 is not None)
    r2 = efs.freeze(snap)
    check("re-freezing the same asOf is a no-op", r2 is None)
    check("exactly one line written despite two freeze() calls", len(efs.load_frozen()) == 1)


@with_temp_paths
def test_record_outcome_rejects_duplicate():
    snap = _fake_snapshot("2026-01-01T00:00:00.000Z", 61, 85)
    efs.freeze(snap)
    efs.record_outcome("2026-01-01T00:00:00.000Z", "episode-1", "2026-01-31T00:00:00Z", 1, "test")
    try:
        efs.record_outcome("2026-01-01T00:00:00.000Z", "episode-1", "2026-01-31T00:00:00Z", 0, "dup")
        check("duplicate outcome recording raises", False)
    except ValueError:
        check("duplicate outcome recording raises ValueError", True)


@with_temp_paths
def test_scoring_perfect_forecaster():
    # Forecaster who always assigns 99 when outcome=1 and 1 when outcome=0.
    for i, outcome in enumerate([1, 0, 1, 0, 1]):
        as_of = f"2026-01-0{i+1}T00:00:00.000Z"
        prob_pct = 99 if outcome else 1
        efs.freeze(_fake_snapshot(as_of, prob_pct, prob_pct))
        efs.record_outcome(as_of, f"episode-{i}", "2026-02-01T00:00:00Z", outcome)

    report = efs.score_escalation_forecasts()
    check(f"n_forecast_rows == 5: got {report['n_forecast_rows']}", report["n_forecast_rows"] == 5)
    check(f"n_distinct_episodes == 5: got {report['n_distinct_episodes']}", report["n_distinct_episodes"] == 5)
    check(f"near-perfect forecaster has low Brier score: got {report['brier_score']:.4f}",
          report["brier_score"] < 0.02)
    check("baseline_climatology base_rate == 0.6 (3 of 5 outcomes=1)",
          abs(report["baseline_climatology"]["base_rate"] - 0.6) < 1e-9)
    check("near-perfect forecaster beats climatology baseline",
          report["brier_score"] < report["baseline_climatology"]["brier"])


@with_temp_paths
def test_scoring_uninformative_forecaster():
    # Forecaster who always says 50/50 regardless of outcome.
    for i, outcome in enumerate([1, 0, 1, 0]):
        as_of = f"2026-01-0{i+1}T00:00:00.000Z"
        efs.freeze(_fake_snapshot(as_of, 50, 50))
        efs.record_outcome(as_of, f"episode-{i}", "2026-02-01T00:00:00Z", outcome)

    report = efs.score_escalation_forecasts()
    check(f"50/50 forecaster has Brier score == 0.25: got {report['brier_score']}",
          abs(report["brier_score"] - 0.25) < 1e-9)
    check(f"50/50 forecaster has sharpness == 0: got {report['sharpness']}",
          abs(report["sharpness"]) < 1e-9)


@with_temp_paths
def test_incremental_value_over_polymarket():
    # Composite (value) tracks outcome perfectly; polymarket-only component does not.
    as_of1 = "2026-01-01T00:00:00.000Z"
    as_of2 = "2026-01-02T00:00:00.000Z"
    efs.freeze(_fake_snapshot(as_of1, 90, 50))  # composite right, polymarket-only unsure
    efs.freeze(_fake_snapshot(as_of2, 10, 50))
    efs.record_outcome(as_of1, "ep-1", "2026-02-01T00:00:00Z", 1)
    efs.record_outcome(as_of2, "ep-2", "2026-02-02T00:00:00Z", 0)

    report = efs.score_escalation_forecasts()
    iv = report["incremental_value_over_polymarket"]
    check(f"composite Brier ({iv['composite_brier']:.4f}) beats polymarket-only ({iv['polymarket_only_brier']:.4f})",
          iv["composite_brier"] < iv["polymarket_only_brier"])


@with_temp_paths
def test_unresolved_due_queue():
    import datetime
    old_as_of = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=40)).isoformat().replace("+00:00", "Z")
    recent_as_of = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    efs.freeze(_fake_snapshot(old_as_of, 70, 70))
    efs.freeze(_fake_snapshot(recent_as_of, 30, 30))

    due = efs.unresolved_due()
    check(f"only the 40-day-old forecast is due (1 expected, got {len(due)})", len(due) == 1)
    check("the due forecast is the old one", due[0]["as_of"] == old_as_of)


if __name__ == "__main__":
    test_freeze_is_append_only_and_idempotent()
    test_record_outcome_rejects_duplicate()
    test_scoring_perfect_forecaster()
    test_scoring_uninformative_forecaster()
    test_incremental_value_over_polymarket()
    test_unresolved_due_queue()
    print("\nAll tests passed.")
