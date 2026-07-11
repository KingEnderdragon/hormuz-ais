# GFW–UANI cross-reference reproducibility

`gfw_uani_reproduce.py` replaces the unreproducible summary-only workflow behind `gfw_uani_matches.json`. It performs no live requests during a run. Instead, it consumes and hashes three archived artifacts so the same join can be repeated after either upstream changes.

## Input contract

1. **UANI corpus** (`.json` or `.jsonl`): one record per archived article with `url`, `title`, full `text` or `body`, and `fetched_at`. Full article text is required; `uani_monitor.py`'s 1,500-character excerpt is not a sufficient research archive.
2. **GFW export** (`.json` or `.jsonl`): raw records from the declared Global Fishing Watch query. Common flat and nested aliases are accepted for IMO, MMSI, vessel ID, name, flag, type, date, and presence hours.
3. **Query manifest** (`.json`): records `gfw_dataset`, `gfw_dataset_version`, exact `gfw_query_url`, pagination method/page size, `bbox` as `[west, south, east, north]`, `start_date`, `end_date`, `uani_hub_url`, `uani_corpus_version`, and `exported_at`. Authentication secrets must not be included.

The large raw GFW response may remain outside Git, but its SHA-256 digest in the report makes the exact input identifiable. Store the raw artifact in durable object storage using its digest as part of the key.

## Deterministic method

- Extract only seven-digit identifiers explicitly introduced by `IMO`, `IMO number`, or `IMO no.`.
- Reject identifiers that fail the IMO check-digit algorithm.
- Normalize GFW identifiers and reject records with missing or invalid IMO values.
- Remove exact duplicate normalized GFW records before aggregation, protecting against repeated cursor/page-boundary records.
- Join the two sources exclusively on valid IMO, the immutable hull identifier. Vessel names are retained as observed history but never used as the join key.
- Aggregate presence hours, distinct presence dates, names, and source-record counts per IMO.
- Write the final report and both normalized intermediate tables.

## Run

```powershell
python gfw_uani_reproduce.py `
  --uani-corpus archive/uani_articles.jsonl `
  --gfw-export archive/gfw_presence.json `
  --query-manifest archive/gfw_uani_query.json `
  --output-dir reports/gfw_uani
```

Outputs:

- `gfw_uani_report.json` — source hashes, query metadata, intermediate counts, rejected UANI identifiers, and final matches.
- `normalized_uani_imos.csv` — every valid article/IMO mention used by the join.
- `normalized_gfw_vessels.csv` — every valid GFW row used by the join.

## Limitations

The pipeline proves that a declared pair of archived inputs produces a declared match. It does not prove that the UANI hub crawl was complete or that GFW observed every vessel. Completeness must be assessed from crawl logs, pagination totals, GFW response metadata, and the archived source inventory. A match means the same valid IMO appears in both sources; it does not independently validate either source's allegation or vessel classification.

## Verification

```powershell
python -m unittest -v test_gfw_uani_reproduce.py
```
