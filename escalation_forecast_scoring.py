"""
Forecast-freezing and prospective scoring for straits.live's Escalation
Probability index. GitHub issue #6 section 4:

    "Define the forecast target, horizon, resolution rule, and treatment of
    overlapping episodes before evaluation. Freeze forecasts prospectively,
    then calculate Brier score, log loss, calibration, reliability, sharpness,
    performance against simple baselines, and incremental value over the
    underlying prediction-market input. Claims of predictive validity should
    reflect the small number of statistically independent crisis episodes."

--- Forecast target (from straits.live/methodology#hormuz-index v0.4.0) ---
Union of, within the horizon:
  - Closure or partial closure of the Strait of Hormuz
  - Major strike on Gulf infrastructure
  - US military casualty
  - Iranian retaliation
  - Brent crude above $150/bbl

--- Horizon ---
30 days from the forecast's `asOf` timestamp (matches straits.live's own
"30-Day Escalation Forecast" framing).

--- Resolution rule ---
A frozen forecast resolves YES if ANY of the five conditions above is
observed to occur within its 30-day window; otherwise NO at window close.
Resolution is a manual judgment call recorded in resolved_outcomes.csv - it
is not automatable from any single feed we currently ingest, and the issue
is explicit that outcome counts will be small, so no attempt is made here to
synthesize resolutions from AIS/GDELT/price data.

--- Overlapping episodes ---
Forecasts frozen every 5 minutes during a single ongoing crisis are NOT
independent draws - they mostly share the same eventual resolution. Scoring
functions below operate on whatever rows resolved_outcomes.csv contains, and
each row carries an `episode_id` that must be assigned by whoever resolves
outcomes (grouping frozen forecasts that concern the same underlying crisis
episode). Aggregate metrics computed across raw forecast rows without
collapsing to one observation per episode overstate the sample size --
score_escalation_forecasts() reports both the raw-row count and the distinct
episode count so this isn't silently hidden.
"""
import csv
import json
import math
import os
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(__file__)
FROZEN_PATH = os.path.join(HERE, "escalation_forecasts_frozen.jsonl")
RESOLVED_OUTCOMES_CSV = os.path.join(HERE, "resolved_outcomes.csv")

HORIZON_DAYS = 30
FORECAST_TARGET = (
    "closure_or_partial_closure_of_hormuz OR major_strike_on_gulf_infrastructure "
    "OR us_military_casualty OR iranian_retaliation OR brent_above_150usd"
)

RESOLVED_OUTCOMES_FIELDS = ["forecast_id", "episode_id", "resolved_at", "outcome", "notes"]


# --- Freezing (append-only) ---

def _forecast_id(as_of):
    return as_of  # asOf is already a unique, sortable ISO-8601 timestamp per poll

