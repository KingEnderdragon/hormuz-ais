# Composite-index methodology (Crisis Pressure & Escalation Probability)

Addresses GitHub issue [#6](https://github.com/KingEnderdragon/hormuz-ais/issues/6),
section 4, "Composite-index reproducibility."

Source: [straits.live/methodology#hormuz-index](https://straits.live/methodology#hormuz-index),
methodologyVersion `0.4.0`, extracted 2026-07-11. straits.live also exposes a
machine-readable audit endpoint at `https://straits.live/api/index` that
publishes per-component raw/score/weight/contribution and a reconciliation
block (`rawComposite`, `decayed`, `smoothedValue`, `anchor24h`,
`hoursSincePrevious`) for both indices every 5 minutes. **This document is the
human-readable spec; [`index_reconciliation.py`](../index_reconciliation.py)
is the executable version of it, checked against that audit endpoint on every
poll** — if straits.live changes a weight, band boundary, or decay constant,
the corresponding check in that module fails loudly rather than this document
silently going stale.

## Crisis Pressure ("what is happening right now")

Recomputed every 5 minutes.

| Component | Weight | Source | Normalization |
|---|---|---|---|
| `aisTransitDeviation` (Physical Reality) | 30% | IMF PortWatch daily transit count deviation from pre-crisis baseline + proprietary AIS aggregation | raw deviation → 0-100 score (exact curve undocumented by straits.live; reproduced empirically from paired raw/score values in `/api/index`, see below) |
| `insuranceMultiple` (Insurance) | 25% | Trade press + proprietary aggregation | war-risk multiple vs. peace baseline for VLCC voyages |
| `gdeltEventPressure` (Event Pressure) | 25% | GDELT 2.0 events database | Goldstein-scale-weighted event count, rolling 72h window, exponential decay (72h half-life) |
| `brentOptionsDread` (Oil-Market Dread) | 20% | CBOE OVX via FRED | 30-day at-the-money implied volatility on Brent options |

**Weighted sum:** `rawComposite = Σ(score_i × weight_i)`. Verified in
[`verify_weighted_sum`](../index_reconciliation.py) — matched the live API to
within 0.01 on every poll observed so far.

**Override rule:** when any single component scores ≥ 75, the published value
is floored at `0.9 × that component's score`, "so a high-severity signal
cannot be averaged away." Verified in `verify_override_floor` as a
per-poll invariant (`published_value >= floor`), not only at the moment the
rule triggers.

**Missing-data redistribution:** when a component's feed is stale/unavailable,
its weight is redistributed pro-rata across the remaining fresh components.
Example given by straits.live: CBOE OVX dropout → remaining three inputs
scale from 30/25/25 to ≈37/31/31. Verified in
`verify_missing_data_redistribution`. The nominal documented-weight check
applies only while all components are fresh; during redistribution it becomes
advisory so the two checks cannot impose contradictory hard requirements.

## Escalation Probability ("what markets price for a bad outcome in 30 days")

Recomputed every 5 minutes.

**Forecast target** (union of, within 30 days): closure/partial closure of
Hormuz; major strike on Gulf infrastructure; US military casualty; Iranian
retaliation; Brent above $150.

| Component | Weight | Source | Method |
|---|---|---|---|
| `polymarketAggregate` | 40% | Polymarket Gamma API | dollar-weighted mean implied probability across matching contracts |
| `brentTermStructureSlope` | 20% | Intraday futures feed (front-month) | Brent-WTI spread, linear interpolation: $0→0, $2→15, $4→35, $7→70, $10→90, $15+→100 (negative clamps to 0) |
| `manifoldForwardContracts` | 25% | Manifold Markets public API | mana-weighted, converted at 100:1 USD-equivalent |
| `kalshiForwardContracts` | 15% | Kalshi trade API v2 | dollar-weighted mean, dominated by `KXHORMUZNORM` (resolves on IMF PortWatch 7-day MA reaching 60 transit calls) |

**Orientation inversion:** contracts framing YES as de-escalation contribute
`1 - P(YES)` so the aggregate always reads as "probability of a bad outcome."

**Liquidity floor:** below $15,000 combined 7-day dollar volume across
Polymarket+Kalshi+Manifold, the index is marked low-confidence rather than
having its weights silently collapse.

**No override rule** for Escalation Probability — "a forecast should be a
smooth probabilistic number, not a threshold flipper."

## Shared smoothing & rails (both indices)

1. **Asymmetric EWMA:** rises propagate instantly; falls decay toward the raw
   composite with a 36-hour half-life. `reconstruct_smoothed()` in
   `index_reconciliation.py` implements this exactly and reproduces the
   published `smoothedValue` to within ~0.02 points against consecutive live
   polls (see `test_index_reconciliation.py`).
2. **24-hour rails:** the smoothed value is clamped to
   `[anchor24h - 10, anchor24h + 25]` — max −10 fall or +25 rise relative to
   the value 24 hours ago.
3. **Band boundaries** (shared): 0–19 Calm, 20–39 Watchful, 40–59 Elevated,
   60–79 High, 80–100 Extreme. Verified in `verify_band` / `band_for`.
4. **Confidence/dispersion figure:** documented only as "weighted standard
   deviation of component scores, scaled and clamped to 2–15" — the scale
   factor from raw stdev to the published figure is not given. Our
   `verify_dispersion` check is therefore **advisory** (`ok=None`), not a hard
   failure; it logs the raw weighted stdev alongside the reported confidence
   for future recalibration if straits.live ever documents the scale factor.

## What is *not* independently reproducible from the public API alone

- The exact raw→score normalization curve for `aisTransitDeviation` and
  `gdeltEventPressure` (we treat straits.live's own reported `score` per
  component as ground truth and verify everything downstream of it, rather
  than re-deriving `score` from `raw` — re-deriving that curve would need a
  longer paired raw/score history than one snapshot provides).
- The confidence/dispersion scale factor (see above).
- Anything upstream of `/api/index` itself (e.g., whether straits.live's GDELT
  query or PortWatch pull is correct) — that is IMF PortWatch parity (issue
  #6 §1) and the AIS-gap audit (§3), out of scope for this document.

## Escalation Probability prospective scoring

See [`escalation_forecast_scoring.py`](../escalation_forecast_scoring.py).
Forecasts are frozen append-only (`escalation_forecasts_frozen.jsonl`) as
they're published, each tagged with its own 30-day resolution due-date and
the forecast-target definition above baked in at freeze time (not
reconstructed later from a possibly-changed methodology version).

Resolution is a **manual judgment call** recorded in `resolved_outcomes.csv`
once a forecast's window closes — whether ANY of the five target conditions
occurred is not mechanically derivable from any single feed this repo
ingests. `python escalation_forecast_scoring.py` lists forecasts whose window
has closed with no recorded outcome yet.

Scoring (`score_escalation_forecasts()`) reports Brier score, log loss,
calibration bins, sharpness, a climatology baseline, and incremental value of
the full composite over the Polymarket-only component — but also reports
`n_distinct_episodes` alongside the raw forecast-row count, because forecasts
frozen every 5 minutes during one ongoing crisis are not independent draws.
**Any claim of predictive validity should cite `n_distinct_episodes`**, which
given real-world crisis frequency will likely stay in the single digits for a
long time — this is a known, permanent limitation of scoring a 30-day
geopolitical forecast, not a bug to fix.
