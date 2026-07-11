# straits.live validation: sections 1, 3, and 5

`validate_straits_135.py` implements offline, reproducible audits for sections 1, 3, and 5 of issue #6. Its section-specific name leaves `validate_straits.py` available for a later shared orchestrator without colliding with work on sections 2, 4, and 6. It never downloads mutable data during an audit. Archive each upstream response first, then pass those artifacts to a subcommand and retain the JSON report beside them.

## 1. PortWatch parity

Export the IMF PortWatch `chokepoint6` history and the straits.live transit history as CSV, then run:

```powershell
python validate_straits_135.py portwatch --portwatch archive/portwatch.csv --straits archive/straits-transits.csv --previous-portwatch archive/portwatch-previous.csv --output reports/portwatch.json
```

The authoritative table is IMF PortWatch's public ArcGIS `Daily_Chokepoints_Data/FeatureServer/0`. Query it with `portid = 'chokepoint6'`, request all fields, order by date, and save the response as CSV before running the audit. Its count columns are `n_total`, `n_tanker`, `n_cargo`, `n_container`, `n_dry_bulk`, `n_general_cargo`, and `n_roro`.

Column aliases cover those ArcGIS names, straits API names, and the poller's normalized names. The report contains field coverage, exact per-date/per-type discrepancies, missing and duplicate dates, the independently calculated final-pre-crisis-year median, throughput discrepancies, and changes between two archived PortWatch releases.

Missing dates are reported but are not automatically hard failures because PortWatch may contain a longer history than a bounded straits export. Count mismatches, duplicate dates, and baseline/throughput mismatches return exit code 1.

## 3. AIS-gap audit

Create a labeled candidate CSV with these columns:

```text
mmsi,vessel_type,total_sightings,hours_missing,last_lat,last_lon,reappeared_at,other_provider_seen,port_arrival,corroborated_dark,feed_outage
```

Then run:

```powershell
python validate_straits_135.py ais-gaps --labels archive/ais-gap-labels.csv --output reports/ais-gaps.json
```

The report calls the metric â€œvessels satisfying the straits.live disappearance rule,â€ separates unresolved/feed-outage cases from resolved labels, calculates precision and false-positive rate, and runs a sensitivity grid over sighting and disappearance-window thresholds. Evidence and reviewer notes may be retained as additional CSV columns.

## 5. Market, risk, and editorial fields

Run all three checks together:

```powershell
python validate_straits_135.py fields --oil archive/straits-oil.csv --oil-reference archive/reference-oil.csv --risk archive/vessel-risk.csv --editorial archive/editorial-provenance.json --output reports/fields.json
```

Oil CSVs require `iso` (or `timestamp_utc`/`as_of`), `brent`, and `wti`. Values are paired with the nearest reference observation within 15 minutes by default and checked within $0.25; both limits are configurable.

The risk CSV requires `mmsi`, `imo`, `vessel_type`, `ofac_match`, `ais_dark`, and `published_risk`. The independent rules mirror methodology v0.4.0: OFAC, AIS-dark, flag-of-convenience tanker, and invalid IMO check-digit signals produce High/Moderate/Low bands. A mismatch is a hard failure.

The editorial provenance file is a JSON array. Every record must include:

```json
{
  "field": "verdict_status",
  "value": "closed",
  "classification": "editorial",
  "method_version": "0.4.0",
  "as_of": "2026-07-10T16:00:00Z",
  "source_name": "carrier advisories",
  "source_url": "https://example.com/source"
}
```

Allowed classifications are `observed`, `estimated`, `derived`, `forecast`, and `editorial`. This forces editorial judgments and rule outputs to remain distinguishable from direct observations and makes methodology changes auditable.

## Verification

```powershell
python -m unittest discover -s tests -v
```