def _existing_ids():
    if not os.path.exists(FROZEN_PATH):
        return set()
    ids = set()
    with open(FROZEN_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                ids.add(json.loads(line)["forecast_id"])
    return ids


def freeze(index_snapshot):
    """Append-only: idempotent no-op if this asOf was already frozen. Never
    rewrites or deletes existing lines - a frozen forecast must stay exactly
    as published so later scoring reflects what was actually knowable at the
    time, not a revised value."""
    ep = index_snapshot["indices"]["escalationProbability"]
    as_of = index_snapshot["asOf"]
    forecast_id = _forecast_id(as_of)
    if forecast_id in _existing_ids():
        return None

    frozen_at = datetime.now(timezone.utc)
    as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    record = {
        "forecast_id": forecast_id,
        "frozen_at": frozen_at.isoformat(),
        "as_of": as_of,
        "methodology_version": index_snapshot.get("methodologyVersion"),
        "forecast_target": FORECAST_TARGET,
        "horizon_days": HORIZON_DAYS,
        "resolution_due_at": (as_of_dt + timedelta(days=HORIZON_DAYS)).isoformat(),
        "value": ep["value"],
        "band": ep["band"],
        "confidence": ep["confidence"],
        "components": ep["components"],
        "reconciliation": ep["reconciliation"],
    }
    with open(FROZEN_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def load_frozen():
    if not os.path.exists(FROZEN_PATH):
        return []
    with open(FROZEN_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def unresolved_due(frozen=None, as_of=None):
    """Forecasts whose 30-day window has closed but that have no matching row
    in resolved_outcomes.csv yet - the analyst's work queue."""
    frozen = frozen if frozen is not None else load_frozen()
    now = as_of or datetime.now(timezone.utc)
    resolved_ids = {r["forecast_id"] for r in load_resolved_outcomes()}
    due = []
    for r in frozen:
        due_at = datetime.fromisoformat(r["resolution_due_at"])
        if due_at <= now and r["forecast_id"] not in resolved_ids:
            due.append(r)
    return due


# --- Resolved outcomes (manually labeled) ---

def ensure_resolved_outcomes_csv():
    if not os.path.exists(RESOLVED_OUTCOMES_CSV):
        with open(RESOLVED_OUTCOMES_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=RESOLVED_OUTCOMES_FIELDS).writeheader()


def load_resolved_outcomes():
    if not os.path.exists(RESOLVED_OUTCOMES_CSV):
        return []
    with open(RESOLVED_OUTCOMES_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def record_outcome(forecast_id, episode_id, resolved_at, outcome, notes=""):
    """outcome must be 0 or 1 - whether ANY of the five FORECAST_TARGET
    conditions was observed within the forecast's window. This is a human
    judgment call against news/JWC/EIA/AIS evidence, not derivable from a
    single feed - see module docstring."""
    ensure_resolved_outcomes_csv()
    if outcome not in (0, 1, "0", "1"):
        raise ValueError(f"outcome must be 0 or 1, got {outcome!r}")
    existing = {r["forecast_id"] for r in load_resolved_outcomes()}
    if forecast_id in existing:
        raise ValueError(f"forecast_id {forecast_id} already has a recorded outcome; "
                         f"resolved_outcomes.csv is treated as append-only to avoid "
                         f"silently overwriting a prior judgment call")
    with open(RESOLVED_OUTCOMES_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=RESOLVED_OUTCOMES_FIELDS).writerow({
            "forecast_id": forecast_id, "episode_id": episode_id,
            "resolved_at": resolved_at, "outcome": int(outcome), "notes": notes,
        })


# --- Scoring ---

def _joined_rows():
    frozen_by_id = {r["forecast_id"]: r for r in load_frozen()}
    rows = []
    for outcome_row in load_resolved_outcomes():
        fid = outcome_row["forecast_id"]
        if fid not in frozen_by_id:
            continue
        rows.append({
            "forecast_id": fid,
            "episode_id": outcome_row["episode_id"],
            "prob": frozen_by_id[fid]["value"] / 100.0,
            "polymarket_only_prob": frozen_by_id[fid]["components"]["polymarketAggregate"]["score"] / 100.0,
            "outcome": int(outcome_row["outcome"]),
        })
    return rows


def brier_score(rows):
    if not rows:
        return None
    return sum((r["prob"] - r["outcome"]) ** 2 for r in rows) / len(rows)


def log_loss(rows, eps=1e-9):
    if not rows:
        return None
    total = 0.0
    for r in rows:
        p = min(max(r["prob"], eps), 1 - eps)
        total += -(r["outcome"] * math.log(p) + (1 - r["outcome"]) * math.log(1 - p))
    return total / len(rows)


def calibration_bins(rows, n_bins=5):
    """Reliability table: for forecasts grouped into probability bins, compare
    mean forecast probability to observed outcome frequency."""
    if not rows:
        return []
    bins = [[] for _ in range(n_bins)]
    for r in rows:
        idx = min(int(r["prob"] * n_bins), n_bins - 1)
        bins[idx].append(r)
    table = []
    for i, b in enumerate(bins):
        if not b:
            continue
        table.append({
            "bin_range": (i / n_bins, (i + 1) / n_bins),
            "n": len(b),
            "mean_forecast_prob": sum(r["prob"] for r in b) / len(b),
            "observed_frequency": sum(r["outcome"] for r in b) / len(b),
        })
    return table


def sharpness(rows):
    """Mean absolute distance from 0.5 - how far forecasts commit from the
    uninformative midpoint, independent of whether they're right."""
    if not rows:
        return None
    return sum(abs(r["prob"] - 0.5) for r in rows) / len(rows)


def baseline_climatology(rows):
    """Constant-probability baseline: the historical base rate of the
    outcome across all resolved episodes (one number, no skill)."""
    if not rows:
        return None
    base_rate = sum(r["outcome"] for r in rows) / len(rows)
    baseline_rows = [{"prob": base_rate, "outcome": r["outcome"]} for r in rows]
    return {"base_rate": base_rate, "brier": brier_score(baseline_rows)}


def incremental_value_over_polymarket(rows):
    """Compares the full composite's Brier score against using the
    Polymarket-only component as the forecast, to check whether the other
    three inputs (Brent-WTI spread, Manifold, Kalshi) add anything."""
    if not rows:
        return None
    full = brier_score(rows)
    polymarket_only = brier_score([{**r, "prob": r["polymarket_only_prob"]} for r in rows])
    return {"composite_brier": full, "polymarket_only_brier": polymarket_only,
            "composite_minus_polymarket": full - polymarket_only}


def score_escalation_forecasts():
    rows = _joined_rows()
    episode_ids = {r["episode_id"] for r in rows}
    report = {
        "n_forecast_rows": len(rows),
        "n_distinct_episodes": len(episode_ids),
        "caveat": (
            "n_forecast_rows counts every 5-min-frozen forecast with a resolved "
            "outcome; n_distinct_episodes counts underlying crisis episodes "
            "(overlapping forecasts within one episode are not independent). "
            "Predictive-validity claims should cite n_distinct_episodes, not "
            "n_forecast_rows."
        ),
        "brier_score": brier_score(rows),
        "log_loss": log_loss(rows),
        "sharpness": sharpness(rows),
        "calibration_bins": calibration_bins(rows),
        "baseline_climatology": baseline_climatology(rows),
        "incremental_value_over_polymarket": incremental_value_over_polymarket(rows),
    }
    return report


if __name__ == "__main__":
    ensure_resolved_outcomes_csv()
    due = unresolved_due()
    if due:
        print(f"{len(due)} frozen forecast(s) past their 30-day resolution window "
              f"with no recorded outcome yet - see resolved_outcomes.csv / record_outcome().")
        for r in due[:5]:
            print(f"  {r['forecast_id']} (value={r['value']}, due={r['resolution_due_at']})")
    print(json.dumps(score_escalation_forecasts(), indent=2))
